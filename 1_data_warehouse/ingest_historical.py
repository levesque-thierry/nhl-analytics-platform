"""
ingest_historical.py — NHL Player Game Log Ingestion

Fetches historical game logs from the NHL Web API and upserts them into
the player_game_logs table. Maps API payload keys to database columns
per the api_map.md reference.

CLI Usage:
    python ingest_historical.py --player-ids 8478402,8475789 --seasons 20252026
    python ingest_historical.py --player-ids 8478402 --seasons 20232024 --skip-existing
    python ingest_historical.py --player-ids 8478402 --seasons 20252026 --game-type 3
"""

import argparse
import logging
import re
import sqlite3
import time

import requests
from pydantic import BaseModel

from database_setup import DB_PATH, initialize_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

NHL_API_BASE: str = "https://api-web.nhle.com"
RATE_LIMIT_SECONDS: float = 0.5

GAME_TYPE_LABELS: dict[int, str] = {2: "Regular Season", 3: "Playoffs"}

SEASON_PATTERN: re.Pattern[str] = re.compile(r"^\d{8}$")


# ---------------------------------------------------------------------------
# Pydantic validation model for raw API game log entries
# ---------------------------------------------------------------------------

class GameLogEntry(BaseModel):
    """Validates a single game log record from the NHL API response."""
    gameId: int
    gameDate: str
    teamAbbrev: str
    opponentAbbrev: str = ""
    homeRoadFlag: str = ""
    goals: int = 0
    assists: int = 0
    points: int = 0
    shots: int = 0
    pim: int = 0
    plusMinus: int = 0
    toi: str = ""
    shifts: int = 0
    powerPlayGoals: int = 0
    powerPlayPoints: int = 0
    shorthandedGoals: int = 0
    gameWinningGoals: int = 0
    otGoals: int = 0


# ---------------------------------------------------------------------------
# Season format validation
# ---------------------------------------------------------------------------

def validate_season(season: str) -> bool:
    """Return True if season is a valid 8-digit YYYYYYYY string."""
    return bool(SEASON_PATTERN.match(season))


# ---------------------------------------------------------------------------
# API fetch + normalization
# ---------------------------------------------------------------------------

def fetch_game_log(player_id: int, season: str, game_type: int) -> list[dict[str, object]]:
    """
    Fetch game logs for a single player, season, and game type from the NHL Web API.
    Returns a list of normalized dicts ready for database insertion.
    """
    url = f"{NHL_API_BASE}/v1/player/{player_id}/game-log/{season}/{game_type}"
    logger.info("Fetching game log: %s", url)

    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("API request failed for player %d, season %s: %s", player_id, season, exc)
        return []

    data = response.json()
    raw_entries: list[dict] = data.get("gameLog", [])

    if not raw_entries:
        logger.warning("No game log entries found for player %d, season %s, game_type %d.", player_id, season, game_type)
        return []

    normalized: list[dict[str, object]] = []
    for raw in raw_entries:
        try:
            entry = GameLogEntry(**raw)
        except Exception as exc:
            logger.warning("Skipping invalid entry for player %d: %s", player_id, exc)
            continue

        normalized.append({
            "game_id": entry.gameId,
            "player_id": player_id,
            "season": season,
            "game_type": game_type,
            "game_date": entry.gameDate,
            "team_abbr": entry.teamAbbrev,
            "opponent_abbr": entry.opponentAbbrev,
            "home_road_flag": entry.homeRoadFlag,
            "goals": entry.goals,
            "assists": entry.assists,
            "points": entry.points,
            "shots": entry.shots,
            "pim": entry.pim,
            "plus_minus": entry.plusMinus,
            "time_on_ice": entry.toi,
            "shifts": entry.shifts,
            "power_play_goals": entry.powerPlayGoals,
            "power_play_points": entry.powerPlayPoints,
            "shorthanded_goals": entry.shorthandedGoals,
            "game_winning_goals": entry.gameWinningGoals,
            "ot_goals": entry.otGoals,
        })

    logger.info("Player %d, season %s, game_type %d: %d valid entries.", player_id, season, game_type, len(normalized))
    return normalized


# ---------------------------------------------------------------------------
# Skip-existing check
# ---------------------------------------------------------------------------

def has_existing_data(conn: sqlite3.Connection, player_id: int, season: str, game_type: int) -> bool:
    """Check if game log data already exists for a player+season+game_type."""
    cursor = conn.execute(
        "SELECT COUNT(*) FROM player_game_logs WHERE player_id = ? AND season = ? AND game_type = ?",
        (player_id, season, game_type),
    )
    count = cursor.fetchone()[0]
    return count > 0


# ---------------------------------------------------------------------------
# Database UPSERT
# ---------------------------------------------------------------------------

UPSERT_SQL: str = """
    INSERT OR REPLACE INTO player_game_logs
        (game_id, player_id, season, game_type, game_date, team_abbr, opponent_abbr,
         home_road_flag, goals, assists, points, shots, pim, plus_minus,
         time_on_ice, shifts, power_play_goals, power_play_points,
         shorthanded_goals, game_winning_goals, ot_goals)
    VALUES
        (:game_id, :player_id, :season, :game_type, :game_date, :team_abbr, :opponent_abbr,
         :home_road_flag, :goals, :assists, :points, :shots, :pim, :plus_minus,
         :time_on_ice, :shifts, :power_play_goals, :power_play_points,
         :shorthanded_goals, :game_winning_goals, :ot_goals)
"""


def upsert_game_logs(conn: sqlite3.Connection, logs: list[dict[str, object]]) -> int:
    """
    Insert or replace game log records into the player_game_logs table.
    Returns the number of rows affected.
    """
    if not logs:
        return 0

    try:
        cursor = conn.executemany(UPSERT_SQL, logs)
        count = cursor.rowcount
        logger.info("Upserted %d game log records.", count)
        return count
    except sqlite3.Error as exc:
        logger.error("Database upsert failed: %s", exc)
        conn.rollback()
        return 0


# ---------------------------------------------------------------------------
# Main ingestion driver
# ---------------------------------------------------------------------------

def ingest_seasons(
    player_ids: list[int],
    seasons: list[str],
    game_type: int = 2,
    skip_existing: bool = False,
    db_path: str = DB_PATH,
) -> None:
    """
    Main ingestion driver: fetches game logs for each player × season
    combination and upserts into the database. Commits once per batch
    for performance.
    """
    total_rows: int = 0
    skipped: int = 0
    gt_label = GAME_TYPE_LABELS.get(game_type, str(game_type))

    with initialize_database(db_path) as conn:
        for player_id in player_ids:
            for season in seasons:
                if not validate_season(season):
                    logger.error("Invalid season format '%s' — must be 8 digits (YYYYYYYY). Skipping.", season)
                    continue

                if skip_existing and has_existing_data(conn, player_id, season, game_type):
                    logger.info("Skipping player %d, season %s, game_type %d (data exists).", player_id, season, game_type)
                    skipped += 1
                    continue

                logs = fetch_game_log(player_id, season, game_type)
                if logs:
                    upsert_game_logs(conn, logs)
                    total_rows += len(logs)
                time.sleep(RATE_LIMIT_SECONDS)

        conn.commit()

    logger.info(
        "Historical ingestion complete. %d total rows ingested, %d skipped (already existed). "
        "Game type: %s (%d).",
        total_rows,
        skipped,
        gt_label,
        game_type,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for historical game log ingestion."""
    parser = argparse.ArgumentParser(
        description="Ingest NHL player game logs into the data warehouse."
    )
    parser.add_argument(
        "--player-ids",
        type=str,
        required=True,
        help="Comma-separated NHL player IDs (e.g., 8478402,8475789).",
    )
    parser.add_argument(
        "--seasons",
        type=str,
        default="20252026",
        help="Comma-separated seasons in YYYYYYYY format (default: 20252026).",
    )
    parser.add_argument(
        "--game-type",
        type=int,
        default=2,
        choices=[2, 3],
        help="Game type: 2=Regular Season (default), 3=Playoffs.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip API call if data already exists for player+season+game_type.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    player_ids = [int(pid.strip()) for pid in args.player_ids.split(",")]
    seasons = [s.strip() for s in args.seasons.split(",")]

    logger.info(
        "Starting ingestion: %d players, %d season(s), game_type=%d (%s), skip_existing=%s.",
        len(player_ids),
        len(seasons),
        args.game_type,
        GAME_TYPE_LABELS.get(args.game_type, "unknown"),
        args.skip_existing,
    )
    ingest_seasons(
        player_ids=player_ids,
        seasons=seasons,
        game_type=args.game_type,
        skip_existing=args.skip_existing,
    )

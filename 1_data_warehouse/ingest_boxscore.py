"""
ingest_boxscore.py — NHL Game Data Ingestion via Boxscore Endpoint

Efficiently ingests per-game player statistics by fetching boxscore data
for each game in a season. ~1,300 API calls vs ~13,000 with per-player approach.

Pipeline:
    1. Fetch rosters for each team → populate players + player_team_seasons
    2. Fetch schedule per team → collect unique game IDs (deduplicated)
    3. Call boxscore once per game → extract all player stats for both teams
    4. Upsert into player_game_logs

CLI Usage:
    python ingest_boxscore.py --season 20252026
    python ingest_boxscore.py --season 20252026 --game-type 2
    python ingest_boxscore.py --season 20252026 --teams EDM,TOR --skip-existing
    python ingest_boxscore.py --season 20252026 --teams EDM --game-type 3
"""

import argparse
import logging
import sqlite3
import time

import requests

from database_setup import DB_PATH, initialize_database
from ingest_roster import ALL_NHL_TEAMS, fetch_roster, upsert_players

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

NHL_API_BASE: str = "https://api-web.nhle.com/v1"
RATE_LIMIT_SECONDS: float = 0.5

GAME_TYPE_LABELS: dict[int, str] = {1: "Preseason", 2: "Regular Season", 3: "Playoffs"}


# ---------------------------------------------------------------------------
# Schedule fetching
# ---------------------------------------------------------------------------

def fetch_team_schedule(team_abbrev: str, season: str) -> list[dict[str, object]]:
    """Fetch all games for a team's season from the schedule endpoint."""
    url = f"{NHL_API_BASE}/club-schedule-season/{team_abbrev}/{season}"
    logger.info("Fetching schedule: %s", url)

    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch schedule for %s: %s", team_abbrev, exc)
        return []

    data = response.json()
    games = data.get("games", [])
    logger.info("Team %s schedule: %d games total.", team_abbrev, len(games))
    return games


def collect_game_ids(
    teams: list[str],
    season: str,
    game_type: int | None = None,
) -> list[dict[str, object]]:
    """
    Fetch schedules for all teams and collect deduplicated game entries.
    Each game entry contains id, gameType, awayTeam, homeTeam, gameDate, etc.
    """
    seen_ids: set[int] = set()
    unique_games: list[dict[str, object]] = []

    for team in teams:
        games = fetch_team_schedule(team, season)
        time.sleep(RATE_LIMIT_SECONDS)

        for game in games:
            game_id = game.get("id")
            if game_id in seen_ids:
                continue

            gtype = game.get("gameType")
            if game_type is not None and gtype != game_type:
                continue

            seen_ids.add(game_id)
            unique_games.append(game)

    logger.info("Collected %d unique game IDs across %d teams.", len(unique_games), len(teams))
    return unique_games


# ---------------------------------------------------------------------------
# Boxscore fetching + normalization
# ---------------------------------------------------------------------------

def fetch_boxscore(game_id: int) -> dict | None:
    """Fetch the boxscore for a single game."""
    url = f"{NHL_API_BASE}/gamecenter/{game_id}/boxscore"

    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch boxscore for game %d: %s", game_id, exc)
        return None

    return response.json()


def _safe_int(value: object, default: int = 0) -> int:
    """Safely convert a value to int, returning default on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _safe_float(value: object) -> float | None:
    """Safely convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        fval = float(value)
        return fval if fval != 0.0 else None
    except (ValueError, TypeError):
        return None


def _extract_player_name(name_obj: object) -> str:
    """Extract player name from API response (handles dict with 'default' key)."""
    if isinstance(name_obj, dict):
        return name_obj.get("default", "")
    if isinstance(name_obj, str):
        return name_obj
    return ""


def normalize_boxscore(data: dict) -> list[dict[str, object]]:
    """
    Normalize a boxscore response into a list of player_game_logs rows.
    Handles both skaters and goalies, home and away teams.
    """
    game_id = data.get("id", 0)
    season = str(data.get("season", ""))
    game_type = data.get("gameType", 2)
    game_date = data.get("gameDate", "")
    away_abbrev = data.get("awayTeam", {}).get("abbrev", "")
    home_abbrev = data.get("homeTeam", {}).get("abbrev", "")

    rows: list[dict[str, object]] = []
    pbgs = data.get("playerByGameStats", {})

    for side in ("awayTeam", "homeTeam"):
        if side not in pbgs:
            continue

        team_abbrev = home_abbrev if side == "homeTeam" else away_abbrev
        opponent_abbrev = away_abbrev if side == "homeTeam" else home_abbrev
        home_road = "H" if side == "homeTeam" else "R"
        team_data = pbgs[side]

        for pos_group in ("forwards", "defense"):
            for player in team_data.get(pos_group, []):
                rows.append({
                    "game_id": game_id,
                    "player_id": player.get("playerId", 0),
                    "season": season,
                    "game_type": game_type,
                    "game_date": game_date,
                    "team_abbr": team_abbrev,
                    "opponent_abbr": opponent_abbrev,
                    "home_road_flag": home_road,
                    "goals": _safe_int(player.get("goals")),
                    "assists": _safe_int(player.get("assists")),
                    "points": _safe_int(player.get("points")),
                    "shots": _safe_int(player.get("sog")),
                    "pim": _safe_int(player.get("pim")),
                    "plus_minus": _safe_int(player.get("plusMinus")),
                    "time_on_ice": player.get("toi", ""),
                    "shifts": _safe_int(player.get("shifts")),
                    "power_play_goals": _safe_int(player.get("powerPlayGoals")),
                    "power_play_points": 0,
                    "shorthanded_goals": 0,
                    "game_winning_goals": 0,
                    "ot_goals": 0,
                    "blocked_shots": _safe_int(player.get("blockedShots")),
                    "hits": _safe_int(player.get("hits")),
                    "giveaways": _safe_int(player.get("giveaways")),
                    "takeaways": _safe_int(player.get("takeaways")),
                    "faceoff_pct": _safe_float(player.get("faceoffWinningPctg")),
                })

        for player in team_data.get("goalies", []):
            toi = player.get("toi", "")
            if toi == "0:00":
                continue

            rows.append({
                "game_id": game_id,
                "player_id": player.get("playerId", 0),
                "season": season,
                "game_type": game_type,
                "game_date": game_date,
                "team_abbr": team_abbrev,
                "opponent_abbr": opponent_abbrev,
                "home_road_flag": home_road,
                "goals": _safe_int(player.get("goals")),
                "assists": _safe_int(player.get("assists")),
                "points": _safe_int(player.get("points")),
                "shots": 0,
                "pim": _safe_int(player.get("pim")),
                "plus_minus": 0,
                "time_on_ice": toi,
                "shifts": 0,
                "power_play_goals": 0,
                "power_play_points": 0,
                "shorthanded_goals": 0,
                "game_winning_goals": 0,
                "ot_goals": 0,
                "blocked_shots": 0,
                "hits": 0,
                "giveaways": 0,
                "takeaways": 0,
                "faceoff_pct": None,
            })

    return rows


# ---------------------------------------------------------------------------
# Database UPSERT
# ---------------------------------------------------------------------------

UPSERT_SQL: str = """
    INSERT OR REPLACE INTO player_game_logs
        (game_id, player_id, season, game_type, game_date, team_abbr, opponent_abbr,
         home_road_flag, goals, assists, points, shots, pim, plus_minus,
         time_on_ice, shifts, power_play_goals, power_play_points,
         shorthanded_goals, game_winning_goals, ot_goals,
         blocked_shots, hits, giveaways, takeaways, faceoff_pct)
    VALUES
        (:game_id, :player_id, :season, :game_type, :game_date, :team_abbr, :opponent_abbr,
         :home_road_flag, :goals, :assists, :points, :shots, :pim, :plus_minus,
         :time_on_ice, :shifts, :power_play_goals, :power_play_points,
         :shorthanded_goals, :game_winning_goals, :ot_goals,
         :blocked_shots, :hits, :giveaways, :takeaways, :faceoff_pct)
"""


def upsert_game_logs(conn: sqlite3.Connection, logs: list[dict[str, object]]) -> int:
    """
    Insert or replace game log records.
    Skips rows where player_id is not in the players table (FK-safe).
    Returns number of rows inserted.
    """
    if not logs:
        return 0

    # Filter to only players that exist in the players table
    known = conn.execute("SELECT id FROM players").fetchall()
    known_ids = {row[0] for row in known}

    filtered = [row for row in logs if row["player_id"] in known_ids]
    skipped_fk = len(logs) - len(filtered)
    if skipped_fk > 0:
        logger.debug("Skipped %d rows for players not in players table.", skipped_fk)

    if not filtered:
        return 0

    try:
        cursor = conn.executemany(UPSERT_SQL, filtered)
        count = cursor.rowcount
        return count
    except sqlite3.Error as exc:
        logger.error("Database upsert failed: %s", exc)
        conn.rollback()
        return 0


# ---------------------------------------------------------------------------
# Skip-existing check
# ---------------------------------------------------------------------------

def has_game_data(conn: sqlite3.Connection, game_id: int) -> bool:
    """Check if game log data already exists for a given game_id."""
    cursor = conn.execute(
        "SELECT COUNT(*) FROM player_game_logs WHERE game_id = ?",
        (game_id,),
    )
    return cursor.fetchone()[0] > 0


# ---------------------------------------------------------------------------
# Main ingestion driver
# ---------------------------------------------------------------------------

def ingest_boxscore(
    season: str,
    game_type: int | None = None,
    teams: list[str] | None = None,
    skip_existing: bool = False,
    db_path: str = DB_PATH,
) -> None:
    """
    Main ingestion driver using the boxscore endpoint.

    Pipeline:
        1. Ingest rosters for all target teams
        2. Collect unique game IDs from schedules
        3. Fetch boxscore for each game and upsert player stats
    """
    if teams is None:
        teams = ALL_NHL_TEAMS

    gt_label = GAME_TYPE_LABELS.get(game_type, "All") if game_type else "All"

    with initialize_database(db_path) as conn:
        # Step 1: Ingest rosters
        logger.info("=== Step 1: Ingesting rosters for %d teams ===", len(teams))
        for team_abbrev in teams:
            players = fetch_roster(team_abbrev, season)
            if players:
                upsert_players(conn, players, team_abbrev, season)
            time.sleep(RATE_LIMIT_SECONDS)

        # Step 2: Collect unique game IDs
        logger.info("=== Step 2: Collecting game IDs from schedules ===")
        unique_games = collect_game_ids(teams, season, game_type)

        if not unique_games:
            logger.warning("No games found for season %s, game_type=%s.", season, gt_label)
            return

        # Step 3: Fetch boxscores and upsert
        logger.info("=== Step 3: Fetching boxscores (%d games) ===", len(unique_games))
        total_rows = 0
        skipped = 0
        errors = 0

        for i, game in enumerate(unique_games, 1):
            game_id = game.get("id", 0)

            if skip_existing and has_game_data(conn, game_id):
                skipped += 1
                if skipped % 50 == 0:
                    logger.info("Progress: %d/%d games processed (%d skipped, %d rows).",
                                i, len(unique_games), skipped, total_rows)
                continue

            boxscore = fetch_boxscore(game_id)
            if boxscore is None:
                errors += 1
                continue

            rows = normalize_boxscore(boxscore)
            if rows:
                upsert_game_logs(conn, rows)
                total_rows += len(rows)

            if i % 50 == 0 or i == len(unique_games):
                logger.info("Progress: %d/%d games processed (%d rows ingested, %d errors).",
                            i, len(unique_games), total_rows, errors)

            if i % 200 == 0:
                conn.commit()
                logger.info("Checkpoint: committed %d rows at game %d/%d.", total_rows, i, len(unique_games))

            time.sleep(RATE_LIMIT_SECONDS)

        conn.commit()

    logger.info(
        "Boxscore ingestion complete. Season: %s, Game type: %s. "
        "%d games processed, %d rows ingested, %d skipped, %d errors.",
        season, gt_label, len(unique_games), total_rows, skipped, errors,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for boxscore ingestion."""
    parser = argparse.ArgumentParser(
        description="Ingest NHL game data via the boxscore endpoint (efficient per-game approach)."
    )
    parser.add_argument(
        "--season",
        type=str,
        default="20252026",
        help="Season in YYYYYYYY format (default: 20252026).",
    )
    parser.add_argument(
        "--game-type",
        type=int,
        default=None,
        choices=[1, 2, 3],
        help="Game type: 1=Preseason, 2=Regular Season, 3=Playoffs. Default: all.",
    )
    parser.add_argument(
        "--teams",
        type=str,
        default=None,
        help="Comma-separated team abbreviations (default: all 32 teams).",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip games that already have data in the database.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.teams:
        teams = [t.strip().upper() for t in args.teams.split(",")]
    else:
        teams = ALL_NHL_TEAMS

    logger.info(
        "Starting boxscore ingestion: %d teams, season=%s, game_type=%s, skip_existing=%s.",
        len(teams),
        args.season,
        GAME_TYPE_LABELS.get(args.game_type, "All") if args.game_type else "All",
        args.skip_existing,
    )

    ingest_boxscore(
        season=args.season,
        game_type=args.game_type,
        teams=teams,
        skip_existing=args.skip_existing,
    )

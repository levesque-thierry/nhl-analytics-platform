"""
ingest_pbp.py — Play-by-Play Ingestion Pipeline

Fetches all play-by-play events for a season from the NHL API and stores
them in the play_by_play table. Each game requires 1 API call.

Usage:
    python ingest_pbp.py --season 20252026
    python ingest_pbp.py --season 20252026 --skip-existing
"""

import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent / "1_data_warehouse"))
from database_setup import initialize_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PBP_URL = "https://api-web.nhle.com/v1/gamecenter/{game_id}/play-by-play"
SCHEDULE_URL = "https://api-web.nhle.com/v1/club-schedule-season/{team}/{season}"
REQUEST_DELAY = 1.0  # seconds between API calls

ALL_TEAMS = [
    "ANA", "BOS", "BUF", "CAR", "CBJ", "CGY", "CHI", "COL", "DAL", "DET",
    "EDM", "FLA", "LAK", "MIN", "MTL", "NJD", "NSH", "NYI", "NYR", "OTT",
    "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR", "UTA", "VAN", "VGK",
    "WPG", "WSH",
]


def fetch_game_ids(season: str, team: str = "ALL") -> list[int]:
    """Fetch all game IDs for a season. team='ALL' fetches from all 32 teams."""
    teams = ALL_TEAMS if team == "ALL" else [team]
    all_game_ids: set[int] = set()

    for t in teams:
        url = SCHEDULE_URL.format(team=t, season=season)
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("Failed to fetch schedule for %s: %s", t, e)
            continue

        data = resp.json()
        for game in data.get("games", []):
            if game.get("gameType") in (2, 3):
                all_game_ids.add(game["id"])

    game_ids = sorted(all_game_ids)
    logger.info("Found %d unique games for season %s", len(game_ids), season)
    return game_ids


def extract_pbp_event(game_id: int, play: dict) -> tuple:
    """Extract a single PBP event row from API response."""
    details = play.get("details", {})
    period_desc = play.get("periodDescriptor", {})

    return (
        game_id,
        play.get("eventId", 0),
        period_desc.get("number", 0),
        play.get("timeInPeriod", ""),
        play.get("timeRemaining", ""),
        play.get("sortOrder", 0),
        play.get("typeCode", 0),
        play.get("typeDescKey", ""),
        play.get("situationCode", ""),
        play.get("homeTeamDefendingSide", ""),
        details.get("xCoord"),
        details.get("yCoord"),
        details.get("zoneCode", ""),
        details.get("shotType", ""),
        details.get("shootingPlayerId"),
        details.get("goalieInNetId"),
        details.get("eventOwnerTeamId"),
        details.get("blockingPlayerId"),
        details.get("scoringPlayerId"),
        details.get("scoringPlayerTotal"),
        details.get("assist1PlayerId"),
        details.get("assist1PlayerTotal"),
        details.get("assist2PlayerId"),
        details.get("assist2PlayerTotal"),
        details.get("awayScore"),
        details.get("homeScore"),
        details.get("awaySOG"),
        details.get("homeSOG"),
    )


def fetch_and_store_pbp(
    conn: sqlite3.Connection,
    game_ids: list[int],
    skip_existing: bool = True,
) -> int:
    """Fetch PBP for each game and store in database. Returns total events stored."""
    cursor = conn.cursor()

    # Get already-ingested game IDs
    if skip_existing:
        existing = {
            row[0]
            for row in cursor.execute(
                "SELECT DISTINCT game_id FROM play_by_play"
            ).fetchall()
        }
    else:
        existing = set()

    total_events = 0
    games_fetched = 0

    for i, game_id in enumerate(game_ids):
        if game_id in existing:
            logger.debug("Skipping game %d (already ingested)", game_id)
            continue

        url = PBP_URL.format(game_id=game_id)
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Failed to fetch PBP for game %d: %s", game_id, e)
            time.sleep(REQUEST_DELAY)
            continue

        data = resp.json()
        plays = data.get("plays", [])

        if not plays:
            logger.warning("No plays found for game %d", game_id)
            time.sleep(REQUEST_DELAY)
            continue

        rows = [extract_pbp_event(game_id, play) for play in plays]

        cursor.executemany(
            """INSERT OR REPLACE INTO play_by_play (
                game_id, event_id, period, time_in_period, time_remaining,
                sort_order, type_code, type_desc_key, situation_code,
                home_team_defending_side, x_coord, y_coord, zone_code,
                shot_type, shooting_player_id, goalie_in_net_id,
                event_owner_team_id, blocking_player_id, scoring_player_id,
                scoring_player_total, assist1_player_id, assist1_player_total,
                assist2_player_id, assist2_player_total, away_score, home_score,
                away_sog, home_sog
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        total_events += len(rows)
        games_fetched += 1

        if (i + 1) % 50 == 0 or (i + 1) == len(game_ids):
            logger.info(
                "Progress: %d/%d games fetched, %d total events",
                i + 1, len(game_ids), total_events,
            )

        time.sleep(REQUEST_DELAY)

    logger.info(
        "PBP ingestion complete: %d games, %d events stored",
        games_fetched, total_events,
    )
    return total_events


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest NHL play-by-play data")
    parser.add_argument("--season", required=True, help="Season (e.g. 20252026)")
    parser.add_argument("--team", default="ALL", help="Team abbreviation or ALL (default: ALL)")
    parser.add_argument("--no-skip", action="store_true", help="Re-fetch all games")
    args = parser.parse_args()

    game_ids = fetch_game_ids(args.season, args.team)
    if not game_ids:
        logger.error("No games found. Check season/team.")
        return

    with initialize_database() as conn:
        fetch_and_store_pbp(conn, game_ids, skip_existing=not args.no_skip)


if __name__ == "__main__":
    main()

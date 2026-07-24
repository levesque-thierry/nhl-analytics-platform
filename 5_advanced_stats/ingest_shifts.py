"""
ingest_shifts.py — Player Shift Ingestion Pipeline

Fetches all shift chart data for a season from the NHL API and stores
them in the player_shifts table. Each game requires 1 API call.

Usage:
    python ingest_shifts.py --season 20252026
    python ingest_shifts.py --season 20252026 --skip-existing
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

SHIFTS_URL = "https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId={game_id}"
SCHEDULE_URL = "https://api-web.nhle.com/v1/club-schedule-season/{team}/{season}"
REQUEST_DELAY = 1.0

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


def extract_shift_row(game_id: int, shift: dict) -> tuple:
    """Extract a single shift row from API response."""
    return (
        game_id,
        shift.get("id", 0),
        shift.get("playerId", 0),
        f"{shift.get('firstName', '')} {shift.get('lastName', '')}".strip(),
        shift.get("teamAbbrev", ""),
        shift.get("period", 0),
        shift.get("shiftNumber", 0),
        shift.get("startTime", ""),
        shift.get("endTime", ""),
        shift.get("duration", ""),
        shift.get("detailCode", 0),
        shift.get("eventNumber", 0),
        shift.get("hexValue", ""),
        shift.get("teamId", 0),
        shift.get("teamName", ""),
        shift.get("typeCode", 0),
    )


def fetch_and_store_shifts(
    conn: sqlite3.Connection,
    game_ids: list[int],
    skip_existing: bool = True,
) -> int:
    """Fetch shifts for each game and store in database. Returns total shifts stored."""
    cursor = conn.cursor()

    if skip_existing:
        existing = {
            row[0]
            for row in cursor.execute(
                "SELECT DISTINCT game_id FROM player_shifts"
            ).fetchall()
        }
    else:
        existing = set()

    total_shifts = 0
    games_fetched = 0

    for i, game_id in enumerate(game_ids):
        if game_id in existing:
            logger.debug("Skipping game %d (already ingested)", game_id)
            continue

        url = SHIFTS_URL.format(game_id=game_id)
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error("Failed to fetch shifts for game %d: %s", game_id, e)
            time.sleep(REQUEST_DELAY)
            continue

        data = resp.json()
        shifts = data.get("data", [])

        if not shifts:
            logger.warning("No shifts found for game %d", game_id)
            time.sleep(REQUEST_DELAY)
            continue

        rows = [extract_shift_row(game_id, shift) for shift in shifts]

        cursor.executemany(
            """INSERT OR REPLACE INTO player_shifts (
                game_id, shift_id, player_id, player_name, team_abbr,
                period, shift_number, start_time, end_time, duration,
                detail_code, event_number, hex_value, team_id, team_name,
                type_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()
        total_shifts += len(rows)
        games_fetched += 1

        if (i + 1) % 50 == 0 or (i + 1) == len(game_ids):
            logger.info(
                "Progress: %d/%d games fetched, %d total shifts",
                i + 1, len(game_ids), total_shifts,
            )

        time.sleep(REQUEST_DELAY)

    logger.info(
        "Shift ingestion complete: %d games, %d shifts stored",
        games_fetched, total_shifts,
    )
    return total_shifts


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest NHL player shift data")
    parser.add_argument("--season", required=True, help="Season (e.g. 20252026)")
    parser.add_argument("--team", default="ALL", help="Team abbreviation or ALL (default: ALL)")
    parser.add_argument("--no-skip", action="store_true", help="Re-fetch all games")
    args = parser.parse_args()

    game_ids = fetch_game_ids(args.season, args.team)
    if not game_ids:
        logger.error("No games found. Check season/team.")
        return

    with initialize_database() as conn:
        fetch_and_store_shifts(conn, game_ids, skip_existing=not args.no_skip)


if __name__ == "__main__":
    main()

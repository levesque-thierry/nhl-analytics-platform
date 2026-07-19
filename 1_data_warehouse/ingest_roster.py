"""
ingest_roster.py — NHL Team Roster Ingestion

Fetches team rosters from the NHL Web API and populates:
    - players: Biographical reference (no team affiliation)
    - player_team_seasons: Season-aware team tracking

CLI Usage:
    python ingest_roster.py --season 20252026
    python ingest_roster.py --season 20252026 --teams EDM,TOR,FLA
"""

import argparse
import logging
import sqlite3
import time

import requests

from database_setup import DB_PATH, initialize_database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

NHL_API_BASE: str = "https://api-web.nhle.com"
RATE_LIMIT_SECONDS: float = 0.5

ALL_NHL_TEAMS: list[str] = [
    "ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ",
    "DAL", "DET", "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD",
    "NYI", "NYR", "OTT", "PHI", "PIT", "SEA", "SJS", "STL", "TBL", "TOR",
    "VAN", "VGK", "WSH", "WPG", "UTA",
]


def fetch_roster(team_abbrev: str, season: str) -> list[dict[str, object]]:
    """
    Fetch the roster for a given team and season from the NHL Web API.
    Returns a flat list of player dicts normalized from all position groups.
    """
    url = f"{NHL_API_BASE}/v1/roster/{team_abbrev}/{season}"
    logger.info("Fetching roster: %s", url)

    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch roster for %s: %s", team_abbrev, exc)
        return []

    data = response.json()
    players: list[dict[str, object]] = []

    for group_key in ("forwards", "defensemen", "goalies"):
        for raw in data.get(group_key, []):
            player: dict[str, object] = {
                "id": raw.get("id"),
                "first_name": raw.get("firstName", {}).get("default", ""),
                "last_name": raw.get("lastName", {}).get("default", ""),
                "position": raw.get("positionCode", ""),
                "sweater_number": raw.get("sweaterNumber"),
                "shoots_catches": raw.get("shootsCatches", ""),
                "birth_date": raw.get("birthDate", ""),
            }
            players.append(player)

    logger.info("Team %s: %d players extracted.", team_abbrev, len(players))
    return players


def upsert_players(conn: sqlite3.Connection, players: list[dict[str, object]], team_abbrev: str, season: str) -> int:
    """
    Insert or replace player biographical records and team-season affiliations.
    Returns the number of player records affected.
    """
    player_sql = """
        INSERT OR REPLACE INTO players
            (id, first_name, last_name, position,
             sweater_number, shoots_catches, birth_date)
        VALUES
            (:id, :first_name, :last_name, :position,
             :sweater_number, :shoots_catches, :birth_date)
    """

    team_sql = """
        INSERT OR REPLACE INTO player_team_seasons
            (player_id, season, team_abbr)
        VALUES
            (?, ?, ?)
    """

    try:
        cursor = conn.executemany(player_sql, players)
        for player in players:
            conn.execute(team_sql, (player["id"], season, team_abbrev))
        conn.commit()
        count = cursor.rowcount
        logger.info("Upserted %d player records + team affiliations for %s.", count, team_abbrev)
        return count
    except sqlite3.Error as exc:
        logger.error("Database upsert failed for team %s: %s", team_abbrev, exc)
        conn.rollback()
        return 0


def ingest_roster(
    teams: list[str],
    season: str,
    db_path: str = DB_PATH,
) -> int:
    """
    Main ingestion driver: fetches rosters for the given teams and populates
    the players and player_team_seasons tables. Returns total players ingested.
    """
    total: int = 0

    with initialize_database(db_path) as conn:
        for team_abbrev in teams:
            players = fetch_roster(team_abbrev, season)
            if players:
                upsert_players(conn, players, team_abbrev, season)
                total += len(players)
            time.sleep(RATE_LIMIT_SECONDS)

    logger.info("Roster ingestion complete. Total players ingested: %d", total)
    return total


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for roster ingestion."""
    parser = argparse.ArgumentParser(
        description="Ingest NHL team rosters into the data warehouse."
    )
    parser.add_argument(
        "--season",
        type=str,
        default="20252026",
        help="Season string in YYYYYYYY format (default: 20252026).",
    )
    parser.add_argument(
        "--teams",
        type=str,
        default=None,
        help="Comma-separated team abbreviations (default: all 32 teams).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.teams:
        teams = [t.strip().upper() for t in args.teams.split(",")]
    else:
        teams = ALL_NHL_TEAMS

    logger.info("Starting roster ingestion for %d teams, season %s.", len(teams), args.season)
    ingest_roster(teams=teams, season=args.season)

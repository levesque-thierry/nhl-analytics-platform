"""
database_setup.py — NHL Data Warehouse Schema Initialization

Creates the SQLite database and normalized tables for the NHL Analytics Platform.
Idempotent: safe to run multiple times (CREATE TABLE IF NOT EXISTS).
Includes schema migration for existing databases (ALTER TABLE ADD COLUMN).

Tables:
    - players: Biographical reference (no team affiliation — see view)
    - player_team_seasons: Season-aware team tracking per player
    - player_game_logs: Per-game statistical records (FK → players)
      Includes boxscore-derived columns: blocked_shots, hits, giveaways, takeaways, faceoff_pct

Views:
    - v_player_current_team: Dynamic lookup of each player's latest team
"""

import contextlib
import logging
import sqlite3
from collections.abc import Generator
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH: str = str(Path(__file__).parent / "nhl_data.db")


def _get_existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of column names for a given table."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all tables if they don't already exist."""
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id              INTEGER PRIMARY KEY,
            first_name      TEXT NOT NULL,
            last_name       TEXT NOT NULL,
            position        TEXT,
            sweater_number  INTEGER,
            shoots_catches  TEXT,
            birth_date      TEXT
        )
    """)
    logger.info("Table 'players' verified.")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_team_seasons (
            player_id   INTEGER NOT NULL,
            season      TEXT NOT NULL,
            team_abbr   TEXT NOT NULL,
            PRIMARY KEY (player_id, season),
            FOREIGN KEY (player_id) REFERENCES players(id)
        )
    """)
    logger.info("Table 'player_team_seasons' verified.")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_game_logs (
            game_id             INTEGER NOT NULL,
            player_id           INTEGER NOT NULL,
            season              TEXT NOT NULL DEFAULT '',
            game_type           INTEGER NOT NULL DEFAULT 2,
            game_date           TEXT NOT NULL,
            team_abbr           TEXT NOT NULL,
            opponent_abbr       TEXT,
            home_road_flag      TEXT,
            goals               INTEGER DEFAULT 0,
            assists             INTEGER DEFAULT 0,
            points              INTEGER DEFAULT 0,
            shots               INTEGER DEFAULT 0,
            pim                 INTEGER DEFAULT 0,
            plus_minus          INTEGER DEFAULT 0,
            time_on_ice         TEXT,
            shifts              INTEGER DEFAULT 0,
            power_play_goals    INTEGER DEFAULT 0,
            power_play_points   INTEGER DEFAULT 0,
            shorthanded_goals   INTEGER DEFAULT 0,
            game_winning_goals  INTEGER DEFAULT 0,
            ot_goals            INTEGER DEFAULT 0,
            blocked_shots       INTEGER DEFAULT 0,
            hits                INTEGER DEFAULT 0,
            giveaways           INTEGER DEFAULT 0,
            takeaways           INTEGER DEFAULT 0,
            faceoff_pct         REAL,
            PRIMARY KEY (game_id, player_id),
            FOREIGN KEY (player_id) REFERENCES players(id)
        )
    """)
    logger.info("Table 'player_game_logs' verified.")

    # --- Advanced stats tables (Phase 6) ---

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS play_by_play (
            game_id                 INTEGER NOT NULL,
            event_id                INTEGER NOT NULL,
            period                  INTEGER NOT NULL,
            time_in_period          TEXT,
            time_remaining          TEXT,
            sort_order              INTEGER,
            type_code               INTEGER,
            type_desc_key           TEXT,
            situation_code          TEXT,
            home_team_defending_side TEXT,
            x_coord                 INTEGER,
            y_coord                 INTEGER,
            zone_code               TEXT,
            shot_type               TEXT,
            shooting_player_id      INTEGER,
            goalie_in_net_id        INTEGER,
            event_owner_team_id     INTEGER,
            blocking_player_id      INTEGER,
            scoring_player_id       INTEGER,
            scoring_player_total    INTEGER,
            assist1_player_id       INTEGER,
            assist1_player_total    INTEGER,
            assist2_player_id       INTEGER,
            assist2_player_total    INTEGER,
            away_score              INTEGER,
            home_score              INTEGER,
            away_sog                INTEGER,
            home_sog                INTEGER,
            PRIMARY KEY (game_id, event_id)
        )
    """)
    logger.info("Table 'play_by_play' verified.")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS player_shifts (
            game_id         INTEGER NOT NULL,
            shift_id        INTEGER NOT NULL,
            player_id       INTEGER NOT NULL,
            player_name     TEXT,
            team_abbr       TEXT,
            period          INTEGER,
            shift_number    INTEGER,
            start_time      TEXT,
            end_time        TEXT,
            duration        TEXT,
            detail_code     INTEGER,
            event_number    INTEGER,
            hex_value       TEXT,
            team_id         INTEGER,
            team_name       TEXT,
            type_code       INTEGER,
            PRIMARY KEY (game_id, shift_id)
        )
    """)
    logger.info("Table 'player_shifts' verified.")


def migrate_schema(conn: sqlite3.Connection) -> None:
    """
    Apply schema migrations for existing databases.
    Checks for missing columns/tables and adds them incrementally.
    Safe to run multiple times.
    """
    cursor = conn.cursor()

    # Remove current_team from players if it exists (migrated to player_team_seasons)
    players_cols = _get_existing_columns(conn, "players")
    if "current_team" in players_cols:
        logger.info("Migrating: dropping 'current_team' from players (now in player_team_seasons).")
        conn.execute("PRAGMA foreign_keys = OFF")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS players_new (
                id              INTEGER PRIMARY KEY,
                first_name      TEXT NOT NULL,
                last_name       TEXT NOT NULL,
                position        TEXT,
                sweater_number  INTEGER,
                shoots_catches  TEXT,
                birth_date      TEXT
            )
        """)
        cursor.execute("""
            INSERT INTO players_new (id, first_name, last_name, position, sweater_number, shoots_catches, birth_date)
            SELECT id, first_name, last_name, position, sweater_number, shoots_catches, birth_date
            FROM players
        """)
        cursor.execute("DROP TABLE players")
        cursor.execute("ALTER TABLE players_new RENAME TO players")
        conn.execute("PRAGMA foreign_keys = ON")
        logger.info("Migration complete: 'current_team' removed from players.")

    # Add season column to player_game_logs if missing
    game_log_cols = _get_existing_columns(conn, "player_game_logs")
    if "season" not in game_log_cols:
        logger.info("Migrating: adding 'season' to player_game_logs.")
        cursor.execute("ALTER TABLE player_game_logs ADD COLUMN season TEXT NOT NULL DEFAULT ''")
        # Backfill from game_date where possible
        cursor.execute("""
            UPDATE player_game_logs
            SET season = substr(game_date, 1, 4) || substr(game_date, 6, 4)
            WHERE season = '' AND length(game_date) = 10
        """)
        logger.info("Migration complete: 'season' added and backfilled.")

    if "game_type" not in game_log_cols:
        logger.info("Migrating: adding 'game_type' to player_game_logs.")
        cursor.execute("ALTER TABLE player_game_logs ADD COLUMN game_type INTEGER NOT NULL DEFAULT 2")
        logger.info("Migration complete: 'game_type' added (default: 2 = Regular Season).")

    # Add boxscore-derived columns if missing
    for col, col_def in [
        ("blocked_shots", "INTEGER DEFAULT 0"),
        ("hits", "INTEGER DEFAULT 0"),
        ("giveaways", "INTEGER DEFAULT 0"),
        ("takeaways", "INTEGER DEFAULT 0"),
        ("faceoff_pct", "REAL"),
    ]:
        if col not in game_log_cols:
            logger.info("Migrating: adding '%s' to player_game_logs.", col)
            cursor.execute(f"ALTER TABLE player_game_logs ADD COLUMN {col} {col_def}")

    conn.commit()


def create_indexes(conn: sqlite3.Connection) -> None:
    """Create performance indexes on high-frequency lookup columns."""
    cursor = conn.cursor()

    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_game_logs_player_id
        ON player_game_logs (player_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_game_logs_game_date
        ON player_game_logs (game_date)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_game_logs_season
        ON player_game_logs (season)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_game_logs_game_type
        ON player_game_logs (game_type)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_team_seasons_player
        ON player_team_seasons (player_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_team_seasons_season
        ON player_team_seasons (season)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pbp_game_id
        ON play_by_play (game_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pbp_type_desc
        ON play_by_play (type_desc_key)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pbp_situation
        ON play_by_play (situation_code)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_shifts_game_id
        ON player_shifts (game_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_shifts_player_id
        ON player_shifts (player_id)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_shifts_team
        ON player_shifts (team_abbr)
    """)
    logger.info("All indexes verified.")


def create_views(conn: sqlite3.Connection) -> None:
    """Create convenience views for common queries."""
    cursor = conn.cursor()

    cursor.execute("""
        CREATE VIEW IF NOT EXISTS v_player_current_team AS
        SELECT
            p.id AS player_id,
            p.first_name,
            p.last_name,
            p.position,
            pts.team_abbr AS current_team,
            pts.season
        FROM players p
        JOIN player_team_seasons pts ON p.id = pts.player_id
        WHERE pts.season = (
            SELECT MAX(pts2.season)
            FROM player_team_seasons pts2
            WHERE pts2.player_id = p.id
        )
    """)
    logger.info("View 'v_player_current_team' verified.")


@contextlib.contextmanager
def initialize_database(db_path: str = DB_PATH) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager: initialize the SQLite database with FK enforcement,
    tables, migrations, indexes, and views. Yields an open connection
    and closes it on exit.
    """
    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        logger.info("Connected to database: %s", db_path)
        logger.info("Foreign key enforcement: ON")

        create_tables(conn)
        migrate_schema(conn)
        create_indexes(conn)
        create_views(conn)

        conn.commit()
        logger.info("Database initialization complete.")
        yield conn

    except sqlite3.Error as exc:
        logger.error("Database initialization failed: %s", exc)
        raise

    finally:
        if conn is not None:
            conn.close()
            logger.debug("Database connection closed.")


if __name__ == "__main__":
    with initialize_database() as conn:
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY type, name")
        objects = [(row[0],) for row in cursor.fetchall()]
        logger.info("Database objects: %s", objects)

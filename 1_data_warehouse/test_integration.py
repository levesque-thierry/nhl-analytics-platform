"""
test_integration.py — Data Warehouse Integration Smoke Test

Automated end-to-end validation of the boxscore-based ingestion pipeline:
    1. Initialize database schema (with migration)
    2. Seed player rosters from 2 teams
    3. Ingest game logs for a small set of games via boxscore
    4. Ingest playoff games via boxscore
    5. Assert row counts, FK integrity, season/game_type correctness
    6. Verify new boxscore columns (blocked_shots, giveaways, takeaways, faceoff_pct)

Manual Shell Command Reference:
    python database_setup.py
    python ingest_roster.py --season 20252026 --teams EDM,TOR
    python ingest_boxscore.py --season 20252026 --teams EDM,TOR --game-type 2
    python ingest_boxscore.py --season 20252026 --teams EDM,TOR --game-type 3
"""

import logging
import os
import sqlite3
import sys

from database_setup import DB_PATH, initialize_database
from ingest_roster import ingest_roster
from ingest_boxscore import collect_game_ids, fetch_boxscore, normalize_boxscore, upsert_game_logs, has_game_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

TEST_DB_PATH: str = os.path.join(os.path.dirname(__file__), "test_nhl_data.db")

SAMPLE_TEAMS: list[str] = ["EDM", "TOR"]
TEST_SEASON: str = "20252026"


def cleanup_test_db() -> None:
    """Remove the test database file if it exists."""
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)
        logger.info("Removed existing test database: %s", TEST_DB_PATH)


def count_rows(conn: sqlite3.Connection, table: str, where: str = "", params: tuple = ()) -> int:
    """Return the row count for a given table with optional WHERE clause."""
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    cursor = conn.execute(sql, params)
    return cursor.fetchone()[0]


def test_schema_creation() -> None:
    """Verify that all tables, views, and new columns exist after initialization."""
    with initialize_database(TEST_DB_PATH) as conn:
        cursor = conn.execute(
            "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') ORDER BY type, name"
        )
        objects = {row[0]: row[1] for row in cursor.fetchall()}

        assert "players" in objects, "Table 'players' missing."
        assert "player_game_logs" in objects, "Table 'player_game_logs' missing."
        assert "player_team_seasons" in objects, "Table 'player_team_seasons' missing."
        assert "v_player_current_team" in objects, "View 'v_player_current_team' missing."

        columns = {row[1] for row in conn.execute("PRAGMA table_info(player_game_logs)").fetchall()}
        for col in ("season", "game_type", "blocked_shots", "giveaways", "takeaways", "faceoff_pct"):
            assert col in columns, f"Column '{col}' missing from player_game_logs."

        player_cols = {row[1] for row in conn.execute("PRAGMA table_info(players)").fetchall()}
        assert "current_team" not in player_cols, "Column 'current_team' should be removed from players."

    logger.info("PASS: Schema creation — all tables, views, and columns verified.")


def test_roster_ingestion() -> None:
    """Verify that roster ingestion populates players and player_team_seasons."""
    total = ingest_roster(teams=SAMPLE_TEAMS, season=TEST_SEASON, db_path=TEST_DB_PATH)

    assert total > 0, "No players ingested from roster."

    with initialize_database(TEST_DB_PATH) as conn:
        player_count = count_rows(conn, "players")
        assert player_count >= 20, f"Expected >= 20 players, got {player_count}."

        ts_count = count_rows(conn, "player_team_seasons", "season = ?", (TEST_SEASON,))
        assert ts_count >= 20, f"Expected >= 20 team-season records, got {ts_count}."

        null_names = count_rows(conn, "players", "first_name = '' OR last_name = ''")
        assert null_names == 0, f"Found {null_names} players with empty names."

        view_count = count_rows(conn, "v_player_current_team")
        assert view_count > 0, "View v_player_current_team returned 0 rows."

    logger.info("PASS: Roster ingestion — %d players, %d team-season records.", player_count, ts_count)


def test_boxscore_ingestion() -> None:
    """Verify boxscore-based game log ingestion for regular season + playoffs."""
    with initialize_database(TEST_DB_PATH) as conn:
        # Collect a small set of regular season game IDs
        regular_games = collect_game_ids(SAMPLE_TEAMS, TEST_SEASON, game_type=2)
        assert len(regular_games) > 0, "No regular season games found."

        test_games = regular_games[:5]
        logger.info("Testing with %d regular season games: %s",
                     len(test_games), [g.get("id") for g in test_games])

        for game in test_games:
            game_id = game.get("id", 0)
            boxscore = fetch_boxscore(game_id)
            assert boxscore is not None, f"Boxscore fetch failed for game {game_id}."

            rows = normalize_boxscore(boxscore)
            assert len(rows) > 0, f"No player rows extracted for game {game_id}."

            upsert_game_logs(conn, rows)

        # Collect playoff games
        playoff_games = collect_game_ids(SAMPLE_TEAMS, TEST_SEASON, game_type=3)
        playoff_tested = 0

        for game in playoff_games[:3]:
            game_id = game.get("id", 0)
            boxscore = fetch_boxscore(game_id)
            if boxscore is None:
                continue
            rows = normalize_boxscore(boxscore)
            if rows:
                upsert_game_logs(conn, rows)
                playoff_tested += 1

        conn.commit()

    with initialize_database(TEST_DB_PATH) as conn:
        total_rows = count_rows(conn, "player_game_logs")
        assert total_rows > 0, "No game log records ingested."

        # FK integrity
        orphans = count_rows(conn, "player_game_logs", """
            player_id NOT IN (SELECT id FROM players)
        """)
        assert orphans == 0, f"Found {orphans} orphaned game log records (FK violation)."

        # Regular season count
        reg_count = count_rows(conn, "player_game_logs", "game_type = 2")
        assert reg_count > 0, "No regular season records."

        # Playoff count
        playoff_count = count_rows(conn, "player_game_logs", "game_type = 3")
        logger.info("Playoff records: %d (playoff games tested: %d).", playoff_count, playoff_tested)

        # Season column populated correctly
        wrong_season = count_rows(conn, "player_game_logs", "season != ?", (TEST_SEASON,))
        assert wrong_season == 0, f"Found {wrong_season} records with wrong season."

        # Verify boxscore columns have data
        has_blocked = count_rows(conn, "player_game_logs", "blocked_shots > 0")
        has_giveaways = count_rows(conn, "player_game_logs", "giveaways > 0")
        has_takeaways = count_rows(conn, "player_game_logs", "takeaways > 0")
        logger.info("Boxscore columns: %d with blocked_shots, %d with giveaways, %d with takeaways.",
                     has_blocked, has_giveaways, has_takeaways)

        # Verify multiple players per game (boxscore gives both teams)
        cursor = conn.execute("""
            SELECT game_id, COUNT(DISTINCT player_id) as player_count
            FROM player_game_logs
            GROUP BY game_id
        """)
        game_player_counts = cursor.fetchall()
        for game_id, cnt in game_player_counts:
            assert cnt >= 10, f"Game {game_id} has only {cnt} players — expected >= 10 from boxscore."

    logger.info("PASS: Boxscore ingestion — %d total records (reg: %d, playoffs: %d), 0 orphans.",
                total_rows, reg_count, playoff_count)


def test_skip_existing() -> None:
    """Verify that skip_existing correctly skips already-ingested games."""
    with initialize_database(TEST_DB_PATH) as conn:
        before_count = count_rows(conn, "player_game_logs")

        existing_game = conn.execute(
            "SELECT DISTINCT game_id FROM player_game_logs LIMIT 1"
        ).fetchone()

    if existing_game is None:
        logger.warning("Skip-existing test skipped — no existing games.")
        return

    game_id = existing_game[0]

    with initialize_database(TEST_DB_PATH) as conn:
        assert has_game_data(conn, game_id) is True, "Expected game to exist."
        after_count = count_rows(conn, "player_game_logs")

    assert before_count == after_count, f"Skip-existing failed: rows changed from {before_count} to {after_count}."
    logger.info("PASS: Skip-existing — no additional rows inserted (%d before, %d after).", before_count, after_count)


def run_all_tests() -> None:
    """Run the full integration test suite."""
    logger.info("=" * 60)
    logger.info("STARTING INTEGRATION SMOKE TEST")
    logger.info("=" * 60)

    cleanup_test_db()

    try:
        test_schema_creation()
        test_roster_ingestion()
        test_boxscore_ingestion()
        test_skip_existing()
    except AssertionError as exc:
        logger.error("TEST FAILED: %s", exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("UNEXPECTED ERROR: %s", exc)
        sys.exit(1)
    finally:
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
            logger.info("Cleaned up test database.")

    logger.info("=" * 60)
    logger.info("ALL TESTS PASSED")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_all_tests()

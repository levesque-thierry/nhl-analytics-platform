"""
db.py — Shared database connection helper for the Streamlit dashboard.
"""

import sqlite3
from pathlib import Path

import pandas as pd

DB_PATH: str = str(Path(__file__).parent.parent / "1_data_warehouse" / "nhl_data.db")


def get_connection() -> sqlite3.Connection:
    """Return a read-only SQLite connection to the NHL data warehouse."""
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Execute a SQL query and return results as a pandas DataFrame."""
    with get_connection() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def table_count(table: str) -> int:
    """Return the row count for a given table."""
    df = query_df(f"SELECT COUNT(*) as cnt FROM {table}")
    return int(df["cnt"].iloc[0])

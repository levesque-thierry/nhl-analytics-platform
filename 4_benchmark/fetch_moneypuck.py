"""
fetch_moneypuck.py — Download MoneyPuck Season-Level Skater Data

Downloads the season summary CSV from MoneyPuck.com for benchmark comparison.
Free for non-commercial use (credit MoneyPuck.com).

CLI Usage:
    python fetch_moneypuck.py
    python fetch_moneypuck.py --season 20252026
"""

import argparse
import logging
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

MONEYPUCK_BASE = "https://moneypuck.com/moneypuck/playerData/seasonSummary"
DATA_DIR = Path(__file__).parent / "data"


def _season_to_mp_year(season: str) -> str:
    """Convert '20252026' to '2025' (MoneyPuck uses start year)."""
    return season[:4]


def fetch_moneypuck_skaters(season: str) -> pd.DataFrame:
    """
    Download MoneyPuck skater season summary for a given season.

    Args:
        season: Season string like '20252026'.

    Returns:
        DataFrame with all-situations skater data.
    """
    year = _season_to_mp_year(season)
    url = f"{MONEYPUCK_BASE}/{year}/regular/skaters.csv"
    logger.info("Downloading MoneyPuck data: %s", url)

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Failed to fetch MoneyPuck data: %s", exc)
        raise

    df = pd.read_csv(StringIO(response.text))
    logger.info("Downloaded %d rows, %d columns.", len(df), len(df.columns))

    # Filter to all situations only
    df = df[df["situation"] == "all"].copy()
    logger.info("All-situations rows: %d", len(df))

    return df


def save_moneypuck_data(df: pd.DataFrame, season: str) -> Path:
    """Save MoneyPuck data to local CSV."""
    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / f"moneypuck_{season}.csv"
    df.to_csv(out_path, index=False)
    logger.info("Saved to: %s", out_path)
    return out_path


def load_moneypuck_data(season: str) -> pd.DataFrame | None:
    """Load previously downloaded MoneyPuck data, or return None."""
    path = DATA_DIR / f"moneypuck_{season}.csv"
    if path.exists():
        return pd.read_csv(path)
    return None


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Download MoneyPuck skater data for benchmark comparison."
    )
    parser.add_argument(
        "--season",
        type=str,
        default="20252026",
        help="Season in YYYYYYYY format (default: 20252026).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if file exists locally.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    existing = load_moneypuck_data(args.season)
    if existing is not None and not args.force:
        logger.info(
            "Data already exists for %s (%d rows). Use --force to re-download.",
            args.season, len(existing),
        )
    else:
        df = fetch_moneypuck_skaters(args.season)
        save_moneypuck_data(df, args.season)
        logger.info("Done. Columns: %s", list(df.columns[:10]))

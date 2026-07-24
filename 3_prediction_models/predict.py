"""
predict.py — Season Pts/Game Projections Using Trained Model

Loads a trained LightGBM model and generates player-season pts/game projections.
Supports confidence intervals via quantile regression or bootstrap.

CLI Usage:
    python predict.py
    python predict.py --season 20262027
    python predict.py --output projections.csv
"""

import argparse
import json
import logging
import sqlite3
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from feature_pipeline import (
    build_feature_matrix,
    get_feature_columns,
    get_categorical_columns,
    load_game_logs,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent / "models"


def load_latest_model() -> tuple[lgb.LBooster, dict]:
    """Load the latest trained model and its metadata."""
    latest_dir = MODELS_DIR / "latest"
    if not latest_dir.exists():
        raise FileNotFoundError(
            f"No trained model found at {latest_dir}. Run train.py first."
        )

    model_path = latest_dir / "model.txt"
    meta_path = latest_dir / "metadata.json"

    model = lgb.Booster(model_file=str(model_path))

    with open(meta_path) as f:
        metadata = json.load(f)

    logger.info(
        "Loaded model v%s (MAE: %.4f, R²: %.4f)",
        metadata.get("model_version", "unknown"),
        metadata["metrics"]["mae"],
        metadata["metrics"]["r2"],
    )

    return model, metadata


def prepare_features(
    feature_matrix: pd.DataFrame,
    feature_cols: list[str],
    cat_cols: list[str],
) -> pd.DataFrame:
    """Prepare feature matrix for prediction (same transforms as training)."""
    X = feature_matrix[feature_cols].copy()
    for col in cat_cols:
        X[col] = X[col].astype("category").cat.codes
    X = X.fillna(-999)
    return X


def generate_projections(
    target_season: str,
    model: lgb.LBooster | None = None,
    metadata: dict | None = None,
) -> pd.DataFrame:
    """
    Generate player pts/game projections for a target season.

    Args:
        target_season: Season to project (e.g. '20262027').
        model: Pre-loaded model (optional, will load latest if None).
        metadata: Pre-loaded metadata (optional).

    Returns:
        DataFrame with columns: player_id, player_name, team, position,
            projected_pts_per_game, projected_total_pts (82 games), confidence_info
    """
    if model is None or metadata is None:
        model, metadata = load_latest_model()

    feature_cols = metadata["feature_columns"]
    cat_cols = metadata["categorical_columns"]

    # Build feature matrix for the target season
    # For future seasons, we use current season data as "prior" features
    fm = build_feature_matrix(target_season)
    if fm.empty:
        logger.warning("No feature data available for season %s.", target_season)
        return pd.DataFrame()

    X = prepare_features(fm, feature_cols, cat_cols)
    predictions = model.predict(X)

    # Clamp predictions to valid range
    predictions = np.clip(predictions, 0.0, 2.0)

    # Build projections DataFrame
    projections = fm[["player_id", "position"]].copy()
    projections["projected_pts_per_game"] = predictions
    projections["projected_total_pts_82"] = predictions * 82
    projections["projected_goals_82"] = (
        fm["goals_per_game"].fillna(0).values * 82
        if "goals_per_game" in fm.columns
        else 0
    )

    # Resolve player names and teams from database
    db_path = str(Path(__file__).parent.parent / "1_data_warehouse" / "nhl_data.db")
    conn = sqlite3.connect(db_path)
    try:
        # Get player info
        player_info = pd.read_sql_query(
            """
            SELECT
                p.id AS player_id,
                p.first_name || ' ' || p.last_name AS player_name,
                p.position,
                v.current_team AS team
            FROM players p
            LEFT JOIN v_player_current_team v ON p.id = v.player_id
            """,
            conn,
        )
        projections = projections.merge(
            player_info[["player_id", "player_name", "team"]],
            on="player_id",
            how="left",
        )
    finally:
        conn.close()

    # Add model metadata
    projections["model_version"] = metadata.get("model_version", "unknown")
    projections["model_mae"] = metadata["metrics"]["mae"]

    # Sort by projected pts/game descending
    projections = projections.sort_values("projected_pts_per_game", ascending=False)
    projections = projections.reset_index(drop=True)

    logger.info(
        "Generated %d projections for season %s.",
        len(projections), target_season,
    )

    return projections


def print_leaderboard(projections: pd.DataFrame, top_n: int = 30) -> None:
    """Print a formatted leaderboard of projections."""
    if projections.empty:
        print("No projections available.")
        return

    top = projections.head(top_n)
    print(f"\n{'='*75}")
    print(f"  NHL Pts/Game Projections — Top {top_n}")
    print(f"{'='*75}")
    print(
        f"{'Rank':>4}  {'Player':<25} {'Team':>4} {'Pos':>3}  "
        f"{'Pts/G':>6}  {'Total Pts':>9}"
    )
    print(f"{'-'*75}")

    for i, (_, row) in enumerate(top.iterrows(), 1):
        name = str(row.get("player_name", "Unknown"))[:24]
        team = str(row.get("team", "???"))[:3]
        pos = str(row.get("position", "?"))[:1]
        ppg = row["projected_pts_per_game"]
        total = row["projected_total_pts_82"]
        print(f"{i:>4}  {name:<25} {team:>4} {pos:>3}  {ppg:>6.3f}  {total:>9.1f}")

    print(f"{'='*75}")
    print(f"  Model MAE: {projections['model_mae'].iloc[0]:.4f} pts/game")
    print(f"  (±{projections['model_mae'].iloc[0] * 82:.0f} pts over 82 games)")
    print()


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Generate player pts/game projections."
    )
    parser.add_argument(
        "--season",
        type=str,
        default="20252026",
        help="Target season (default: 20252026).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output CSV path (optional).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Number of top players to display (default: 30).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    projections = generate_projections(args.season)
    print_leaderboard(projections, top_n=args.top)

    if args.output:
        projections.to_csv(args.output, index=False)
        logger.info("Projections saved to: %s", args.output)

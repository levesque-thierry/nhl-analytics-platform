"""
compare.py — Benchmark Comparison: Our Model vs MoneyPuck

Matches players between our predictions and MoneyPuck data,
computes comparison metrics, and outputs a unified DataFrame.

Comparison models:
    1. Our LightGBM: predicted pts/game (preseason)
    2. MoneyPuck xGoals: I_F_xGoals (in-season expected goals model)
    3. Actual: ground truth from our database

Usage:
    from compare import build_comparison
    df = build_comparison("20252026")
"""

import logging
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from fetch_moneypuck import load_moneypuck_data, fetch_moneypuck_skaters, save_moneypuck_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH = str(Path(__file__).parent.parent / "1_data_warehouse" / "nhl_data.db")
MIN_GAMES = 20

# Add prediction models to path
sys.path.insert(0, str(Path(__file__).parent.parent / "3_prediction_models"))


def _match_players(
    our_df: pd.DataFrame,
    mp_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Match players between our data and MoneyPuck by name.
    MoneyPuck uses 'First Last', we use 'first_name last_name'.
    Returns inner-joined DataFrame.
    """
    our_df = our_df.copy()
    mp_df = mp_df.copy()

    # Normalize names for matching
    our_df["_match_name"] = (
        our_df["first_name"].str.strip().str.lower()
        + " "
        + our_df["last_name"].str.strip().str.lower()
    )
    mp_df["_match_name"] = mp_df["name"].str.strip().str.lower()

    # Merge on name
    merged = our_df.merge(
        mp_df[["_match_name", "playerId", "team", "position",
               "I_F_xGoals", "I_F_goals", "I_F_points",
               "I_F_primaryAssists", "I_F_secondaryAssists",
               "I_F_shotsOnGoal", "games_played"]],
        on="_match_name",
        how="inner",
        suffixes=("", "_mp"),
    )

    logger.info(
        "Matched %d players (our %d x MoneyPuck %d).",
        len(merged), len(our_df), len(mp_df),
    )

    return merged


def _compute_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Compute regression metrics."""
    mask = ~(np.isnan(y_true) | np.isnan(y_pred))
    y_t, y_p = y_true[mask], y_pred[mask]
    if len(y_t) == 0:
        return {"mae": np.nan, "rmse": np.nan, "r2": np.nan, "corr": np.nan, "n": 0}

    return {
        "mae": float(mean_absolute_error(y_t, y_p)),
        "rmse": float(np.sqrt(mean_squared_error(y_t, y_p))),
        "r2": float(r2_score(y_t, y_p)),
        "corr": float(np.corrcoef(y_t, y_p)[0, 1]),
        "n": int(len(y_t)),
    }


def build_comparison(
    season: str,
    min_games: int = MIN_GAMES,
    db_path: str = DB_PATH,
) -> pd.DataFrame:
    """
    Build a comparison DataFrame matching our predictions with MoneyPuck data.

    Returns DataFrame with columns:
        player_name, team, position, gp,
        actual_pts_per_game, actual_goals_per_game,
        our_predicted_pts_per_game, mp_xgoals_per_game,
        our_error, mp_error
    """
    logger.info("Building comparison for season: %s", season)

    # --- 1. Load MoneyPuck data ---
    mp_df = load_moneypuck_data(season)
    if mp_df is None:
        logger.info("Downloading MoneyPuck data for %s...", season)
        mp_df = fetch_moneypuck_skaters(season)
        save_moneypuck_data(mp_df, season)

    # Filter MoneyPuck to skaters with enough games
    mp_df = mp_df[mp_df["games_played"] >= min_games].copy()
    logger.info("MoneyPuck skaters with >= %d GP: %d", min_games, len(mp_df))

    # --- 2. Load our model's predictions + actuals from feature matrix ---
    from feature_pipeline import build_feature_matrix
    from predict import load_latest_model as _load_model

    model, metadata = _load_model()
    feature_cols = metadata["feature_columns"]
    cat_cols = metadata["categorical_columns"]

    fm = build_feature_matrix(season)
    if fm.empty:
        logger.warning("No feature data for %s.", season)
        return pd.DataFrame()

    # Generate predictions
    X = fm[feature_cols].copy()
    for col in cat_cols:
        X[col] = X[col].astype("category").cat.codes
    X = X.fillna(-999)
    fm["projected_pts_per_game"] = np.clip(model.predict(X), 0.0, 2.0)

    # Filter to players with enough games
    our_df = fm[fm["target_games"] >= min_games].copy()

    # --- 3. Load player names ---
    conn = sqlite3.connect(db_path)
    try:
        players = pd.read_sql_query(
            "SELECT id, first_name, last_name FROM players", conn
        )
    finally:
        conn.close()

    our_df = our_df.merge(players, left_on="player_id", right_on="id", how="left")

    # --- 4. Match players ---
    merged = _match_players(our_df, mp_df)
    if merged.empty:
        logger.warning("No player matches found.")
        return pd.DataFrame()

    # --- 5. Compute comparison metrics ---
    result = pd.DataFrame({
        "player_name": merged["first_name"] + " " + merged["last_name"],
        "team": merged["team"],
        "position": merged["position"],
        "gp_ours": merged["target_games"].astype(int),
        "gp_mp": merged["games_played"].astype(int),
        # Actual stats (from our DB)
        "actual_pts_per_game": merged["pts_per_game"],
        "actual_goals_per_game": merged["goals_per_game"],
        # Our model prediction
        "our_predicted_pts_per_game": merged["projected_pts_per_game"],
        # MoneyPuck model: xGoals per game
        "mp_xgoals": merged["I_F_xGoals"].fillna(0),
        "mp_xgoals_per_game": merged["I_F_xGoals"].fillna(0) / merged["games_played"].replace(0, np.nan),
        # Actual from MoneyPuck (should match ours closely)
        "mp_actual_goals": merged["I_F_goals"],
        "mp_actual_points": merged["I_F_points"],
    })

    # Compute errors
    result["our_error"] = result["actual_pts_per_game"] - result["our_predicted_pts_per_game"]
    result["mp_error"] = result["actual_goals_per_game"] - result["mp_xgoals_per_game"]

    # Absolute errors
    result["our_abs_error"] = result["our_error"].abs()
    result["mp_abs_error"] = result["mp_error"].abs()

    # Who was more accurate per player?
    result["more_accurate"] = np.where(
        result["our_abs_error"] < result["mp_abs_error"],
        "Our Model",
        "MoneyPuck",
    )

    logger.info("Comparison built: %d matched players.", len(result))
    return result


def compute_summary_metrics(comparison_df: pd.DataFrame) -> dict[str, dict[str, float]]:
    """
    Compute summary metrics for both models.

    Returns:
        {
            "our_model": {"mae": ..., "rmse": ..., "r2": ..., "corr": ...},
            "moneypuck": {"mae": ..., "rmse": ..., "r2": ..., "corr": ...},
        }
    """
    if comparison_df.empty:
        return {}

    our_metrics = _compute_metrics(
        comparison_df["actual_pts_per_game"].values,
        comparison_df["our_predicted_pts_per_game"].values,
    )

    mp_metrics = _compute_metrics(
        comparison_df["actual_goals_per_game"].values,
        comparison_df["mp_xgoals_per_game"].values,
    )

    return {
        "our_model": our_metrics,
        "moneypuck": mp_metrics,
    }


if __name__ == "__main__":
    comparison = build_comparison("20252026")
    if comparison.empty:
        print("No comparison data.")
    else:
        print(f"\nMatched players: {len(comparison)}")
        print(f"\nOur model wins: {(comparison['more_accurate'] == 'Our Model').sum()}")
        print(f"MoneyPuck wins: {(comparison['more_accurate'] == 'MoneyPuck').sum()}")

        summary = compute_summary_metrics(comparison)
        print("\n=== Summary Metrics ===")
        for model_name, metrics in summary.items():
            print(f"\n{model_name}:")
            for k, v in metrics.items():
                print(f"  {k}: {v:.4f}")

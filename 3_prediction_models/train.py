"""
train.py — LightGBM Model Training for Pts/Game Prediction

Trains a LightGBM regressor on season-level features to predict pts_per_game.
Uses temporal split: train on earlier seasons, validate on the latest.

CLI Usage:
    python train.py
    python train.py --train-seasons 20232024,20242025 --val-season 20252026
    python train.py --tune
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from feature_pipeline import (
    build_feature_matrix,
    get_categorical_columns,
    get_feature_columns,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
MODELS_DIR = Path(__file__).parent / "models"
MODELS_DIR.mkdir(exist_ok=True)

DEFAULT_TRAIN_SEASONS = ["20232024", "20242025"]
DEFAULT_VAL_SEASON = "20252026"


def prepare_data(
    train_seasons: list[str],
    val_season: str,
) -> tuple[pd.DataFrame, pd.DataFrame, list[str], list[str]]:
    """
    Build and combine feature matrices for training and validation seasons.

    Returns:
        (X_train, X_val, feature_cols, cat_cols)
    """
    train_dfs = []
    for season in train_seasons:
        logger.info("Building training features for season: %s", season)
        fm = build_feature_matrix(season)
        if not fm.empty:
            train_dfs.append(fm)

    if not train_dfs:
        logger.error("No training data available.")
        sys.exit(1)

    X_train = pd.concat(train_dfs, ignore_index=True)
    logger.info("Training set: %d rows from seasons %s", len(X_train), train_seasons)

    logger.info("Building validation features for season: %s", val_season)
    X_val = build_feature_matrix(val_season)
    if X_val.empty:
        logger.error("No validation data available.")
        sys.exit(1)
    logger.info("Validation set: %d rows", len(X_val))

    feature_cols = get_feature_columns(X_train)
    cat_cols = [c for c in get_categorical_columns() if c in feature_cols]

    return X_train, X_val, feature_cols, cat_cols


def train_model(
    X_train: pd.DataFrame,
    X_val: pd.DataFrame,
    feature_cols: list[str],
    cat_cols: list[str],
    params: dict | None = None,
) -> tuple[lgb.LBooster, dict]:
    """
    Train a LightGBM regressor and evaluate on the validation set.

    Returns:
        (model, metrics_dict)
    """
    y_train = X_train["pts_per_game"].values
    y_val = X_val["pts_per_game"].values

    X_tr = X_train[feature_cols].copy()
    X_v = X_val[feature_cols].copy()

    # Encode categoricals as integers
    for col in cat_cols:
        X_tr[col] = X_tr[col].astype("category").cat.codes
        X_v[col] = X_v[col].astype("category").cat.codes

    # Replace NaN with LightGBM's sentinel
    X_tr = X_tr.fillna(-999)
    X_v = X_v.fillna(-999)

    default_params = {
        "objective": "regression",
        "metric": "mae",
        "boosting_type": "gbdt",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
        "n_jobs": -1,
        "seed": 42,
    }

    if params:
        default_params.update(params)

    train_data = lgb.Dataset(X_tr, label=y_train, categorical_feature=cat_cols)
    val_data = lgb.Dataset(X_v, label=y_val, categorical_feature=cat_cols, reference=train_data)

    callbacks = [
        lgb.log_evaluation(period=50),
        lgb.early_stopping(stopping_rounds=50),
    ]

    model = lgb.train(
        default_params,
        train_data,
        num_boost_round=1000,
        valid_sets=[val_data],
        callbacks=callbacks,
    )

    # Evaluate
    y_pred = model.predict(X_v)
    metrics = {
        "mae": float(mean_absolute_error(y_val, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_val, y_pred))),
        "r2": float(r2_score(y_val, y_pred)),
        "n_train": len(y_train),
        "n_val": len(y_val),
        "best_iteration": model.best_iteration,
        "trained_at": datetime.now().isoformat(),
    }

    logger.info("Validation MAE: %.4f", metrics["mae"])
    logger.info("Validation RMSE: %.4f", metrics["rmse"])
    logger.info("Validation R²: %.4f", metrics["r2"])
    logger.info("Best iteration: %d", metrics["best_iteration"])

    return model, metrics


def get_feature_importance(
    model: lgb.LBooster,
    feature_cols: list[str],
) -> pd.DataFrame:
    """Extract feature importance from the trained model."""
    importance = model.feature_importance(importance_type="gain")
    fi = pd.DataFrame({
        "feature": feature_cols,
        "importance": importance,
    }).sort_values("importance", ascending=False)
    fi["importance_pct"] = fi["importance"] / fi["importance"].sum() * 100
    return fi


def save_model(
    model: lgb.LBooster,
    metrics: dict,
    feature_cols: list[str],
    cat_cols: list[str],
    feature_importance: pd.DataFrame,
) -> Path:
    """Save model artifact and metadata."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_dir = MODELS_DIR / f"v_{timestamp}"
    model_dir.mkdir(exist_ok=True)

    # Save model
    model_path = model_dir / "model.txt"
    model.save_model(str(model_path))
    logger.info("Model saved: %s", model_path)

    # Save metadata
    metadata = {
        "metrics": metrics,
        "feature_columns": feature_cols,
        "categorical_columns": cat_cols,
        "model_version": timestamp,
    }
    meta_path = model_dir / "metadata.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Metadata saved: %s", meta_path)

    # Save feature importance
    fi_path = model_dir / "feature_importance.csv"
    feature_importance.to_csv(fi_path, index=False)
    logger.info("Feature importance saved: %s", fi_path)

    # Save "latest" symlink-style copy
    latest_dir = MODELS_DIR / "latest"
    latest_dir.mkdir(exist_ok=True)
    model.save_model(str(latest_dir / "model.txt"))
    with open(latest_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)
    feature_importance.to_csv(latest_dir / "feature_importance.csv", index=False)

    return model_dir


def print_residual_analysis(y_true: np.ndarray, y_pred: np.ndarray) -> None:
    """Print residual analysis summary."""
    residuals = y_true - y_pred
    logger.info("=== Residual Analysis ===")
    logger.info("  Mean residual: %.4f", residuals.mean())
    logger.info("  Std residual:  %.4f", residuals.std())
    logger.info("  Min residual:  %.4f", residuals.min())
    logger.info("  Max residual:  %.4f", residuals.max())

    # Percentile analysis
    abs_residuals = np.abs(residuals)
    for pct in [50, 75, 90, 95]:
        val = np.percentile(abs_residuals, pct)
        logger.info("  P%d absolute residual: %.4f", pct, val)


def run_training(
    train_seasons: list[str] | None = None,
    val_season: str | None = None,
    params: dict | None = None,
) -> dict:
    """
    Full training pipeline. Returns metrics dict.
    """
    train_s = train_seasons or DEFAULT_TRAIN_SEASONS
    val_s = val_season or DEFAULT_VAL_SEASON

    logger.info("=" * 60)
    logger.info("Pts/Game Prediction Model — Training")
    logger.info("Train seasons: %s | Val season: %s", train_s, val_s)
    logger.info("=" * 60)

    X_train, X_val, feature_cols, cat_cols = prepare_data(train_s, val_s)

    model, metrics = train_model(X_train, X_val, feature_cols, cat_cols, params)

    y_val = X_val["pts_per_game"].values
    X_v = X_val[feature_cols].copy()
    for col in cat_cols:
        X_v[col] = X_v[col].astype("category").cat.codes
    X_v = X_v.fillna(-999)
    y_pred = model.predict(X_v)
    print_residual_analysis(y_val, y_pred)

    fi = get_feature_importance(model, feature_cols)
    logger.info("\nTop 15 features by importance:")
    for _, row in fi.head(15).iterrows():
        logger.info("  %-40s  %.1f%%", row["feature"], row["importance_pct"])

    model_dir = save_model(model, metrics, feature_cols, cat_cols, fi)
    logger.info("Model saved to: %s", model_dir)

    return metrics


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Train LightGBM pts/game prediction model."
    )
    parser.add_argument(
        "--train-seasons",
        type=str,
        default=",".join(DEFAULT_TRAIN_SEASONS),
        help="Comma-separated training seasons (default: 20232024,20242025).",
    )
    parser.add_argument(
        "--val-season",
        type=str,
        default=DEFAULT_VAL_SEASON,
        help="Validation season (default: 20252026).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    train_seasons = [s.strip() for s in args.train_seasons.split(",")]
    run_training(train_seasons=train_seasons, val_season=args.val_season)

"""
4_Predictions.py — Player Pts/Game Prediction Dashboard

Visualizes the LightGBM model's season projections:
    1. Projection Leaderboard — ranked player predictions with filters
    2. Actual vs Predicted — scatter plot comparing model output to real stats
    3. Feature Importance — which features drive the model
    4. Model Metrics — performance statistics and residual analysis
"""

import json
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "3_prediction_models"))
sys.path.insert(0, str(PROJECT_ROOT / "1_data_warehouse"))

from db import get_connection, query_df
from predict import generate_projections, load_latest_model
from feature_pipeline import build_feature_matrix, get_feature_columns, get_categorical_columns

import lightgbm as lgb
import numpy as np

st.set_page_config(
    page_title="Predictions — NHL Analytics",
    page_icon="🎯",
    layout="wide",
)

COLORS = {
    "dark_blue": "#003087",
    "red": "#C8102E",
    "gold": "#D97706",
    "gray": "#6B7280",
    "light_gray": "#F5F5F5",
}


# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def load_projections(season: str) -> pd.DataFrame:
    """Generate player projections for a season."""
    return generate_projections(season)


@st.cache_data(ttl=3600)
def load_actuals_vs_predicted(season: str) -> pd.DataFrame:
    """Build actual vs predicted comparison for a completed season."""
    from predict import load_latest_model as _load_model

    model, metadata = _load_model()
    feature_cols = metadata["feature_columns"]
    cat_cols = metadata["categorical_columns"]

    fm = build_feature_matrix(season)
    if fm.empty:
        return pd.DataFrame()

    X = fm[feature_cols].copy()
    for col in cat_cols:
        X[col] = X[col].astype("category").cat.codes
    X = X.fillna(-999)

    fm["predicted_pts_per_game"] = model.predict(X)

    # Get player names
    conn = get_connection()
    try:
        players = query_df("""
            SELECT p.id AS player_id,
                   p.first_name || ' ' || p.last_name AS player_name,
                   p.position,
                   v.current_team AS team
            FROM players p
            LEFT JOIN v_player_current_team v ON p.id = v.player_id
        """)
    finally:
        conn.close()

    result = fm.merge(players, on="player_id", how="left")
    result["residual"] = result["pts_per_game"] - result["predicted_pts_per_game"]
    return result


@st.cache_data(ttl=3600)
def load_feature_importance() -> pd.DataFrame:
    """Load feature importance from the latest model."""
    fi_path = PROJECT_ROOT / "3_prediction_models" / "models" / "latest" / "feature_importance.csv"
    if not fi_path.exists():
        return pd.DataFrame()
    return pd.read_csv(fi_path)


@st.cache_data(ttl=3600)
def load_model_metadata() -> dict:
    """Load model metadata."""
    meta_path = PROJECT_ROOT / "3_prediction_models" / "models" / "latest" / "metadata.json"
    if not meta_path.exists():
        return {}
    with open(meta_path) as f:
        return json.load(f)


def get_season_options() -> list[str]:
    """Get distinct seasons from the database."""
    df = query_df("SELECT DISTINCT season FROM player_game_logs ORDER BY season DESC")
    return df["season"].tolist()


# ---------------------------------------------------------------------------
# Page title
# ---------------------------------------------------------------------------

st.title("🎯 Player Predictions")
st.caption("LightGBM pts/game model — trained on 2 seasons, validated on the latest.")

# Check if model exists
meta = load_model_metadata()
if not meta:
    st.error("No trained model found. Run `python -m train` in `3_prediction_models/` first.")
    st.stop()

# ---------------------------------------------------------------------------
# Tab layout
# ---------------------------------------------------------------------------

tab_leaderboard, tab_actual_vs_pred, tab_importance, tab_metrics = st.tabs([
    "🏆 Projection Leaderboard",
    "📈 Actual vs Predicted",
    "🔍 Feature Importance",
    "📊 Model Metrics",
])


# ===========================================================================
# TAB 1: PROJECTION LEADERBOARD
# ===========================================================================

with tab_leaderboard:
    lb_col1, lb_col2, lb_col3 = st.columns(3)

    with lb_col1:
        season_options = get_season_options()
        selected_season = st.selectbox("Season", season_options, key="proj_season")

    with lb_col2:
        pos_filter = st.radio(
            "Position",
            ["ALL", "F", "D"],
            horizontal=True,
            key="proj_pos",
        )

    with lb_col3:
        top_n = st.slider("Show Top N", 10, 100, 30, key="proj_top_n")

    projections = load_projections(selected_season)

    if projections.empty:
        st.info("No projections available for this season.")
    else:
        # Filter
        filtered = projections.copy()
        if pos_filter == "F":
            filtered = filtered[filtered["position"].isin(["C", "L", "R"])]
        elif pos_filter == "D":
            filtered = filtered[filtered["position"] == "D"]

        filtered = filtered.head(top_n)

        # KPI cards
        k1, k2, k3 = st.columns(3)
        k1.metric("Players Projected", len(projections))
        k2.metric("Avg Pts/Game", f"{projections['projected_pts_per_game'].mean():.3f}")
        k3.metric("Model MAE", f"{meta['metrics']['mae']:.4f}")

        st.divider()

        # Leaderboard table
        display_df = filtered[[
            "player_name", "team", "position",
            "projected_pts_per_game", "projected_total_pts_82",
        ]].copy()
        display_df.columns = ["Player", "Team", "Pos", "Pts/Game", "Total Pts (82)"]
        display_df["Rank"] = range(1, len(display_df) + 1)
        display_df = display_df[["Rank", "Player", "Team", "Pos", "Pts/Game", "Total Pts (82)"]]

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Pts/Game": st.column_config.NumberColumn("Pts/Game", format="%.3f"),
                "Total Pts (82)": st.column_config.NumberColumn("Total Pts (82)", format="%.1f"),
            },
        )

        # Pts/game distribution chart
        st.subheader("Pts/Game Distribution")
        chart_df = projections[["projected_pts_per_game"]].copy()
        chart_df.columns = ["Projected Pts/Game"]
        dist_chart = (
            alt.Chart(chart_df)
            .mark_bar(color=COLORS["dark_blue"], opacity=0.8, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
            .encode(
                x=alt.X("Projected Pts/Game:Q", bin=alt.Bin(maxbins=30), title="Projected Pts/Game"),
                y=alt.Y("count():Q", title="Number of Players"),
                tooltip=[
                    alt.Tooltip("Projected Pts/Game:Q", bin=True, title="Pts/Game"),
                    alt.Tooltip("count():Q", title="Players"),
                ],
            )
            .configure_view(stroke=None)
            .properties(height=280)
        )
        st.altair_chart(dist_chart, use_container_width=True)


# ===========================================================================
# TAB 2: ACTUAL VS PREDICTED
# ===========================================================================

with tab_actual_vs_pred:
    av_col1, av_col2 = st.columns([1, 3])

    with av_col1:
        av_season = st.selectbox("Season", season_options, key="av_season")
        av_position = st.radio(
            "Position",
            ["ALL", "F", "D"],
            horizontal=True,
            key="av_pos",
        )

    with av_col2:
        av_df = load_actuals_vs_predicted(av_season)

        if av_df.empty:
            st.info("No data available for this season.")
        else:
            filtered_av = av_df.copy()
            if av_position == "F":
                filtered_av = filtered_av[filtered_av["position"].isin(["C", "L", "R"])]
            elif av_position == "D":
                filtered_av = filtered_av[filtered_av["position"] == "D"]

            # Scatter plot
            scatter = (
                alt.Chart(filtered_av)
                .mark_circle(size=60, opacity=0.6)
                .encode(
                    x=alt.X("predicted_pts_per_game:Q", title="Predicted Pts/Game"),
                    y=alt.Y("pts_per_game:Q", title="Actual Pts/Game"),
                    color=alt.Color(
                        "position:N",
                        scale=alt.Scale(
                            domain=["C", "L", "R", "D"],
                            range=[COLORS["dark_blue"], COLORS["gold"], COLORS["red"], COLORS["gray"]],
                        ),
                        legend=alt.Legend(title="Position"),
                    ),
                    tooltip=[
                        alt.Tooltip("player_name:N", title="Player"),
                        alt.Tooltip("team:N", title="Team"),
                        alt.Tooltip("predicted_pts_per_game:Q", title="Predicted", format=".3f"),
                        alt.Tooltip("pts_per_game:Q", title="Actual", format=".3f"),
                    ],
                )
                .properties(height=400)
            )

            # Perfect prediction line
            line_df = pd.DataFrame({"x": [0, 1.5], "y": [0, 1.5]})
            perfect_line = (
                alt.Chart(line_df)
                .mark_line(strokeDash=[6, 4], color=COLORS["gray"], strokeWidth=1)
                .encode(
                    x=alt.X("x:Q"),
                    y=alt.Y("y:Q"),
                )
            )

            st.altair_chart((scatter + perfect_line).configure_view(stroke=None), use_container_width=True)

            # Metrics row
            m1, m2, m3, m4 = st.columns(4)
            residuals = filtered_av["residual"]
            m1.metric("MAE", f"{residuals.abs().mean():.4f}")
            m2.metric("RMSE", f"{np.sqrt((residuals ** 2).mean()):.4f}")
            m3.metric("R²", f"{1 - (residuals ** 2).sum() / ((filtered_av['pts_per_game'] - filtered_av['pts_per_game'].mean()) ** 2).sum():.4f}")
            m4.metric("Max Error", f"{residuals.abs().max():.4f}")


# ===========================================================================
# TAB 3: FEATURE IMPORTANCE
# ===========================================================================

with tab_importance:
    fi_df = load_feature_importance()

    if fi_df.empty:
        st.info("No feature importance data found.")
    else:
        fi_top = fi_df.head(20).copy()
        fi_top.columns = ["Feature", "Importance", "Importance %"]

        # Bar chart
        fi_chart = (
            alt.Chart(fi_top)
            .mark_bar(cornerRadiusEnd=4, color=COLORS["dark_blue"])
            .encode(
                y=alt.Y("Feature:N", sort="-x", title=None),
                x=alt.X("Importance %:Q", title="Importance (%)"),
                tooltip=[
                    alt.Tooltip("Feature:N"),
                    alt.Tooltip("Importance %:Q", format=".1f"),
                ],
            )
            .configure_view(stroke=None)
            .properties(height=400)
        )
        st.altair_chart(fi_chart, use_container_width=True)

        st.dataframe(fi_top, use_container_width=True, hide_index=True)


# ===========================================================================
# TAB 4: MODEL METRICS
# ===========================================================================

with tab_metrics:
    metrics = meta.get("metrics", {})

    st.subheader("Model Performance")

    mc1, mc2, mc3, mc4 = st.columns(4)
    mc1.metric("MAE", f"{metrics.get('mae', 0):.4f}")
    mc2.metric("RMSE", f"{metrics.get('rmse', 0):.4f}")
    mc3.metric("R²", f"{metrics.get('r2', 0):.4f}")
    mc4.metric("Best Iteration", metrics.get("best_iteration", "N/A"))

    st.divider()

    st.subheader("Training Details")
    st.markdown(f"""
    - **Model version:** {meta.get('model_version', 'unknown')}
    - **Trained at:** {metrics.get('trained_at', 'unknown')}
    - **Training samples:** {metrics.get('n_train', 'unknown')}
    - **Validation samples:** {metrics.get('n_val', 'unknown')}
    - **Feature columns:** {len(meta.get('feature_columns', []))}
    - **Categorical columns:** {', '.join(meta.get('categorical_columns', []))}
    """)

    st.divider()

    st.subheader("Interpretation Guide")
    st.markdown("""
    - **MAE** = average absolute error in pts/game. Multiply by 82 for season-level error.
    - **R²** = proportion of variance explained. 1.0 = perfect, 0.0 = baseline.
    - Features are from **prior seasons only** — no data leakage from the target season.
    - Model uses temporal split: trained on 2023-2024 + 2024-2025, validated on 2025-2026.
    """)

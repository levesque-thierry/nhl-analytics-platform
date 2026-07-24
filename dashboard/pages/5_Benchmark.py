"""
5_Benchmark.py — External Provider Benchmark Comparison

Compares our model's predictions against MoneyPuck's xGoals model:
    1. Head-to-Head Metrics — side-by-side MAE, RMSE, R², correlation
    2. Predicted vs Actual — scatter plots for both models
    3. Error Distribution — histogram of prediction errors
    4. Per-Player Leaderboard — which model was more accurate per player
"""

import sys
from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "4_benchmark"))
sys.path.insert(0, str(PROJECT_ROOT / "3_prediction_models"))
sys.path.insert(0, str(PROJECT_ROOT / "1_data_warehouse"))

from db import get_connection, query_df
from compare import build_comparison, compute_summary_metrics

st.set_page_config(
    page_title="Benchmark — NHL Analytics",
    page_icon="⚖️",
    layout="wide",
)

COLORS = {
    "dark_blue": "#003087",
    "red": "#C8102E",
    "gold": "#D97706",
    "gray": "#6B7280",
    "green": "#16A34A",
    "light_gray": "#F5F5F5",
}


# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400)
def load_comparison(season: str) -> pd.DataFrame:
    """Build comparison between our model and MoneyPuck."""
    return build_comparison(season)


@st.cache_data(ttl=86400)
def load_summary(season: str) -> dict:
    """Compute summary metrics."""
    comp = load_comparison(season)
    return compute_summary_metrics(comp)


def get_season_options() -> list[str]:
    """Get distinct seasons from the database."""
    df = query_df("SELECT DISTINCT season FROM player_game_logs ORDER BY season DESC")
    return df["season"].tolist()


# ---------------------------------------------------------------------------
# Page title
# ---------------------------------------------------------------------------

st.title("⚖️ Benchmark Comparison")
st.caption("Our LightGBM preseason model vs MoneyPuck's in-season xGoals model.")

# ---------------------------------------------------------------------------
# Tab layout
# ---------------------------------------------------------------------------

tab_metrics, tab_scatter, tab_error, tab_leaderboard = st.tabs([
    "📊 Head-to-Head Metrics",
    "📈 Predicted vs Actual",
    "📉 Error Distribution",
    "🏆 Per-Player Leaderboard",
])


# ===========================================================================
# TAB 1: HEAD-TO-HEAD METRICS
# ===========================================================================

with tab_metrics:
    m_col1, m_col2 = st.columns(2)

    with m_col1:
        season_options = get_season_options()
        selected_season = st.selectbox("Season", season_options, key="bench_season")
        min_gp = st.slider("Min Games Played", 10, 82, 20, key="bench_min_gp")

    comparison = load_comparison(selected_season)

    if comparison.empty:
        st.info("No comparison data available.")
        st.stop()

    # Filter by min GP
    comparison = comparison[
        (comparison["gp_ours"] >= min_gp) & (comparison["gp_mp"] >= min_gp)
    ]

    summary = compute_summary_metrics(comparison)

    st.divider()

    # Metric cards
    st.subheader("Model Performance Comparison")

    our_m = summary.get("our_model", {})
    mp_m = summary.get("moneypuck", {})

    # Note the different targets
    st.info(
        "**Note:** Our model predicts **pts/game** (preseason). "
        "MoneyPuck predicts **xGoals/game** (in-season). "
        "Both are compared against their respective actuals."
    )

    mc1, mc2, mc3, mc4, mc5 = st.columns(5)
    mc1.metric("Metric", "Our Model / MoneyPuck")
    mc2.metric(
        "MAE",
        f"{our_m.get('mae', 0):.4f} / {mp_m.get('mae', 0):.4f}",
        delta=f"{(our_m.get('mae', 0) - mp_m.get('mae', 0)):.4f}",
        delta_color="inverse",
    )
    mc3.metric(
        "RMSE",
        f"{our_m.get('rmse', 0):.4f} / {mp_m.get('rmse', 0):.4f}",
        delta=f"{(our_m.get('rmse', 0) - mp_m.get('rmse', 0)):.4f}",
        delta_color="inverse",
    )
    mc4.metric(
        "R²",
        f"{our_m.get('r2', 0):.4f} / {mp_m.get('r2', 0):.4f}",
        delta=f"{(our_m.get('r2', 0) - mp_m.get('r2', 0)):.4f}",
    )
    mc5.metric(
        "Correlation",
        f"{our_m.get('corr', 0):.4f} / {mp_m.get('corr', 0):.4f}",
        delta=f"{(our_m.get('corr', 0) - mp_m.get('corr', 0)):.4f}",
    )

    st.divider()

    # Win count
    our_wins = (comparison["more_accurate"] == "Our Model").sum()
    mp_wins = (comparison["more_accurate"] == "MoneyPuck").sum()
    total = len(comparison)

    st.subheader("Head-to-Head Win Count")

    wc1, wc2, wc3 = st.columns(3)
    wc1.metric("Our Model Wins", f"{our_wins} ({our_wins/total*100:.1f}%)")
    wc2.metric("MoneyPuck Wins", f"{mp_wins} ({mp_wins/total*100:.1f}%)")
    wc3.metric("Total Players", total)

    # Win bar chart
    win_df = pd.DataFrame({
        "Model": ["Our Model", "MoneyPuck"],
        "Wins": [our_wins, mp_wins],
    })
    win_chart = (
        alt.Chart(win_df)
        .mark_bar(cornerRadiusEnd=4, width=60)
        .encode(
            x=alt.X("Model:N", title=None),
            y=alt.Y("Wins:Q", title="Players More Accurate"),
            color=alt.Color(
                "Model:N",
                scale=alt.Scale(
                    domain=["Our Model", "MoneyPuck"],
                    range=[COLORS["dark_blue"], COLORS["red"]],
                ),
                legend=None,
            ),
        )
        .configure_view(stroke=None)
        .properties(height=250)
    )
    st.altair_chart(win_chart, use_container_width=False)


# ===========================================================================
# TAB 2: PREDICTED VS ACTUAL
# ===========================================================================

with tab_scatter:
    sc_col1, sc_col2 = st.columns([1, 3])

    with sc_col1:
        sc_position = st.radio(
            "Position",
            ["ALL", "F", "D"],
            horizontal=True,
            key="sc_pos",
        )

    with sc_col2:
        filtered = comparison.copy()
        if sc_position == "F":
            filtered = filtered[filtered["position"].isin(["C", "L", "R"])]
        elif sc_position == "D":
            filtered = filtered[filtered["position"] == "D"]

        # Our model scatter
        our_scatter = (
            alt.Chart(filtered)
            .mark_circle(size=50, opacity=0.6, color=COLORS["dark_blue"])
            .encode(
                x=alt.X("our_predicted_pts_per_game:Q", title="Our Predicted Pts/Game"),
                y=alt.Y("actual_pts_per_game:Q", title="Actual Pts/Game"),
                tooltip=[
                    alt.Tooltip("player_name:N", title="Player"),
                    alt.Tooltip("team:N", title="Team"),
                    alt.Tooltip("our_predicted_pts_per_game:Q", title="Predicted", format=".3f"),
                    alt.Tooltip("actual_pts_per_game:Q", title="Actual", format=".3f"),
                ],
            )
        )

        # MoneyPuck scatter
        mp_scatter = (
            alt.Chart(filtered)
            .mark_circle(size=50, opacity=0.6, color=COLORS["red"])
            .encode(
                x=alt.X("mp_xgoals_per_game:Q", title="MoneyPuck xGoals/Game"),
                y=alt.Y("actual_goals_per_game:Q", title="Actual Goals/Game"),
                tooltip=[
                    alt.Tooltip("player_name:N", title="Player"),
                    alt.Tooltip("team:N", title="Team"),
                    alt.Tooltip("mp_xgoals_per_game:Q", title="xGoals", format=".3f"),
                    alt.Tooltip("actual_goals_per_game:Q", title="Actual", format=".3f"),
                ],
            )
        )

        # Perfect prediction lines
        line_data = pd.DataFrame({"x": [0, 1.5], "y": [0, 1.5]})
        perfect_line = (
            alt.Chart(line_data)
            .mark_line(strokeDash=[6, 4], color=COLORS["gray"], strokeWidth=1)
            .encode(x="x:Q", y="y:Q")
        )

        # Side by side charts
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Our Model** (pts/game)")
            st.altair_chart(our_scatter + perfect_line, use_container_width=True)
        with col_b:
            st.markdown("**MoneyPuck** (xGoals/game)")
            st.altair_chart(mp_scatter + perfect_line, use_container_width=True)


# ===========================================================================
# TAB 3: ERROR DISTRIBUTION
# ===========================================================================

with tab_error:
    st.subheader("Prediction Error Distribution")

    err_df = comparison[["our_error", "mp_error"]].melt(
        var_name="Model", value_name="Error"
    )
    err_df["Model"] = err_df["Model"].map({
        "our_error": "Our Model (pts/game)",
        "mp_error": "MoneyPuck (xGoals/game)",
    })

    err_chart = (
        alt.Chart(err_df)
        .mark_bar(opacity=0.7)
        .encode(
            x=alt.X("Error:Q", title="Prediction Error", bin=alt.Bin(maxbins=40)),
            y=alt.Y("count():Q", title="Count"),
            color=alt.Color(
                "Model:N",
                scale=alt.Scale(
                    domain=["Our Model (pts/game)", "MoneyPuck (xGoals/game)"],
                    range=[COLORS["dark_blue"], COLORS["red"]],
                ),
            ),
            tooltip=[
                alt.Tooltip("Model:N"),
                alt.Tooltip("Error:Q", format=".4f"),
                alt.Tooltip("count():Q"),
            ],
        )
        .configure_view(stroke=None)
        .properties(height=350)
    )
    st.altair_chart(err_chart, use_container_width=True)

    # Error stats
    st.divider()
    es1, es2, es3, es4 = st.columns(4)
    es1.metric(
        "Mean Error (Ours)",
        f"{comparison['our_error'].mean():.4f}",
        help="Positive = underpredicted, Negative = overpredicted",
    )
    es2.metric(
        "Mean Error (MP)",
        f"{comparison['mp_error'].mean():.4f}",
    )
    es3.metric(
        "Std Error (Ours)",
        f"{comparison['our_error'].std():.4f}",
    )
    es4.metric(
        "Std Error (MP)",
        f"{comparison['mp_error'].std():.4f}",
    )


# ===========================================================================
# TAB 4: PER-PLAYER LEADERBOARD
# ===========================================================================

with tab_leaderboard:
    lb_col1, lb_col2, lb_col3 = st.columns(3)

    with lb_col1:
        lb_position = st.radio(
            "Position",
            ["ALL", "F", "D"],
            horizontal=True,
            key="lb_pos",
        )

    with lb_col2:
        lb_sort = st.selectbox(
            "Sort By",
            ["our_abs_error", "mp_abs_error", "actual_pts_per_game"],
            index=0,
            key="lb_sort",
        )

    with lb_col3:
        lb_show = st.selectbox(
            "Show",
            ["Most Accurate (Ours)", "Most Accurate (MoneyPuck)", "Highest Disagreement"],
            key="lb_show",
        )

    filtered_lb = comparison.copy()
    if lb_position == "F":
        filtered_lb = filtered_lb[filtered_lb["position"].isin(["C", "L", "R"])]
    elif lb_position == "D":
        filtered_lb = filtered_lb[filtered_lb["position"] == "D"]

    # Apply show filter
    if lb_show == "Most Accurate (Ours)":
        filtered_lb = filtered_lb.nsmallest(30, "our_abs_error")
    elif lb_show == "Most Accurate (MoneyPuck)":
        filtered_lb = filtered_lb.nsmallest(30, "mp_abs_error")
    else:
        filtered_lb["disagreement"] = (filtered_lb["our_predicted_pts_per_game"] - filtered_lb["mp_xgoals_per_game"]).abs()
        filtered_lb = filtered_lb.nlargest(30, "disagreement")

    # Display table
    display_df = filtered_lb[[
        "player_name", "team", "position",
        "actual_pts_per_game", "actual_goals_per_game",
        "our_predicted_pts_per_game", "mp_xgoals_per_game",
        "our_abs_error", "mp_abs_error", "more_accurate",
    ]].copy()
    display_df.columns = [
        "Player", "Team", "Pos",
        "Actual Pts/G", "Actual G/G",
        "Ours Pred", "MP xG/G",
        "Ours Err", "MP Err", "Winner",
    ]

    st.dataframe(display_df, use_container_width=True, hide_index=True)

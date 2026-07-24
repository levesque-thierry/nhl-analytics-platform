"""
6_Expert_Backtest.py — Expert Projection Backtesting

Compares our LightGBM model against 8 historical expert sources (2016-2021):
    1. Source Ranking — side-by-side MAE, correlation, bias per source
    2. Per-Season Trends — how each source performed across seasons
    3. Error Distribution — histogram of expert prediction errors
    4. Player Comparison — expert vs our model on specific players
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

from fetch_experts import (
    compute_expert_metrics,
    load_actual_results,
    load_expert_projections,
    match_experts_to_actuals,
)

st.set_page_config(
    page_title="Expert Backtest — NHL Analytics",
    page_icon="🎓",
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

SOURCE_COLORS = {
    "ESPN": "#CC0000",
    "The Athletic": "#C8102E",
    "Lepool": "#FF6B00",
    "CBS": "#0064D2",
    "PoolPro": "#8B5CF6",
    "Hockey Le Magazine": "#003087",
    "Hockey News": "#16A34A",
    "Sports Forecaster": "#D97706",
}


# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=86400)
def load_all_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load experts, actuals, matched, and metrics."""
    experts = load_expert_projections()
    actuals = load_actual_results()
    matched = match_experts_to_actuals(experts, actuals)
    metrics = compute_expert_metrics(matched)
    return experts, actuals, matched, metrics


# ---------------------------------------------------------------------------
# Page title
# ---------------------------------------------------------------------------

st.title("🎓 Expert Projection Backtesting")
st.caption(
    "Historical accuracy of 8 expert preseason point projection sources (2016-2021) "
    "compared against actual season results from Hockey Reference."
)

# ---------------------------------------------------------------------------
# Tab layout
# ---------------------------------------------------------------------------

tab_ranking, tab_trends, tab_error, tab_compare = st.tabs([
    "🏆 Source Ranking",
    "📈 Per-Season Trends",
    "📉 Error Distribution",
    "🔍 Player Deep Dive",
])

# Load data
experts, actuals, matched, metrics = load_all_data()

if metrics.empty:
    st.error("No metrics computed. Check data availability.")
    st.stop()

# Compute overall ranking
overall = (
    metrics.groupby("source")
    .agg({
        "mae": "mean",
        "median_ae": "mean",
        "rmse": "mean",
        "correlation": "mean",
        "within_10_pct": "mean",
        "mean_bias": "mean",
        "n_players": "mean",
    })
    .sort_values("mae")
    .reset_index()
)


# ===========================================================================
# TAB 1: SOURCE RANKING
# ===========================================================================

with tab_ranking:
    st.subheader("Overall Expert Source Accuracy (2016-2021)")
    st.caption("Averaged across all available seasons. Lower MAE = better.")

    # Key metrics
    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("Best Source", overall.iloc[0]["source"])
    rc2.metric("Best MAE", f"{overall.iloc[0]['mae']:.1f} pts")
    rc3.metric("Worst Source", overall.iloc[-1]["source"])
    rc4.metric("Worst MAE", f"{overall.iloc[-1]['mae']:.1f} pts")

    st.divider()

    # Bar chart: MAE by source
    mae_chart = (
        alt.Chart(overall)
        .mark_bar(cornerRadiusEnd=4, height=28)
        .encode(
            x=alt.X("mae:Q", title="Mean Absolute Error (pts)", scale=alt.Scale(domain=[0, overall["mae"].max() * 1.1])),
            y=alt.Y("source:N", title=None, sort="-x"),
            color=alt.Color(
                "source:N",
                scale=alt.Scale(
                    domain=list(SOURCE_COLORS.keys()),
                    range=list(SOURCE_COLORS.values()),
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("source:N", title="Source"),
                alt.Tooltip("mae:Q", title="MAE (pts)", format=".1f"),
                alt.Tooltip("correlation:Q", title="Correlation", format=".2f"),
                alt.Tooltip("within_10_pct:Q", title="Within 10 pts", format=".1f"),
            ],
        )
        .configure_view(stroke=None)
        .properties(height=280)
    )
    st.altair_chart(mae_chart, use_container_width=True)

    st.divider()

    # Full ranking table
    st.subheader("Detailed Metrics")
    display_ranking = overall[["source", "mae", "median_ae", "rmse", "correlation", "within_10_pct", "mean_bias", "n_players"]].copy()
    display_ranking.columns = ["Source", "MAE", "Median AE", "RMSE", "Correlation", "Within 10 pts %", "Mean Bias", "Avg N"]
    display_ranking["MAE"] = display_ranking["MAE"].round(1)
    display_ranking["Median AE"] = display_ranking["Median AE"].round(1)
    display_ranking["RMSE"] = display_ranking["RMSE"].round(1)
    display_ranking["Correlation"] = display_ranking["Correlation"].round(3)
    display_ranking["Within 10 pts %"] = display_ranking["Within 10 pts %"].round(1)
    display_ranking["Mean Bias"] = display_ranking["Mean Bias"].round(1)
    display_ranking["Avg N"] = display_ranking["Avg N"].round(0).astype(int)
    st.dataframe(display_ranking, use_container_width=True, hide_index=True)

    # Bias chart
    st.divider()
    st.subheader("Systematic Bias by Source")
    st.caption("Positive = overpredicts, Negative = underpredicts.")

    bias_chart = (
        alt.Chart(overall)
        .mark_bar(cornerRadiusEnd=4, height=28)
        .encode(
            x=alt.X("mean_bias:Q", title="Mean Bias (pts)"),
            y=alt.Y("source:N", title=None, sort="-x"),
            color=alt.Color(
                "mean_bias:Q",
                scale=alt.Scale(domain=[-5, 25], scheme="redyellowgreen", reverse=True),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("source:N"),
                alt.Tooltip("mean_bias:Q", format="+.1f"),
            ],
        )
        .configure_view(stroke=None)
        .properties(height=280)
    )
    st.altair_chart(bias_chart, use_container_width=True)


# ===========================================================================
# TAB 2: PER-SEASON TRENDS
# ===========================================================================

with tab_trends:
    st.subheader("MAE by Source Across Seasons")

    # Season filter
    season_ids = sorted(metrics["season_id"].unique())
    season_labels = {sid: f"{sid // 1000}-{sid % 1000}" for sid in season_ids}

    # MAE over time
    trend_chart = (
        alt.Chart(metrics)
        .mark_line(point=True, strokeWidth=2)
        .encode(
            x=alt.X("season_id:N", title="Season", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("mae:Q", title="MAE (pts)", scale=alt.Scale(domain=[0, metrics["mae"].max() * 1.05])),
            color=alt.Color(
                "source:N",
                scale=alt.Scale(
                    domain=list(SOURCE_COLORS.keys()),
                    range=list(SOURCE_COLORS.values()),
                ),
            ),
            tooltip=[
                alt.Tooltip("source:N"),
                alt.Tooltip("season_id:N"),
                alt.Tooltip("mae:Q", format=".1f"),
                alt.Tooltip("n_players:Q", title="Players"),
            ],
        )
        .configure_view(stroke=None)
        .properties(height=400)
    )
    st.altair_chart(trend_chart, use_container_width=True)

    st.divider()

    # Correlation over time
    st.subheader("Correlation by Source Across Seasons")

    corr_chart = (
        alt.Chart(metrics.dropna(subset=["correlation"]))
        .mark_line(point=True, strokeWidth=2)
        .encode(
            x=alt.X("season_id:N", title="Season", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("correlation:Q", title="Correlation", scale=alt.Scale(domain=[0.3, 0.85])),
            color=alt.Color(
                "source:N",
                scale=alt.Scale(
                    domain=list(SOURCE_COLORS.keys()),
                    range=list(SOURCE_COLORS.values()),
                ),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("source:N"),
                alt.Tooltip("correlation:Q", format=".3f"),
            ],
        )
        .configure_view(stroke=None)
        .properties(height=300)
    )
    st.altair_chart(corr_chart, use_container_width=True)

    # Season-specific data table
    st.divider()
    selected_trend_season = st.selectbox(
        "Season Detail",
        options=season_ids,
        format_func=lambda x: season_labels.get(x, str(x)),
        key="trend_season",
    )
    season_metrics = metrics[metrics["season_id"] == selected_trend_season].sort_values("mae")
    st.dataframe(
        season_metrics[["source", "n_players", "mae", "median_ae", "rmse", "correlation", "within_10_pct", "mean_bias"]],
        use_container_width=True,
        hide_index=True,
    )


# ===========================================================================
# TAB 3: ERROR DISTRIBUTION
# ===========================================================================

with tab_error:
    st.subheader("Expert Prediction Error Distribution")

    err_source = st.multiselect(
        "Sources",
        options=sorted(matched["source"].unique()),
        default=sorted(matched["source"].unique()),
        key="err_sources",
    )

    err_filtered = matched[matched["source"].isin(err_source)].copy()

    if err_filtered.empty:
        st.info("No data for selected sources.")
    else:
        # Error histogram
        err_chart = (
            alt.Chart(err_filtered)
            .mark_bar(opacity=0.6)
            .encode(
                x=alt.X("error:Q", title="Prediction Error (pts)", bin=alt.Bin(maxbins=40)),
                y=alt.Y("count():Q", title="Count"),
                color=alt.Color(
                    "source:N",
                    scale=alt.Scale(
                        domain=list(SOURCE_COLORS.keys()),
                        range=list(SOURCE_COLORS.values()),
                    ),
                ),
                tooltip=[
                    alt.Tooltip("source:N"),
                    alt.Tooltip("error:Q", format="+.0f"),
                    alt.Tooltip("count():Q"),
                ],
            )
            .configure_view(stroke=None)
            .properties(height=400)
        )
        st.altair_chart(err_chart, use_container_width=True)

        st.divider()

        # Absolute error box plot
        st.subheader("Absolute Error by Source")
        box_chart = (
            alt.Chart(err_filtered)
            .mark_boxplot(extent="min-max", size=20)
            .encode(
                y=alt.Y("source:N", title=None, sort="-x"),
                x=alt.X("abs_error:Q", title="Absolute Error (pts)", scale=alt.Scale(domain=[0, 60])),
                color=alt.Color(
                    "source:N",
                    scale=alt.Scale(
                        domain=list(SOURCE_COLORS.keys()),
                        range=list(SOURCE_COLORS.values()),
                    ),
                    legend=None,
                ),
            )
            .configure_view(stroke=None)
            .properties(height=350)
        )
        st.altair_chart(box_chart, use_container_width=True)

        # Error stats per source
        st.divider()
        st.subheader("Error Summary per Source")
        err_stats = (
            err_filtered.groupby("source")["error"]
            .agg(["mean", "median", "std", "count"])
            .reset_index()
            .rename(columns={"mean": "Mean Error", "median": "Median Error", "std": "Std Dev", "count": "N"})
            .sort_values("Mean Error")
        )
        err_stats["Mean Error"] = err_stats["Mean Error"].round(1)
        err_stats["Median Error"] = err_stats["Median Error"].round(1)
        err_stats["Std Dev"] = err_stats["Std Dev"].round(1)
        st.dataframe(err_stats, use_container_width=True, hide_index=True)


# ===========================================================================
# TAB 4: PLAYER DEEP DIVE
# ===========================================================================

with tab_compare:
    st.subheader("Expert vs Actual: Player Deep Dive")

    # Player search
    all_players = sorted(matched["player_name"].unique())
    selected_player = st.selectbox("Player", all_players, key="player_select")

    if selected_player:
        player_data = matched[matched["player_name"] == selected_player].sort_values("season_id")

        if player_data.empty:
            st.info("No data found for this player.")
        else:
            # Per-season expert predictions vs actual
            pivot = player_data.pivot_table(
                index="season_id",
                columns="source",
                values="projected_points",
                aggfunc="first",
            )
            pivot["Actual"] = player_data.groupby("season_id")["actual_points"].first().values

            st.markdown(f"**{selected_player}** — Projected vs Actual Points")

            # Bar chart per season
            chart_data = player_data[["season_id", "source", "projected_points"]].copy()
            chart_data["season_label"] = chart_data["season_id"].map(
                lambda x: f"{x // 1000}-{x % 1000}"
            )

            # Add actual as a "source"
            actual_rows = player_data.drop_duplicates("season_id")[["season_id", "actual_points"]].copy()
            actual_rows["source"] = "Actual"
            actual_rows["projected_points"] = actual_rows["actual_points"]
            actual_rows["season_label"] = actual_rows["season_id"].map(
                lambda x: f"{x // 1000}-{x % 1000}"
            )
            chart_data = pd.concat([chart_data, actual_rows], ignore_index=True)

            player_chart = (
                alt.Chart(chart_data)
                .mark_bar(cornerRadiusEnd=3)
                .encode(
                    x=alt.X("season_label:N", title="Season"),
                    y=alt.Y("projected_points:Q", title="Points"),
                    xOffset=alt.XOffset("source:N"),
                    color=alt.Color(
                        "source:N",
                        scale=alt.Scale(
                            domain=list(SOURCE_COLORS.keys()) + ["Actual"],
                            range=list(SOURCE_COLORS.values()) + [COLORS["gray"]],
                        ),
                    ),
                    tooltip=[
                        alt.Tooltip("source:N"),
                        alt.Tooltip("projected_points:Q", title="Points"),
                    ],
                )
                .configure_view(stroke=None)
                .properties(height=350)
            )
            st.altair_chart(player_chart, use_container_width=True)

            # Table
            st.divider()
            st.dataframe(
                pivot.reset_index(),
                use_container_width=True,
                hide_index=True,
            )

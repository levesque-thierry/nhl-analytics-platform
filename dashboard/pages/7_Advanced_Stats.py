"""
7_Advanced_Stats.py — Advanced Possession Statistics (Corsi/Fenwick)

Visualizes on-ice possession metrics computed from play-by-play + shift data:
    1. Player Leaderboard — sortable Corsi/Fenwick rankings
    2. Team Overview — team-level possession aggregation
    3. Player Deep Dive — single player Corsi/Fenwick breakdown
    4. Distribution Charts — histogram of CF% across the league
"""

import sqlite3
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "5_advanced_stats"))
sys.path.insert(0, str(PROJECT_ROOT / "1_data_warehouse"))

from db import get_connection, query_df

st.set_page_config(
    page_title="Advanced Stats — NHL Analytics",
    page_icon="📊",
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
def load_corsi_data(min_gp: int = 10) -> pd.DataFrame:
    """Load pre-computed Corsi/Fenwick from DB, or compute on the fly."""
    from corsi_fenwick import compute_player_corsi
    conn = get_connection()
    try:
        df = compute_player_corsi(conn)
    finally:
        conn.close()
    if df.empty:
        return df
    return df[df["gp"] >= min_gp].copy()


def load_game_corsi(game_id: int) -> pd.DataFrame:
    """Load Corsi/Fenwick for a single game."""
    import numpy as np
    from corsi_fenwick import compute_game_corsi

    conn = get_connection()
    try:
        pbp = pd.read_sql_query(
            "SELECT * FROM play_by_play WHERE game_id = ?", conn, params=(int(game_id),)
        )
        shifts = pd.read_sql_query(
            "SELECT * FROM player_shifts WHERE game_id = ?", conn, params=(int(game_id),)
        )
    finally:
        conn.close()

    if pbp.empty or shifts.empty:
        return pd.DataFrame()

    result = compute_game_corsi(pbp, shifts, "", "", "1551")
    if not result:
        return pd.DataFrame()

    rows = []
    for pid, stats in result.items():
        rows.append({
            "player_id": pid,
            "team": stats["team"],
            "cf": stats["cf"],
            "ca": stats["ca"],
            "cf_pct": stats["cf_pct"],
            "ff": stats["ff"],
            "fa": stats["fa"],
            "ff_pct": stats["ff_pct"],
            "toi_seconds": stats["toi_seconds"],
        })
    return pd.DataFrame(rows)


def load_db_stats() -> dict:
    """Load basic counts from the database."""
    pbp_count = query_df("SELECT COUNT(*) as cnt FROM play_by_play")["cnt"].iloc[0]
    shift_count = query_df("SELECT COUNT(*) as cnt FROM player_shifts")["cnt"].iloc[0]
    game_count = query_df("SELECT COUNT(DISTINCT game_id) as cnt FROM play_by_play")["cnt"].iloc[0]
    return {"pbp_events": pbp_count, "shifts": shift_count, "games": game_count}


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("Filters")
    min_gp = st.slider("Min Games Played", 1, 82, 10)
    position_filter = st.multiselect(
        "Positions",
        ["C", "L", "R", "D"],
        default=["C", "L", "R", "D"],
    )

# ---------------------------------------------------------------------------
# Page Header
# ---------------------------------------------------------------------------

st.title("Advanced Possession Statistics")
st.caption("Corsi & Fenwick metrics computed from play-by-play and shift data (5v5 only)")

db_stats = load_db_stats()
c1, c2, c3 = st.columns(3)
c1.metric("PBP Events", f"{db_stats['pbp_events']:,}")
c2.metric("Shift Records", f"{db_stats['shifts']:,}")
c3.metric("Games with PBP", f"{db_stats['games']:,}")

st.divider()

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab1, tab2, tab3, tab4 = st.tabs([
    "Player Leaderboard",
    "Team Overview",
    "Player Deep Dive",
    "Distribution",
])

# --- Tab 1: Player Leaderboard ---
with tab1:
    st.subheader("Player Corsi/Fenwick Leaderboard")

    df = load_corsi_data(min_gp)
    if df.empty:
        st.info("No Corsi/Fenwick data available. Run `python 5_advanced_stats/ingest_pbp.py` and `ingest_shifts.py` first.")
    else:
        # Load player info for position filter
        players = query_df("SELECT id, first_name, last_name, position FROM players")
        df = df.merge(
            players.rename(columns={"id": "player_id"}),
            on="player_id", how="left", suffixes=("", "_info"),
        )
        if "position_info" in df.columns:
            df = df[df["position_info"].isin(position_filter)]

        # Display metrics
        display_df = df[[
            "player_name", "team", "gp", "cf", "ca", "cf_pct",
            "ff", "fa", "ff_pct", "toi_seconds", "cf_per_60", "ff_per_60",
        ]].copy()
        display_df["toi_min"] = (display_df["toi_seconds"] / 60).round(1)
        display_df = display_df.sort_values("cf_pct", ascending=False)
        display_df = display_df.drop(columns=["toi_seconds"])

        st.dataframe(
            display_df.style.format({
                "cf_pct": "{:.1f}%",
                "ff_pct": "{:.1f}%",
                "cf_per_60": "{:.2f}",
                "ff_per_60": "{:.2f}",
                "toi_min": "{:.1f}",
            }),
            use_container_width=True,
            height=600,
        )

# --- Tab 2: Team Overview ---
with tab2:
    st.subheader("Team-Level Possession Metrics")

    df = load_corsi_data(min_gp)
    if df.empty:
        st.info("No data available.")
    else:
        team_df = df.groupby("team").agg({
            "cf": "sum",
            "ca": "sum",
            "ff": "sum",
            "fa": "sum",
            "toi_seconds": "sum",
            "gp": "mean",
            "player_id": "count",
        }).rename(columns={"player_id": "players_count"}).reset_index()

        team_df["cf_pct"] = (team_df["cf"] / (team_df["cf"] + team_df["ca"]) * 100).round(1)
        team_df["ff_pct"] = (team_df["ff"] / (team_df["ff"] + team_df["fa"]) * 100).round(1)
        team_df["gp"] = team_df["gp"].round(0).astype(int)

        team_df = team_df.sort_values("cf_pct", ascending=False)

        # Bar chart
        chart = alt.Chart(team_df).mark_bar().encode(
            x=alt.X("team:N", sort="-y", title="Team"),
            y=alt.Y("cf_pct:Q", title="Corsi For %", scale=alt.Scale(domain=[40, 60])),
            color=alt.Color(
                "cf_pct:Q",
                scale=alt.Scale(scheme="redyellowgreen", domain=[42, 58]),
                legend=None,
            ),
            tooltip=["team", "cf_pct", "ff_pct", "gp"],
        ).properties(width=800, height=400)

        # Reference line at 50%
        rule = alt.Chart(pd.DataFrame({"y": [50]})).mark_rule(
            strokeDash=[4, 4], color="gray"
        ).encode(y="y:Q")

        st.altair_chart(chart + rule, use_container_width=True)

        # Table
        st.dataframe(
            team_df[["team", "gp", "players_count", "cf", "ca", "cf_pct", "ff", "fa", "ff_pct"]].style.format({
                "cf_pct": "{:.1f}%",
                "ff_pct": "{:.1f}%",
            }),
            use_container_width=True,
        )

# --- Tab 3: Player Deep Dive ---
with tab3:
    st.subheader("Player Deep Dive")

    df = load_corsi_data(min_gp)
    if df.empty:
        st.info("No data available.")
    else:
        player_names = sorted(df["player_name"].unique())
        selected = st.selectbox("Select Player", player_names)

        if selected:
            player_row = df[df["player_name"] == selected].iloc[0]

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("CF%", f"{player_row['cf_pct']:.1f}%")
            c2.metric("FF%", f"{player_row['ff_pct']:.1f}%")
            c3.metric("CF/60", f"{player_row['cf_per_60']:.2f}")
            c4.metric("FF/60", f"{player_row['ff_per_60']:.2f}")

            st.caption(
                f"Team: {player_row['team']}  |  "
                f"GP: {player_row['gp']}  |  "
                f"CF: {player_row['cf']}  |  CA: {player_row['ca']}  |  "
                f"FF: {player_row['ff']}  |  FA: {player_row['fa']}  |  "
                f"TOI: {player_row['toi_seconds']/60:.0f} min"
            )

            # Comparison bar: CF vs CA
            bar_data = pd.DataFrame({
                "Event": ["Corsi For", "Corsi Against", "Fenwick For", "Fenwick Against"],
                "Count": [player_row["cf"], player_row["ca"], player_row["ff"], player_row["fa"]],
                "Type": ["Corsi", "Corsi", "Fenwick", "Fenwick"],
            })

            bar_chart = alt.Chart(bar_data).mark_bar().encode(
                x=alt.X("Event:N", sort=None),
                y=alt.Y("Count:Q"),
                color=alt.Color("Type:N", scale=alt.Scale(domain=["Corsi", "Fenwick"], range=[COLORS["dark_blue"], COLORS["red"]])),
            ).properties(width=400, height=300)
            st.altair_chart(bar_chart, use_container_width=True)

# --- Tab 4: Distribution ---
with tab4:
    st.subheader("League CF% Distribution")

    df = load_corsi_data(min_gp)
    if df.empty:
        st.info("No data available.")
    else:
        hist = alt.Chart(df).mark_bar(opacity=0.7).encode(
            alt.X("cf_pct:Q", bin=alt.Bin(maxbins=30), title="Corsi For %"),
            alt.Y("count()", title="Players"),
            color=alt.value(COLORS["dark_blue"]),
        ).properties(width=800, height=400)

        # Reference line at 50%
        rule = alt.Chart(pd.DataFrame({"x": [50]})).mark_rule(
            strokeDash=[4, 4], color=COLORS["red"]
        ).encode(alt.X("x:Q"))

        st.altair_chart(hist + rule, use_container_width=True)

        # Fenwick distribution
        hist_ff = alt.Chart(df).mark_bar(opacity=0.7).encode(
            alt.X("ff_pct:Q", bin=alt.Bin(maxbins=30), title="Fenwick For %"),
            alt.Y("count()", title="Players"),
            color=alt.value(COLORS["gold"]),
        ).properties(width=800, height=400)

        st.altair_chart(hist_ff + rule, use_container_width=True)

        # Summary stats
        c1, c2, c3 = st.columns(3)
        c1.metric("Mean CF%", f"{df['cf_pct'].mean():.1f}%")
        c2.metric("Median CF%", f"{df['cf_pct'].median():.1f}%")
        c3.metric("Std CF%", f"{df['cf_pct'].std():.1f}%")

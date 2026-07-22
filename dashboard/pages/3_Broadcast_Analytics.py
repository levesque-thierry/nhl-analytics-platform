"""
3_Broadcast_Analytics.py — Broadcast Analytics Dashboard

Four-tab visualization of the Phase 2 StreakAnomaly engine:
    1. Anomaly Leaderboard — ranked active streaks with filters
    2. Player Deep Dive — per-player streak analysis with rarity breakdown
    3. Baselines Explorer — historical frequency heatmaps, curves, team comparison
    4. Streak Type Distribution — aggregate anomaly stats by type and severity
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Path setup for broadcast engine imports
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "2_broadcast_engine"))
sys.path.insert(0, str(PROJECT_ROOT / "1_data_warehouse"))

from baselines import STREAK_TYPES, load_baselines  # noqa: E402
from streak_engine import (  # noqa: E402
    evaluate_all_players,
    evaluate_streaks,
    fetch_player_game_logs,
    fetch_player_info,
)

from db import get_connection, query_df, table_count  # noqa: E402

BASELINES_PATH = str(PROJECT_ROOT / "2_broadcast_engine" / "baselines_cache.json")

st.set_page_config(
    page_title="Broadcast Analytics — NHL Analytics",
    page_icon="📡",
    layout="wide",
)

# ---------------------------------------------------------------------------
# NHL color palette
# ---------------------------------------------------------------------------
COLORS = {
    "dark_blue": "#003087",
    "red": "#C8102E",
    "white": "#FFFFFF",
    "light_gray": "#F5F5F5",
    "mid_gray": "#999999",
    "extremely_rare": "#B91C1C",
    "very_rare": "#EA580C",
    "rare": "#D97706",
    "uncommon": "#2563EB",
    "common": "#6B7280",
}

SEVERITY_ORDER = ["EXTREMELY RARE", "VERY RARE", "RARE", "UNCOMMON", "COMMON"]
SEVERITY_COLORS = {
    "EXTREMELY RARE": COLORS["extremely_rare"],
    "VERY RARE": COLORS["very_rare"],
    "RARE": COLORS["rare"],
    "UNCOMMON": COLORS["uncommon"],
    "COMMON": COLORS["common"],
}

STREAK_LABELS = {
    "point_streak": "Point Streak (1+ pts)",
    "goal_streak": "Goal Streak (1+ G)",
    "multi_point": "Multi-Point (2+ pts)",
    "hat_trick": "Hat Trick (3+ G)",
    "takeaway_streak": "Takeaway Streak (1+ TK)",
    "scoreless_drought": "Scoreless Drought (0 G)",
    "pointless_drought": "Pointless Drought (0 pts)",
    "shot_drought": "Shot Drought (<=1 SOG)",
    "minus_streak": "Minus Streak (-/-)",
}

SIGN_COLORS = {"positive": COLORS["dark_blue"], "negative": COLORS["red"]}


# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600)
def load_baselines_cached() -> dict:
    """Load baselines from JSON cache."""
    return load_baselines(BASELINES_PATH)


@st.cache_data(ttl=3600)
def load_anomalies(season: str, min_novelty: float) -> list[dict]:
    """Run full anomaly evaluation for a season. Returns list of dicts."""
    anomalies = evaluate_all_players(
        season=season,
        min_novelty=min_novelty,
        min_streak_length=2,
        min_games=10,
    )
    return [a.to_dict() for a in anomalies]


@st.cache_data(ttl=3600)
def load_player_anomalies(player_id: int, season: str) -> list[dict]:
    """Run anomaly evaluation for a single player."""
    conn = get_connection()
    try:
        info = fetch_player_info(conn, player_id)
        if info is None:
            return []
        games = fetch_player_game_logs(conn, player_id, season=season, limit=40)
        baselines = load_baselines(BASELINES_PATH)
        anomalies = evaluate_streaks(
            player_id=player_id,
            player_name=f"{info['first_name']} {info['last_name']}",
            team=info["team"],
            position=info["position"],
            season=season,
            game_logs=games,
            baselines=baselines,
            min_length=2,
        )
        return [a.to_dict() for a in anomalies]
    finally:
        conn.close()


def get_season_options() -> list[str]:
    """Get distinct seasons from the database."""
    df = query_df("SELECT DISTINCT season FROM player_game_logs ORDER BY season DESC")
    return df["season"].tolist()


def get_team_options() -> list[str]:
    """Get distinct team abbreviations from the database."""
    df = query_df("SELECT DISTINCT team_abbr FROM player_game_logs ORDER BY team_abbr")
    return df["team_abbr"].tolist()


# ---------------------------------------------------------------------------
# Page title
# ---------------------------------------------------------------------------

st.title("📡 Broadcast Analytics")
st.caption("StreakAnomaly engine — real-time detection of rare statistical streaks across the NHL.")

# ---------------------------------------------------------------------------
# Tab layout
# ---------------------------------------------------------------------------

tab_leaderboard, tab_player, tab_baselines, tab_distribution = st.tabs([
    "🏆 Anomaly Leaderboard",
    "🔍 Player Deep Dive",
    "📊 Baselines Explorer",
    "📈 Streak Type Distribution",
])


# ===========================================================================
# TAB 1: ANOMALY LEADERBOARD
# ===========================================================================

with tab_leaderboard:
    # --- Filter row ---
    f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns(5)

    with f_col1:
        season_options = get_season_options()
        selected_season = st.selectbox("Season", season_options, key="lb_season")

    with f_col2:
        min_novelty = st.slider("Min Novelty Index", 0.0, 1.0, 0.5, 0.05, key="lb_novelty")

    with f_col3:
        all_streak_names = list(STREAK_TYPES.keys())
        selected_types = st.multiselect(
            "Streak Type",
            all_streak_names,
            default=[],
            format_func=lambda x: STREAK_LABELS.get(x, x),
            key="lb_types",
        )

    with f_col4:
        team_options = get_team_options()
        selected_teams = st.multiselect("Team", team_options, default=[], key="lb_teams")

    with f_col5:
        pos_group = st.radio(
            "Position",
            ["ALL", "F", "D"],
            horizontal=True,
            key="lb_pos",
        )

    # --- Load data ---
    anomalies_raw = load_anomalies(selected_season, 0.0)  # load all, filter below

    # --- In-memory filtering ---
    filtered = anomalies_raw
    if min_novelty > 0:
        filtered = [a for a in filtered if a["novelty_index"] >= min_novelty]
    if selected_types:
        filtered = [a for a in filtered if a["streak"]["type"] in selected_types]
    if selected_teams:
        filtered = [a for a in filtered if a["team"] in selected_teams]
    if pos_group != "ALL":
        filtered = [a for a in filtered if a["position"] == (pos_group if pos_group != "ALL" else a["position"])]

    # --- KPI cards ---
    k1, k2, k3, k4 = st.columns(4)
    extremely = sum(1 for a in filtered if a["severity"] == "EXTREMELY RARE")
    very = sum(1 for a in filtered if a["severity"] == "VERY RARE")
    avg_ni = sum(a["novelty_index"] for a in filtered) / len(filtered) if filtered else 0.0

    k1.metric("Total Anomalies", len(filtered))
    k2.metric("Extremely Rare", extremely, delta=None)
    k3.metric("Very Rare", very, delta=None)
    k4.metric("Avg Novelty Index", f"{avg_ni:.3f}")

    st.divider()

    # --- Main table ---
    if not filtered:
        st.info("No anomalies match the current filters.")
    else:
        table_data = []
        for a in filtered:
            table_data.append({
                "Player": a["player_name"],
                "Team": a["team"],
                "Pos": a["position"],
                "Streak": STREAK_LABELS.get(a["streak"]["type"], a["streak"]["type"]),
                "Length": a["streak"]["length"],
                "Start": a["streak"]["start_date"],
                "End": a["streak"]["end_date"],
                "Novelty Index": a["novelty_index"],
                "Severity": a["severity"],
            })

        df = pd.DataFrame(table_data)
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Novelty Index": st.column_config.ProgressColumn(
                    "Novelty Index",
                    min_value=0.0,
                    max_value=1.0,
                    format="%.3f",
                ),
                "Length": st.column_config.NumberColumn("Length", format="%d"),
            },
        )


# ===========================================================================
# TAB 2: PLAYER DEEP DIVE
# ===========================================================================

with tab_player:
    p_col1, p_col2 = st.columns([1, 2])

    with p_col1:
        st.subheader("Player Search")
        search = st.text_input("Search by name", placeholder="e.g. McDavid", key="pd_search")
        player_id = None
        player_info = None

        if search:
            results = query_df("""
                SELECT
                    p.id,
                    p.first_name || ' ' || p.last_name AS Name,
                    p.position AS Pos,
                    v.current_team AS Team,
                    p.sweater_number AS "#"
                FROM players p
                LEFT JOIN v_player_current_team v ON p.id = v.player_id
                WHERE (p.first_name || ' ' || p.last_name) LIKE ?
                ORDER BY p.last_name, p.first_name
                LIMIT 20
            """, (f"%{search}%",))

            if results.empty:
                st.info("No players found.")
            else:
                selected = st.selectbox(
                    "Select player",
                    results["Name"].tolist(),
                    key="pd_select",
                )
                row = results[results["Name"] == selected].iloc[0]
                player_id = int(row["id"])

                st.markdown(f"**{row['Name']}**")
                c1, c2, c3 = st.columns(3)
                c1.caption(f"Team: **{row['Team']}**")
                c2.caption(f"Pos: **{row['Pos']}**")
                c3.caption(f"#: **{row['#']}**")

                season_opts = get_season_options()
                pd_season = st.selectbox("Season", season_opts, key="pd_season")
        else:
            st.info("Enter a player name to begin.")

    with p_col2:
        if player_id:
            anomalies = load_player_anomalies(player_id, pd_season)

            if not anomalies:
                st.success("No active streaks detected for this player.")
            else:
                st.subheader(f"Active Streaks ({len(anomalies)})")

                for a in anomalies:
                    severity = a["severity"]
                    color = SEVERITY_COLORS.get(severity, COLORS["common"])
                    streak = a["streak"]
                    ni = a["novelty_index"]

                    with st.container(border=True):
                        # Header row
                        st.markdown(
                            f'<span style="color:{color}; font-weight:bold; font-size:1.1em">'
                            f'{severity}</span>'
                            f' &mdash; <strong>{STREAK_LABELS.get(streak["type"], streak["type"])}</strong>'
                            f' &nbsp;: {streak["length"]} games'
                            f' <span style="color:#888">({streak["start_date"]} to {streak["end_date"]})</span>',
                            unsafe_allow_html=True,
                        )

                        # Rarity bars
                        rarity_data = []
                        for r in a["rarity_scores"]:
                            rarity_data.append({
                                "Level": r["level"].capitalize(),
                                "Rarity": r["rarity"],
                                "Probability": r["probability"],
                            })

                        rarity_df = pd.DataFrame(rarity_data)
                        bar_chart = (
                            alt.Chart(rarity_df)
                            .mark_bar(size=28, cornerRadiusEnd=4)
                            .encode(
                                y=alt.Y("Level:N", sort=["League", "Team", "Player"], title=None),
                                x=alt.X("Rarity:Q", title="Rarity Score", scale=alt.Scale(domain=[0, 1])),
                                color=alt.Color(
                                    "Level:N",
                                    scale=alt.Scale(
                                        domain=["League", "Team", "Player"],
                                        range=[COLORS["dark_blue"], COLORS["red"], "#D97706"],
                                    ),
                                    legend=None,
                                ),
                                tooltip=[
                                    alt.Tooltip("Level:N"),
                                    alt.Tooltip("Rarity:Q", format=".4f"),
                                    alt.Tooltip("Probability:Q", format=".4f", title="P(streak >= N)"),
                                ],
                            )
                            .configure_view(stroke=None)
                            .configure_axis(grid=False)
                            .properties(height=100, width=380)
                        )
                        st.altair_chart(bar_chart, use_container_width=False)

                        # Novelty index
                        mc1, mc2 = st.columns([1, 3])
                        mc1.metric("Novelty Index", f"{ni:.4f}")

                        # Expandable game log
                        games = streak.get("recent_games", [])
                        if games:
                            with st.expander(f"View {len(games)} streak games"):
                                game_df = pd.DataFrame(games)
                                display_cols = [
                                    c for c in [
                                        "game_date", "team_abbr", "opponent_abbr",
                                        "goals", "assists", "points", "shots",
                                        "plus_minus", "takeaways", "time_on_ice",
                                    ] if c in game_df.columns
                                ]
                                rename_map = {
                                    "game_date": "Date",
                                    "team_abbr": "Team",
                                    "opponent_abbr": "Opp",
                                    "goals": "G",
                                    "assists": "A",
                                    "points": "Pts",
                                    "shots": "SOG",
                                    "plus_minus": "+/-",
                                    "takeaways": "TK",
                                    "time_on_ice": "TOI",
                                }
                                game_df = game_df[display_cols].rename(columns=rename_map)
                                st.dataframe(game_df, use_container_width=True, hide_index=True)
        else:
            st.info("Search and select a player on the left to see their active streaks.")


# ===========================================================================
# TAB 3: BASELINES EXPLORER
# ===========================================================================

with tab_baselines:
    baselines = load_baselines_cached()

    st.subheader("Historical Streak Frequency Distributions")
    st.caption(
        "P(player-season has a streak of at least N games) computed from "
        f"{baselines['metadata']['total_player_seasons']['ALL']:,} player-seasons "
        f"across seasons {', '.join(baselines['metadata']['seasons'])}."
    )

    b_col1, b_col2 = st.columns([1, 3])

    with b_col1:
        b_pos = st.radio("Position Group", ["ALL", "F", "D"], key="b_pos")
        b_streak_types = st.multiselect(
            "Streak Types",
            list(STREAK_TYPES.keys()),
            default=["point_streak", "goal_streak"],
            format_func=lambda x: STREAK_LABELS.get(x, x),
            key="b_types",
        )
        b_team = st.selectbox(
            "Team (for comparison)",
            sorted(baselines["team"].keys()),
            index=sorted(baselines["team"].keys()).index("EDM"),
            key="b_team",
        )
        b_team_streak = st.selectbox(
            "Team Streak Type",
            list(STREAK_TYPES.keys()),
            index=0,
            format_func=lambda x: STREAK_LABELS.get(x, x),
            key="b_team_streak",
        )

    with b_col2:
        # --- Panel A: League Frequency Curves ---
        st.markdown("**League Frequency Curves**")

        if b_streak_types:
            curve_data = []
            for st_name in b_streak_types:
                freq = baselines.get("league", {}).get(b_pos, {}).get(st_name, {})
                for length_str, prob in freq.items():
                    curve_data.append({
                        "Streak Type": STREAK_LABELS.get(st_name, st_name),
                        "Length": int(length_str),
                        "Probability": prob,
                    })

            if curve_data:
                curve_df = pd.DataFrame(curve_data)
                curve_chart = (
                    alt.Chart(curve_df)
                    .mark_line(point=True, strokeWidth=2)
                    .encode(
                        x=alt.X("Length:Q", title="Streak Length (N)", scale=alt.Scale(type="log")),
                        y=alt.Y("Probability:Q", title="P(streak >= N)", scale=alt.Scale(domain=[0, 1])),
                        color=alt.Color(
                            "Streak Type:N",
                            scale=alt.Scale(scheme="category10"),
                        ),
                        tooltip=[
                            alt.Tooltip("Streak Type:N"),
                            alt.Tooltip("Length:Q", title="Length"),
                            alt.Tooltip("Probability:Q", format=".4f"),
                        ],
                    )
                    .configure_view(stroke=None)
                    .properties(height=350)
                )
                st.altair_chart(curve_chart, use_container_width=True)
            else:
                st.info("No data for selected streak types.")
        else:
            st.info("Select at least one streak type.")

    st.divider()

    # --- Panel B: Heatmap ---
    st.markdown("**Streak Frequency Heatmap**")
    st.caption("Darker = more probable. Each cell = P(player-season has streak >= N) for that type and length.")

    heatmap_data = []
    for st_name in STREAK_TYPES.keys():
        freq = baselines.get("league", {}).get(b_pos, {}).get(st_name, {})
        for length_str, prob in freq.items():
            length = int(length_str)
            # Bucket long droughts for readability
            if length > 20:
                bucket = f"{(length // 5) * 5 + 1}-{(length // 5) * 5 + 5}"
            else:
                bucket = str(length)
            heatmap_data.append({
                "Streak Type": STREAK_LABELS.get(st_name, st_name),
                "Length": length,
                "Length Bucket": bucket,
                "Probability": prob,
                "Sort Key": list(STREAK_TYPES.keys()).index(st_name),
            })

    if heatmap_data:
        heat_df = pd.DataFrame(heatmap_data)
        # Sort streak types by their original order
        type_order = [STREAK_LABELS[k] for k in STREAK_TYPES.keys()]
        heat_chart = (
            alt.Chart(heat_df)
            .mark_rect(stroke="white", strokeWidth=0.5)
            .encode(
                x=alt.X(
                    "Length:Q",
                    title="Streak Length (N)",
                    scale=alt.Scale(type="log"),
                ),
                y=alt.Y(
                    "Streak Type:N",
                    title=None,
                    sort=type_order,
                ),
                color=alt.Color(
                    "Probability:Q",
                    scale=alt.Scale(scheme="blues", domain=[0, 1]),
                    title="P(>=N)",
                ),
                tooltip=[
                    alt.Tooltip("Streak Type:N"),
                    alt.Tooltip("Length:Q", title="Length"),
                    alt.Tooltip("Probability:Q", format=".4f", title="Probability"),
                ],
            )
            .configure_view(stroke=None)
            .properties(height=280)
        )
        st.altair_chart(heat_chart, use_container_width=True)

    st.divider()

    # --- Panel C: Team vs League Comparison ---
    st.markdown(f"**Team vs League: {b_team}**")

    comp_st_name = b_team_streak
    league_freq = baselines.get("league", {}).get(b_pos, {}).get(comp_st_name, {})
    team_freq = baselines.get("team", {}).get(b_team, {}).get(b_pos, {}).get(comp_st_name, {})

    comp_data = []
    all_lengths = sorted(set(list(league_freq.keys()) + list(team_freq.keys())), key=lambda x: int(x))
    for length_str in all_lengths:
        length = int(length_str)
        comp_data.append({
            "Length": length,
            "League": league_freq.get(length_str, 0.0),
            b_team: team_freq.get(length_str, 0.0),
        })

    if comp_data:
        comp_df = pd.DataFrame(comp_data)

        # League line as dashed, team as solid
        league_line = (
            alt.Chart(comp_df)
            .mark_line(strokeWidth=2, strokeDash=[6, 4], color=COLORS["mid_gray"])
            .encode(
                x=alt.X("Length:Q", scale=alt.Scale(type="log")),
                y=alt.Y("League:Q", scale=alt.Scale(domain=[0, 1])),
            )
        )
        team_line = (
            alt.Chart(comp_df)
            .mark_line(strokeWidth=2.5, color=COLORS["dark_blue"])
            .encode(
                x=alt.X("Length:Q", scale=alt.Scale(type="log")),
                y=alt.Y(f"{b_team}:Q", scale=alt.Scale(domain=[0, 1])),
            )
        )
        combined_chart = (league_line + team_line).configure_view(stroke=None).properties(height=300)
        st.altair_chart(combined_chart, use_container_width=True)

        legend_html = (
            f'<span style="color:{COLORS["mid_gray"]}">&#9644;&#9644; League</span>'
            f' &nbsp;&nbsp; '
            f'<span style="color:{COLORS["dark_blue"]}">&#9644;&#9644; {b_team}</span>'
        )
        st.markdown(legend_html, unsafe_allow_html=True)
    else:
        st.info("No comparison data available.")

    st.divider()

    # --- Panel D: Player Career Max Distribution ---
    st.markdown("**Player Career-Best Streak Length Distribution**")

    p_dist_type = st.selectbox(
        "Streak Type",
        list(STREAK_TYPES.keys()),
        index=0,
        format_func=lambda x: STREAK_LABELS.get(x, x),
        key="p_dist_type",
    )

    player_baselines = baselines.get("player", {})
    career_maxes = []
    for pid, streaks in player_baselines.items():
        max_len = streaks.get(p_dist_type, 0)
        if max_len > 0:
            career_maxes.append({"Player ID": int(pid), "Career Best": max_len})

    if career_maxes:
        dist_df = pd.DataFrame(career_maxes)
        dist_chart = (
            alt.Chart(dist_df)
            .mark_bar(color=COLORS["dark_blue"], opacity=0.8, cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
            .encode(
                x=alt.X(
                    "Career Best:Q",
                    title="Career Best Streak Length",
                    bin=alt.Bin(maxbins=20),
                ),
                y=alt.Y("count():Q", title="Number of Players"),
                tooltip=[
                    alt.Tooltip("Career Best:Q", bin=True, title="Career Best Length"),
                    alt.Tooltip("count():Q", title="Players"),
                ],
            )
            .configure_view(stroke=None)
            .properties(height=280)
        )
        st.altair_chart(dist_chart, use_container_width=True)
        st.caption(
            f"Based on {len(career_maxes)} players with at least one {STREAK_LABELS.get(p_dist_type, p_dist_type)} "
            f"in their career. Median: {dist_df['Career Best'].median():.0f} games, "
            f"Max: {dist_df['Career Best'].max()} games."
        )
    else:
        st.info("No career data available for this streak type.")


# ===========================================================================
# TAB 4: STREAK TYPE DISTRIBUTION
# ===========================================================================

with tab_distribution:
    st.subheader("Anomaly Distribution by Streak Type")

    dist_season = st.selectbox("Season", get_season_options(), key="dist_season")
    dist_novelty = st.slider("Min Novelty", 0.0, 1.0, 0.3, 0.05, key="dist_novelty")

    dist_anomalies = load_anomalies(dist_season, 0.0)
    dist_filtered = [a for a in dist_anomalies if a["novelty_index"] >= dist_novelty]

    if not dist_filtered:
        st.info("No anomalies found for this configuration.")
    else:
        # --- Panel A: Count by streak type ---
        d_col1, d_col2 = st.columns(2)

        with d_col1:
            st.markdown("**Anomalies by Streak Type**")
            type_counts = defaultdict(lambda: {"positive": 0, "negative": 0})
            for a in dist_filtered:
                st_type = a["streak"]["type"]
                sign = a["streak"]["sign"]
                type_counts[st_type][sign] += 1

            bar_data = []
            for st_name in STREAK_TYPES.keys():
                counts = type_counts.get(st_name, {"positive": 0, "negative": 0})
                for sign in ["positive", "negative"]:
                    if counts[sign] > 0:
                        bar_data.append({
                            "Streak Type": STREAK_LABELS.get(st_name, st_name),
                            "Sign": sign.capitalize(),
                            "Count": counts[sign],
                        })

            if bar_data:
                bar_df = pd.DataFrame(bar_data)
                bar_chart = (
                    alt.Chart(bar_df)
                    .mark_bar(cornerRadiusEnd=4)
                    .encode(
                        y=alt.Y("Streak Type:N", sort="-x", title=None),
                        x=alt.X("Count:Q", title="Number of Anomalies"),
                        color=alt.Color(
                            "Sign:N",
                            scale=alt.Scale(
                                domain=["Positive", "Negative"],
                                range=[COLORS["dark_blue"], COLORS["red"]],
                            ),
                        ),
                        tooltip=[
                            alt.Tooltip("Streak Type:N"),
                            alt.Tooltip("Sign:N"),
                            alt.Tooltip("Count:Q"),
                        ],
                    )
                    .configure_view(stroke=None)
                    .properties(height=300)
                )
                st.altair_chart(bar_chart, use_container_width=True)

        with d_col2:
            st.markdown("**Severity Distribution**")
            sev_counts = defaultdict(int)
            for a in dist_filtered:
                sev_counts[a["severity"]] += 1

            sev_data = []
            for sev in SEVERITY_ORDER:
                if sev_counts[sev] > 0:
                    sev_data.append({"Severity": sev, "Count": sev_counts[sev]})

            if sev_data:
                sev_df = pd.DataFrame(sev_data)
                sev_chart = (
                    alt.Chart(sev_df)
                    .mark_arc(innerRadius=50, outerRadius=90)
                    .encode(
                        theta=alt.Theta("Count:Q"),
                        color=alt.Color(
                            "Severity:N",
                            scale=alt.Scale(
                                domain=[d["Severity"] for d in sev_data],
                                range=[SEVERITY_COLORS[d["Severity"]] for d in sev_data],
                            ),
                            legend=alt.Legend(title=None),
                        ),
                        tooltip=[
                            alt.Tooltip("Severity:N"),
                            alt.Tooltip("Count:Q"),
                        ],
                    )
                    .configure_view(stroke=None)
                    .properties(height=300)
                )
                st.altair_chart(sev_chart, use_container_width=True)

        st.divider()

        # --- Panel C: Summary table ---
        st.markdown("**Streak Type Summary**")
        summary_rows = []
        for st_name in STREAK_TYPES.keys():
            st_filtered = [a for a in dist_filtered if a["streak"]["type"] == st_name]
            if not st_filtered:
                continue
            ni_vals = [a["novelty_index"] for a in st_filtered]
            lengths = [a["streak"]["length"] for a in st_filtered]
            teams_list = [a["team"] for a in st_filtered]
            most_common_team = Counter(teams_list).most_common(1)[0][0] if teams_list else "N/A"

            summary_rows.append({
                "Streak Type": STREAK_LABELS.get(st_name, st_name),
                "Sign": STREAK_TYPES[st_name].sign.capitalize(),
                "Count": len(st_filtered),
                "Avg Novelty": f"{sum(ni_vals) / len(ni_vals):.3f}",
                "Max Length": max(lengths),
                "Top Team": most_common_team,
            })

        if summary_rows:
            summary_df = pd.DataFrame(summary_rows)
            st.dataframe(summary_df, use_container_width=True, hide_index=True)

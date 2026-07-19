"""
DB Summary — Explore the NHL Data Warehouse

Three tabs:
    1. Overview — Table stats, team coverage, date range
    2. Player Explorer — Search and inspect individual players
    3. Game Logs — Filterable raw data with charts
"""

import streamlit as st

from db import query_df, table_count

st.set_page_config(page_title="DB Summary — NHL Analytics", page_icon="🏒", layout="wide")
st.title("📊 DB Summary")

# ── Tab layout ──────────────────────────────────────────────────────────────

tab_overview, tab_players, tab_logs = st.tabs(["Overview", "Player Explorer", "Game Logs"])


# ── Tab 1: Overview ─────────────────────────────────────────────────────────

with tab_overview:
    st.subheader("Database Overview")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Players", table_count("players"))
    col2.metric("Total Game Logs", table_count("player_game_logs"))

    teams_df = query_df("SELECT DISTINCT current_team FROM v_player_current_team ORDER BY current_team")
    col3.metric("Teams Represented", len(teams_df))

    seasons_df = query_df("SELECT DISTINCT season FROM player_game_logs ORDER BY season")
    col4.metric("Seasons", len(seasons_df))

    col5, col6, col7, col8 = st.columns(4)
    game_types = query_df("""
        SELECT
            CASE game_type
                WHEN 2 THEN 'Regular Season'
                WHEN 3 THEN 'Playoffs'
                ELSE 'Other (' || game_type || ')'
            END AS label,
            COUNT(*) as cnt
        FROM player_game_logs
        GROUP BY game_type
        ORDER BY game_type
    """)
    if not game_types.empty:
        for _, row in game_types.iterrows():
            col5.metric(row["label"], f'{row["cnt"]:,}')
    col6.metric("Team Seasons", table_count("player_team_seasons"))

    date_range = query_df("SELECT MIN(game_date) as min_date, MAX(game_date) as max_date FROM player_game_logs")
    if not date_range.empty and date_range["min_date"].iloc[0]:
        col7.metric("Date Range", f"{date_range['min_date'].iloc[0]} → {date_range['max_date'].iloc[0]}")
    else:
        col7.metric("Date Range", "No data")

    st.divider()

    st.subheader("Players by Team")
    team_counts = query_df("""
        SELECT current_team AS Team, COUNT(*) as Players
        FROM v_player_current_team
        GROUP BY current_team
        ORDER BY Players DESC
    """)
    if not team_counts.empty:
        st.bar_chart(team_counts.set_index("Team"))

    st.divider()

    st.subheader("Top Scorers (All Time in DB)")
    top_scorers = query_df("""
        SELECT
            p.first_name || ' ' || p.last_name AS Player,
            v.current_team AS Team,
            SUM(g.goals) AS Goals,
            SUM(g.assists) AS Assists,
            SUM(g.points) AS Points,
            COUNT(*) AS Games
        FROM player_game_logs g
        JOIN players p ON g.player_id = p.id
        LEFT JOIN v_player_current_team v ON p.id = v.player_id
        GROUP BY g.player_id
        ORDER BY Points DESC
        LIMIT 20
    """)
    if not top_scorers.empty:
        st.dataframe(top_scorers, use_container_width=True)

    st.divider()

    st.subheader("Boxscore Stats Coverage")
    boxscore_stats = query_df("""
        SELECT
            SUM(CASE WHEN blocked_shots > 0 THEN 1 ELSE 0 END) AS "With Blocked Shots",
            SUM(CASE WHEN giveaways > 0 THEN 1 ELSE 0 END) AS "With Giveaways",
            SUM(CASE WHEN takeaways > 0 THEN 1 ELSE 0 END) AS "With Takeaways",
            SUM(CASE WHEN faceoff_pct IS NOT NULL THEN 1 ELSE 0 END) AS "With Faceoff %",
            COUNT(*) AS "Total Records"
        FROM player_game_logs
    """)
    if not boxscore_stats.empty:
        st.dataframe(boxscore_stats, use_container_width=True, hide_index=True)


# ── Tab 2: Player Explorer ──────────────────────────────────────────────────

with tab_players:
    st.subheader("Player Explorer")

    search = st.text_input("Search by name", placeholder="e.g. McDavid")

    if search:
        players_df = query_df("""
            SELECT
                p.id,
                p.first_name || ' ' || p.last_name AS Name,
                p.position AS Pos,
                v.current_team AS Team,
                p.sweater_number AS '#',
                p.birth_date AS Born,
                p.shoots_catches AS Shoots
            FROM players p
            LEFT JOIN v_player_current_team v ON p.id = v.player_id
            WHERE (p.first_name || ' ' || p.last_name) LIKE ?
            ORDER BY p.last_name, p.first_name
        """, (f"%{search}%",))

        if players_df.empty:
            st.info("No players found matching that search.")
        else:
            st.dataframe(players_df, use_container_width=True, hide_index=True)

            # If single player found, show their stats
            if len(players_df) == 1:
                player_id = int(players_df["id"].iloc[0])
                player_name = players_df["Name"].iloc[0]
                st.divider()
                st.subheader(f"Season Totals — {player_name}")

                stats_df = query_df("""
                    SELECT
                        game_date AS Date,
                        team_abbr AS Team,
                        opponent_abbr AS Opp,
                        goals AS G,
                        assists AS A,
                        points AS Pts,
                        shots AS S,
                        pim AS PIM,
                        plus_minus AS "+/-",
                        time_on_ice AS TOI,
                        shifts AS Shifts,
                        blocked_shots AS BS,
                        giveaways AS GA,
                        takeaways AS TK,
                        faceoff_pct AS "FO%"
                    FROM player_game_logs
                    WHERE player_id = ?
                    ORDER BY game_date
                """, (player_id,))

                if not stats_df.empty:
                    # Summary metrics
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Games", len(stats_df))
                    c2.metric("Goals", int(stats_df["G"].sum()))
                    c3.metric("Assists", int(stats_df["A"].sum()))
                    c4.metric("Points", int(stats_df["Pts"].sum()))

                    # Points over time chart
                    st.line_chart(stats_df.set_index("Date")[["Pts"]])

                    # Raw game log
                    with st.expander("View all game logs"):
                        st.dataframe(stats_df, use_container_width=True, hide_index=True)
    else:
        st.info("Enter a player name to begin searching.")


# ── Tab 3: Game Logs ────────────────────────────────────────────────────────

with tab_logs:
    st.subheader("Game Log Explorer")

    col_filter1, col_filter2, col_filter3, col_filter4 = st.columns(4)
    with col_filter1:
        team_options = query_df("""
            SELECT DISTINCT team_abbr FROM player_game_logs ORDER BY team_abbr
        """)["team_abbr"].tolist()
        selected_teams = st.multiselect("Filter by team", team_options, default=[])

    with col_filter2:
        season_options = query_df("""
            SELECT DISTINCT season FROM player_game_logs ORDER BY season DESC
        """)["season"].tolist()
        selected_seasons = st.multiselect("Filter by season", season_options, default=[])

    with col_filter3:
        gt_options = query_df("""
            SELECT DISTINCT game_type FROM player_game_logs ORDER BY game_type
        """)["game_type"].tolist()
        gt_labels = {2: "Regular Season", 3: "Playoffs"}
        selected_gt = st.multiselect(
            "Game type",
            gt_options,
            default=[],
            format_func=lambda x: gt_labels.get(x, str(x)),
        )

    with col_filter4:
        date_range = query_df("SELECT MIN(game_date) as mn, MAX(game_date) as mx FROM player_game_logs")
        if not date_range.empty and date_range["mn"].iloc[0]:
            min_d = str(date_range["mn"].iloc[0])
            max_d = str(date_range["mx"].iloc[0])
            selected_dates = st.date_input(
                "Date range",
                value=(),
                min_value=min_d,
                max_value=max_d,
            )
        else:
            selected_dates = ()

    # Build query
    where_clauses: list[str] = []
    params: list[str] = []
    if selected_teams:
        placeholders = ",".join("?" for _ in selected_teams)
        where_clauses.append(f"g.team_abbr IN ({placeholders})")
        params.extend(selected_teams)
    if selected_seasons:
        placeholders = ",".join("?" for _ in selected_seasons)
        where_clauses.append(f"g.season IN ({placeholders})")
        params.extend(selected_seasons)
    if selected_gt:
        placeholders = ",".join("?" for _ in selected_gt)
        where_clauses.append(f"g.game_type IN ({placeholders})")
        params.extend(selected_gt)
    if len(selected_dates) == 2:
        where_clauses.append("g.game_date BETWEEN ? AND ?")
        params.append(str(selected_dates[0]))
        params.append(str(selected_dates[1]))

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    logs_df = query_df(f"""
        SELECT
            g.season AS Season,
            CASE g.game_type WHEN 2 THEN 'Reg' WHEN 3 THEN 'PO' ELSE g.game_type END AS Type,
            g.game_date AS Date,
            p.first_name || ' ' || p.last_name AS Player,
            g.team_abbr AS Team,
            g.opponent_abbr AS Opp,
            g.home_road_flag AS "H/R",
            g.goals AS G,
            g.assists AS A,
            g.points AS Pts,
            g.shots AS S,
            g.pim AS PIM,
            g.plus_minus AS "+/-",
            g.time_on_ice AS TOI,
            g.shifts AS Shifts,
            g.power_play_goals AS PPG,
            g.shorthanded_goals AS SHG,
            g.game_winning_goals AS GWG,
            g.blocked_shots AS BS,
            g.giveaways AS GA,
            g.takeaways AS TK,
            g.faceoff_pct AS "FO_pct"
        FROM player_game_logs g
        JOIN players p ON g.player_id = p.id
        {where_sql}
        ORDER BY g.game_date DESC, g.points DESC
        LIMIT 500
    """, tuple(params))

    if logs_df.empty:
        st.info("No game logs match the selected filters.")
    else:
        st.caption(f"Showing {len(logs_df)} records (max 500)")
        st.dataframe(logs_df, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Goals per Game (filtered)")

        # Aggregate by date for chart
        daily = logs_df.groupby("Date", as_index=False).agg({"G": "sum", "Pts": "sum"})
        st.line_chart(daily.set_index("Date")[["G", "Pts"]])

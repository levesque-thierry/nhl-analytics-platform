"""
Architecture — Platform Resources & Data Pipeline

Displays:
    1. Mermaid flowchart of the full data pipeline
    2. Database schema metadata (tables, views, columns, row counts)
    3. Pipeline scripts with CLI usage
    4. NHL API endpoint mapping (full reference from api_map.md)
"""

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from db import get_connection, query_df, table_count

st.set_page_config(page_title="Architecture — NHL Analytics", page_icon="🏒", layout="wide")
st.title("🏗️ Architecture")

# ── Mermaid Diagram ─────────────────────────────────────────────────────────

st.subheader("Data Pipeline")

MERMAID_CODE = """
graph LR
    api["NHL Web API<br/><i>api-web.nhle.com</i>"]
    db_setup["database_setup.py<br/><i>Schema + Migrations</i>"]
    roster["ingest_roster.py<br/><i>Team Rosters</i>"]
    boxscore["ingest_boxscore.py<br/><i>Schedule → Boxscore</i>"]
    historical["ingest_historical.py<br/><i>Player Game Logs (Legacy)</i>"]

    players[("players<br/><i>Biographical ref</i>")]
    pts[("player_team_seasons<br/><i>Season × Team</i>")]
    logs[("player_game_logs<br/><i>Per-game stats</i>")]
    view[("v_player_current_team<br/><i>Latest team view</i>")]

    dashboard["Streamlit Dashboard"]

    api -->|"GET /roster"| roster
    api -->|"GET /club-schedule-season"| boxscore
    api -->|"GET /gamecenter/.../boxscore"| boxscore
    api -->|"GET /player/.../game-log"| historical
    roster --> players
    roster --> pts
    boxscore --> logs
    historical -.->|"Legacy path"| logs
    db_setup --> players
    db_setup --> pts
    db_setup --> logs
    db_setup --> view
    players --> view
    pts --> view
    players --> dashboard
    view --> dashboard
    logs --> dashboard
"""

html_code = f"""
<!DOCTYPE html>
<html>
<head>
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <script>mermaid.initialize({{ startOnLoad: true, theme: 'default', securityLevel: 'loose' }});</script>
  <style>
    body {{ margin: 0; padding: 16px; background: white; }}
    .mermaid {{ display: flex; justify-content: center; }}
  </style>
</head>
<body>
  <div class="mermaid">
{MERMAID_CODE}
  </div>
</body>
</html>
"""

components.html(html_code, height=420, width=1200)

st.divider()

# ── Database Schema ─────────────────────────────────────────────────────────

st.subheader("Database Schema")

SCHEMA_DOCS: dict[str, str] = {
    "players": "Biographical reference for every player in the warehouse. No team affiliation — use the `v_player_current_team` view or `player_team_seasons` for that.",
    "player_team_seasons": "Season-aware team tracking. Composite PK `(player_id, season)` — one row per player per season per team.",
    "player_game_logs": "Per-game statistical records. PK `(game_id, player_id)`. Columns: goals, assists, points, shots, PIM, +/-, TOI, shifts, PP goals, SH goals, GWG, OT goals, blocked_shots, giveaways, takeaways, faceoff_pct. Populated via boxscore endpoint.",
    "v_player_current_team": "Dynamic view: resolves each player's latest season team from `player_team_seasons`. Columns: player_id, first_name, last_name, position, current_team, season.",
}

conn = get_connection()
tables: list[str] = [
    r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
]
views: list[str] = [
    r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'view' ORDER BY name"
    ).fetchall()
]
conn.close()

for obj_name in tables + views:
    is_view = obj_name in views
    icon = "👁️" if is_view else "📋"
    label = f"{icon} `{obj_name}`" + (" (view)" if is_view else "")

    with st.expander(label, expanded=False):
        # Row count
        if not is_view:
            cnt = table_count(obj_name)
            st.metric("Rows", f"{cnt:,}")
        else:
            st.caption("View — no direct row count (query the underlying tables)")

        # Column info
        cols_df = query_df(f"PRAGMA table_info({obj_name})")
        if not cols_df.empty:
            display_cols = cols_df[["name", "type", "notnull", "dflt_value", "pk"]].copy()
            display_cols.columns = ["Column", "Type", "NOT NULL", "Default", "PK"]
            display_cols["PK"] = display_cols["PK"].apply(lambda x: "🔑" if x else "")
            display_cols["NOT NULL"] = display_cols["NOT NULL"].apply(lambda x: "✓" if x else "")
            st.dataframe(display_cols, use_container_width=True, hide_index=True)

        # Indexes (tables only)
        if not is_view:
            idx_df = query_df(f"PRAGMA index_list({obj_name})")
            if not idx_df.empty:
                st.caption("Indexes")
                for _, idx_row in idx_df.iterrows():
                    idx_name = idx_row["name"]
                    idx_info = query_df(f"PRAGMA index_info({idx_name})")
                    cols = ", ".join(idx_info["name"].tolist())
                    st.code(f"{idx_name} ({cols})", language=None)

        # Description
        desc = SCHEMA_DOCS.get(obj_name, "")
        if desc:
            st.caption(desc)

st.divider()

# ── Pipeline Scripts ────────────────────────────────────────────────────────

st.subheader("Pipeline Scripts")

SCRIPTS: list[dict[str, str]] = [
    {
        "name": "database_setup.py",
        "desc": "Schema initialization, migrations, indexes, views. Idempotent — safe to run multiple times.",
        "usage": "python database_setup.py",
    },
    {
        "name": "ingest_roster.py",
        "desc": "Fetches team rosters from the NHL API → populates `players` + `player_team_seasons`.",
        "usage": (
            "python ingest_roster.py --season 20252026\n"
            "python ingest_roster.py --season 20252026 --teams EDM,TOR,FLA"
        ),
    },
    {
        "name": "ingest_boxscore.py",
        "desc": "Primary ingestion pipeline. Fetches schedules → boxscores per game → populates `player_game_logs`. ~1,300 API calls for a full season (vs ~13,000 with per-player approach).",
        "usage": (
            "python ingest_boxscore.py --season 20252026\n"
            "python ingest_boxscore.py --season 20252026 --game-type 2\n"
            "python ingest_boxscore.py --season 20252026 --teams EDM,TOR --skip-existing"
        ),
    },
    {
        "name": "ingest_historical.py",
        "desc": "Legacy per-player game log ingestion. Kept for targeted player-specific queries.",
        "usage": (
            "python ingest_historical.py --player-ids 8478402,8475789 --seasons 20252026\n"
            "python ingest_historical.py --player-ids 8478402 --seasons 20252026 --game-type 3\n"
            "python ingest_historical.py --player-ids 8478402 --seasons 20252026 --skip-existing"
        ),
    },
    {
        "name": "test_integration.py",
        "desc": "Automated smoke test: schema creation → roster ingestion → boxscore ingestion → FK assertions → skip-existing check.",
        "usage": "python test_integration.py",
    },
]

for script in SCRIPTS:
    with st.expander(f"📄 `{script['name']}`", expanded=False):
        st.write(script["desc"])
        st.code(script["usage"], language="bash")

st.divider()

# ── API Endpoints ───────────────────────────────────────────────────────────

st.subheader("NHL Web API Reference")

# Quick-reference cards
ENDPOINTS: list[dict[str, str]] = [
    {
        "name": "Team Roster",
        "url": "/v1/roster/{teamAbbrev}/{season}",
        "desc": "Returns roster grouped by position. Used by `ingest_roster.py`.",
    },
    {
        "name": "Club Schedule",
        "url": "/v1/club-schedule-season/{teamAbbrev}/{season}",
        "desc": "All games for a team's season. Used by `ingest_boxscore.py` to collect game IDs.",
    },
    {
        "name": "Boxscore",
        "url": "/v1/gamecenter/{gameId}/boxscore",
        "desc": "All player stats for both teams in one game. Primary data source for `ingest_boxscore.py`.",
    },
    {
        "name": "Player Game Log",
        "url": "/v1/player/{playerId}/game-log/{season}/{gameType}",
        "desc": "Per-game stats for a single player. Legacy path used by `ingest_historical.py`.",
    },
    {
        "name": "Club Stats",
        "url": "/v1/club-stats/{teamAbbrev}/{season}/{gameType}",
        "desc": "Season aggregates for all players on a team. Useful for goalie analysis.",
    },
    {
        "name": "Skater Leaders",
        "url": "/v1/skater-stats-leaders/{season}/{gameType}",
        "desc": "League-wide stat leaders. Use `?limit=-1` for all players.",
    },
]

card_cols = st.columns(3)
for i, ep in enumerate(ENDPOINTS):
    with card_cols[i % 3]:
        st.markdown(f"**{ep['name']}**")
        st.code(f"https://api-web.nhle.com{ep['url']}", language=None)
        st.caption(ep["desc"])

st.divider()

# Full api_map.md rendered as documentation
API_MAP_PATH = Path(__file__).parent.parent.parent / ".agent_context" / "api_map.md"
if API_MAP_PATH.exists():
    api_map_content = API_MAP_PATH.read_text(encoding="utf-8")
    st.markdown(api_map_content)
else:
    st.warning(f"API reference not found at `{API_MAP_PATH}`.")

"""
NHL Analytics Platform — Streamlit Dashboard

Main entry point and project home page.
Run: streamlit run dashboard/app.py
"""

import streamlit as st

st.set_page_config(
    page_title="NHL Analytics Platform",
    page_icon="🏒",
    layout="wide",
)

st.title("🏒 NHL Analytics Platform")

st.markdown("""
---

### Welcome

This platform is a three-tier analytics engine built on top of official NHL Web API data.

| Module | Purpose |
|---|---|
| **1. Data Warehouse** | Local SQLite database with normalized player and game-log tables, fed by automated ingestion pipelines. |
| **2. Broadcast Engine** | Algorithmic novelty index that flags rare statistical streaks and anomalies in real time. |
| **3. Prediction Models** | Baseline and ML regression models for player point projections. |

---

### Quick Links

Use the **sidebar** to navigate to:

- **DB Summary** — Explore the data warehouse: table stats, player search, game log explorer with charts.
- **Architecture** — Platform resources, data pipeline flowchart (Mermaid), schema metadata, and script reference.

---

### Data Sources

All data is sourced from the [NHL Web API](https://api-web.nhle.com) (`api-web.nhle.com/v1/`).
No API key required. See `.agent_context/api_map.md` for the endpoint reference.
""")

st.divider()

st.markdown("*Built with Streamlit, SQLite, and the NHL Web API.*")

\# NHL Analytics Platform - Development Plan \& State Tracker



This file tracks the architectural roadmap and implementation state of the platform. The AI Agent must update the status checkboxes (`\[ ]` to `\[x]`) as phases are completed.



\---



\## 🏗️ Phase 1: Data Warehouse (`1\_data\_warehouse/`)

Goal: Build a robust local SQLite database and automated historical ingestion pipeline.



\- \[x] Task 1.1: Create `requirements.txt` and initialize Python venv (`requests`, `pydantic`).

\- \[x] Task 1.2: Create `database\_setup.py` (Schema definition, foreign keys, indexes, and player/game-log tables).

\- \[x] Task 1.3: Create `ingest\_roster.py` (Fetch all 32 NHL team rosters → populate `players` table).

\- \[x] Task 1.4: Create `ingest\_historical.py` (API extraction wrapper, Pydantic validation, and UPSERT logic for game logs).

\- \[x] Task 1.5: Build `test\_integration.py` (Automated smoke test: schema → roster → game logs → FK assertions).

\- \[x] Task 1.6: Update `plan.md` — check off completed tasks.

\- \[x] Task 1.7: Update schema with `player\_team\_seasons` table, `season`/`game\_type` columns, `v\_player\_current\_team` view.

\- \[x] Task 1.8: Rewrite `ingest\_roster.py` to populate `players` + `player\_team\_seasons` (no `current\_team`).

\- \[x] Task 1.9: Add `--game-type` and `--skip-existing` flags to `ingest\_historical.py`.

\- \[x] Task 1.10: Update `test\_integration.py` for new schema (FK to `player\_team\_seasons`, `season`/`game\_type` checks).



\---



## 🖥️ Dashboard (`dashboard/`)

Goal: Build a Streamlit multi-page app to explore and visualize the data warehouse.

- \[x] Task D.1: Move `requirements.txt` to project root and add `streamlit`, `pandas` dependencies.

- \[x] Task D.2: Create `dashboard/db.py` — shared database connection helper (read-only SQLite, pandas query wrapper).

- \[x] Task D.3: Create `dashboard/app.py` — main entry point with project description home page.

- \[x] Task D.4: Create `dashboard/pages/1_DB_Summary.py` — 3-tab explorer: Overview, Player Explorer, Game Logs with charts and filters.

- \[x] Task D.5: Update dashboard for schema v2 — fix `current_team` column refs, add `season`/`game_type` filters and metrics.

- \[x] Task D.6: Create `dashboard/pages/2_Architecture.py` — Mermaid pipeline flowchart, database schema metadata, script/API reference.

- \[x] Task D.7: Render full `api_map.md` as API reference documentation on the Architecture page.

- \[x] Task 1.11: Data quality — delete 76 corrupted `player_game_logs` rows (malformed season format), add season format validation guard to `ingest_historical.py`.

- \[x] Task 1.12: Redesign ingestion pipeline — create `ingest_boxscore.py` using schedule + boxscore endpoints (~1,300 API calls vs ~13,000 per-player approach).

- \[x] Task 1.13: Expand schema — add `blocked_shots`, `giveaways`, `takeaways`, `faceoff_pct` columns to `player_game_logs` with migration support.

- \[x] Task 1.14: Update `test_integration.py` for boxscore-based ingestion pipeline (tests schedule → boxscore → normalization → upsert).

- \[x] Task 1.15: Update `api_map.md` with schedule endpoint and boxscore endpoint field documentation.



\---



\## 📊 Phase 2: Broadcast Analytics Engine (`2\_broadcast\_engine/`)

Goal: Develop an algorithmic novelty index engine to flag rare player streaks.



\- \[ ] Task 2.1: Write historical frequency query layers against `nhl\_data.db` to calculate empirical probability baselines ($P$).

\- \[ ] Task 2.2: Implement core algorithmic `StreakAnomaly` engine (Object-Oriented dataclass structures parsing active player game sequences).

\- \[ ] Task 2.3: Build comprehensive test suites (`pytest`) checking edge cases (e.g., scoring slumps, injury gaps, multiple streaks).

\- \[ ] Task 2.4: Build daily cron/runner pipeline script to evaluate active streaks for a specific game-day schedule.



\---



\## 🤖 Phase 3: Prediction Models (`3\_prediction\_models/`)

Goal: Run tabular baseline models and feature engineering pipelines for point projections.



\- \[ ] Task 3.1: Conduct exploratory data analysis (EDA) via notebooks to generate aggregate tabular player-season feature sets.

\- \[ ] Task 3.2: Establish baseline evaluation metrics (MAE / $R^2$) using simple historical rolling averages.

\- \[ ] Task 3.3: Train and document a lightweight machine learning regression model (e.g., Ridge or LightGBM) to beat the baseline metrics.


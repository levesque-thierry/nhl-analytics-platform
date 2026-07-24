# NHL Web API Reference Data Map

This file provides the exact structural and JSON key definitions for the modern `api-web.nhle.com` endpoints utilized across this platform. Use this as a strict schema reference for validation and mapping layers.

---

## 🛑 1. Player Game Logs Endpoint (Legacy — used by ingest_historical.py)
- **URL Pattern:** `https://api-web.nhle.com/v1/player/{playerId}/game-log/{season}/{gameType}`
- **HTTP Method:** GET
- **Top-Level Key:** The payload returns a dictionary where all individual game records are nested inside an array called `"gameLog"`.
- **gameType:** `2` = Regular Season, `3` = Playoffs.

### Game Log Object Keys (`gameLog` array items):
| JSON Key | Type | Description / Constraints | Database Target |
| :--- | :--- | :--- | :--- |
| `gameId` | int | Unique identifier for the specific game. | `game_id` (PK) |
| `gameDate` | str | Date of the game (Format: `YYYY-MM-DD`). | `game_date` |
| `teamAbbrev` | str | 3-letter abbreviation of the player's team. | `team_abbr` |
| `opponentAbbrev` | str | 3-letter abbreviation of the opposing team. | `opponent_abbr` |
| `homeRoadFlag` | str | `"H"` for Home, `"R"` for Road. | `home_road_flag` |
| `goals` | int | Total goals scored by the player in this game. | `goals` |
| `assists` | int | Total assists recorded by the player in this game. | `assists` |
| `points` | int | Total points (goals + assists). | `points` |
| `shots` | int | Total shots on goal taken by the player. | `shots` |
| `pim` | int | Penalty minutes served. | `pim` |
| `plusMinus` | int | Game plus/minus rating. | `plus_minus` |
| `toi` | str | Time on ice for the game (Format string: `"MM:SS"`). | `time_on_ice` |
| `shifts` | int | Total shifts taken by the player in this game. | `shifts` |
| `powerPlayGoals` | int | Goals scored on the power play. | `power_play_goals` |
| `powerPlayPoints` | int | Total points (goals + assists) on the power play. | `power_play_points` |
| `shorthandedGoals` | int | Goals scored while shorthanded. | `shorthanded_goals` |
| `gameWinningGoals` | int | Goals that were the game-winning goal. | `game_winning_goals` |
| `otGoals` | int | Goals scored in overtime. | `ot_goals` |

### Injected Columns (not in API payload):
| Column | Type | Description | Source |
| :--- | :--- | :--- | :--- |
| `player_id` | int | Unique player ID. | Injected from ingestion script input parameters. |
| `season` | str | Season in `YYYYYYYY` format (e.g. `20252026`). | Injected from ingestion script input parameters. |
| `game_type` | int | `2` = Regular Season, `3` = Playoffs. | Injected from ingestion script input parameters (default: `2`). |

---

## 🛑 2. Team Roster / Roster Lookup (For Ingestion Pipelines)
To get a list of active player IDs for a team to populate the database:
- **URL Pattern:** `https://api-web.nhle.com/v1/roster/{teamAbbrev}/{season}`
- **Top-Level Keys:** Returns arrays grouped by position: `"forwards"`, `"defensemen"`, `"goalies"`.

### Roster Player Object Keys:
| JSON Key | Type | Description | Database Target |
| :--- | :--- | :--- | :--- |
| `id` | int | Unique NHL Player ID. | `id` (PK) |
| `firstName` | dict | Nested name string, extracted via `["default"]` | `first_name` |
| `lastName` | dict | Nested name string, extracted via `["default"]` | `last_name` |
| `positionCode` | str | Position abbreviation (e.g., `"C"`, `"L"`, `"R"`, `"D"`, `"G"`). | `position` |
| `sweaterNumber` | int | Player's jersey number. | `sweater_number` |
| `shootsCatches` | str | `"L"` or `"R"` — shooting/catching hand. | `shoots_catches` |
| `birthDate` | str | Date of birth (Format: `YYYY-MM-DD`). | `birth_date` |

> **Note:** The roster endpoint is grouped into three top-level arrays: `"forwards"`, `"defensemen"`, and `"goalies"`. All three share the same object schema above.

---

## 🛑 3. Club Schedule Endpoint (Used by ingest_boxscore.py)
- **URL Pattern:** `https://api-web.nhle.com/v1/club-schedule-season/{teamAbbrev}/{season}`
- **HTTP Method:** GET
- **Top-Level Keys:** `previousSeason`, `currentSeason`, `nextSeason`, `clubTimezone`, `clubUTCOffset`, `games`
- **gameType:** `1` = Preseason, `2` = Regular Season, `3` = Playoffs

### Game Object Keys (inside `games` array):
| JSON Key | Type | Description |
| :--- | :--- | :--- |
| `id` | int | Unique game identifier (encodes season + game type). |
| `season` | int | Season (e.g., `20252026`). |
| `gameType` | int | `1`=Preseason, `2`=Regular, `3`=Playoffs. |
| `gameDate` | str | Date (Format: `YYYY-MM-DD`). |
| `gameState` | str | `"OFF"`, `"FINAL"`, `"CRIT"`, etc. |
| `awayTeam` | dict | `{id, abbrev, score, sog, ...}` |
| `homeTeam` | dict | `{id, abbrev, score, sog, ...}` |

> **Note:** Each team's schedule includes all games they played. Deduplication by game ID is required when collecting across multiple teams.

---

## 🛑 4. Boxscore Endpoint (Primary — used by ingest_boxscore.py)
- **URL Pattern:** `https://api-web.nhle.com/v1/gamecenter/{gameId}/boxscore`
- **HTTP Method:** GET
- **Top-Level Keys:** `id`, `season`, `gameType`, `gameDate`, `awayTeam`, `homeTeam`, `playerByGameStats`, `gameOutcome`, ...

### Player-By-Game-Stats Structure:
```
playerByGameStats
  ├── awayTeam
  │     ├── forwards: [player, ...]
  │     ├── defense: [player, ...]
  │     └── goalies: [player, ...]
  └── homeTeam
        ├── forwards: [player, ...]
        ├── defense: [player, ...]
        └── goalies: [player, ...]
```

### Skater Object Keys (forwards + defense):
| JSON Key | Type | Description | Database Target |
| :--- | :--- | :--- | :--- |
| `playerId` | int | Unique NHL Player ID. | `player_id` |
| `sweaterNumber` | int | Jersey number. | — |
| `name` | dict | `{"default": "Full Name"}` | — (use roster) |
| `position` | str | Position code. | — |
| `goals` | int | Goals scored. | `goals` |
| `assists` | int | Assists recorded. | `assists` |
| `points` | int | Points (goals + assists). | `points` |
| `plusMinus` | int | Plus/minus rating. | `plus_minus` |
| `pim` | int | Penalty minutes. | `pim` |
| `hits` | int | Hits delivered. | — (dropped) |
| `powerPlayGoals` | int | Power play goals. | `power_play_goals` |
| `sog` | int | Shots on goal. | `shots` |
| `faceoffWinningPctg` | float | Faceoff win percentage (0.0–1.0). | `faceoff_pct` |
| `toi` | str | Time on ice (Format: `"MM:SS"`). | `time_on_ice` |
| `blockedShots` | int | Shot blocks. | `blocked_shots` |
| `shifts` | int | Total shifts. | `shifts` |
| `giveaways` | int | Giveaways. | `giveaways` |
| `takeaways` | int | Takeaways. | `takeaways` |

### Goalie Object Keys:
| JSON Key | Type | Description | Database Target |
| :--- | :--- | :--- | :--- |
| `playerId` | int | Unique NHL Player ID. | `player_id` |
| `name` | dict | `{"default": "Full Name"}` | — (use roster) |
| `toi` | str | Time on ice. | `time_on_ice` |
| `shotsAgainst` | int | Total shots faced. | — |
| `saves` | int | Total saves. | — |
| `savePctg` | float | Save percentage. | — |
| `goalsAgainst` | int | Goals against. | — |
| `decision` | str | `"W"`, `"L"`, `"OTL"`, or `null`. | — |
| `starter` | bool | Whether this goalie started. | — |

> **Note:** Goalie game logs are minimal (no shots, shifts, faceoff). Goalie-specific stats (SV%, GAA) are available via the `/v1/club-stats/` endpoint but are season-level aggregates.

---

## 🛑 5. Club Stats Endpoint (Season Aggregates)
- **URL Pattern:** `https://api-web.nhle.com/v1/club-stats/{teamAbbrev}/{season}/{gameType}`
- **HTTP Method:** GET
- **Top-Level Keys:** `season`, `gameType`, `skaters`, `goalies`

### Skater Aggregate Object Keys:
| JSON Key | Type | Description |
| :--- | :--- | :--- |
| `playerId` | int | Unique NHL Player ID. |
| `firstName` | dict | `{"default": "First Name"}` |
| `lastName` | dict | `{"default": "Last Name"}` |
| `positionCode` | str | Position abbreviation. |
| `gamesPlayed` | int | Total games played this season. |
| `goals` | int | Season total goals. |
| `assists` | int | Season total assists. |
| `points` | int | Season total points. |
| `plusMinus` | int | Season +/- |
| `penaltyMinutes` | int | Season PIM. |
| `powerPlayGoals` | int | Season PP goals. |
| `shorthandedGoals` | int | Season SH goals. |
| `gameWinningGoals` | int | Season GWG. |
| `overtimeGoals` | int | Season OT goals. |
| `shots` | int | Season total SOG. |
| `shootingPctg` | float | Season shooting %. |
| `avgTimeOnIcePerGame` | float | Avg TOI per game (seconds). |

### Goalie Aggregate Object Keys:
| JSON Key | Type | Description |
| :--- | :--- | :--- |
| `playerId` | int | Unique NHL Player ID. |
| `gamesPlayed` | int | GP. |
| `gamesStarted` | int | GS. |
| `wins` | int | Wins. |
| `losses` | int | Losses. |
| `overtimeLosses` | int | OTL. |
| `goalsAgainstAverage` | float | GAA. |
| `savePercentage` | float | SV%. |
| `shotsAgainst` | int | SA. |
| `saves` | int | Saves. |
| `goalsAgainst` | int | GA. |
| `shutouts` | int | SO. |

> **Note:** Season-level aggregates only — not per-game. Useful for goalie analysis where boxscore data is limited.

---

## 🛑 6. Skater Stats Leaders Endpoint
- **URL Pattern:** `https://api-web.nhle.com/v1/skater-stats-leaders/{season}/{gameType}?categories={cats}&limit={n}`
- **HTTP Method:** GET
- **Query Params:** `categories` = comma-separated list (e.g., `goals,assists,points`); `limit` = `-1` for all players.
- **Top-Level Keys:** One key per category requested (e.g., `"points"`, `"goals"`).

### Leader Object Keys (inside each category array):
| JSON Key | Type | Description |
| :--- | :--- | :--- |
| `playerId` | int | Unique NHL Player ID. |
| `firstName` | dict | `{"default": "First Name"}` |
| `lastName` | dict | `{"default": "Last Name"}` |
| `teamAbbrevs` | str | Team abbreviation. |
| `value` | int/float | The stat value. |
| `gamesPlayed` | int | Games played. |

> **Note:** Use `limit=-1` to get full league-wide rankings. Useful for leaderboards and comparison features.

---

## 🛑 7. Game Center Endpoint
- **URL Pattern:** `https://api-web.nhle.com/v1/gamecenter/{gameId}/landing`
- **HTTP Method:** GET
- **Description:** Extended game summary with scoring summary, play-by-play highlights, three stars, etc.
- **Top-Level Keys:** `id`, `season`, `gameType`, `gameDate`, `awayTeam`, `homeTeam`, `scoringSummary`, `threeStars`, ...

> **Note:** More detailed than boxscore. Use for broadcast/display features (scoring summaries, stars).

---

## 🛑 8. Standings Endpoint
- **URL Pattern:** `https://api-web.nhle.com/v1/standings/{date}`
- **HTTP Method:** GET
- **Date format:** `YYYY-MM-DD`
- **Description:** League standings as of a given date.

> **Note:** Useful for context in prediction models (playoff race, seeding).

---

## 🛑 9. Play-by-Play Endpoint (Used by `5_advanced_stats/ingest_pbp.py`)
- **URL Pattern:** `https://api-web.nhle.com/v1/gamecenter/{gameId}/play-by-play`
- **HTTP Method:** GET
- **Description:** Full play-by-play event data for a single game. Each event includes coordinates, situation code, player IDs, and scoring details.
- **Rate Limit:** 1 request per game, ~1,300 games per season. Use `REQUEST_DELAY = 1.0` between calls.

### PBP Event Object Keys:
| JSON Key | Type | Description | Database Target |
| :--- | :--- | :--- | :--- |
| `id` | int | Unique event identifier. | `event_id` |
| `period` | int | Period number (1-4 for OT, 5 for SO). | `period` |
| `timeInPeriod` | str | Time elapsed in period (`"MM:SS"`). | `time_in_period` |
| `timeRemaining` | str | Time remaining in period (`"MM:SS"`). | `time_remaining` |
| `sortOrder` | int | Event sequence number within game. | `sort_order` |
| `typeCode` | int | Numeric event type code. | `type_code` |
| `typeDescKey` | str | Event type slug: `goal`, `shot-on-goal`, `missed-shot`, `blocked-shot`, `faceoff`, `hit`, `giveaway`, `takeaway`, `penalty`, `stoppage`, `delayed-penalty`, `period-start`, `period-end`, `game-end`. | `type_desc_key` |
| `situationCode` | str | 4-digit strength code `"HS GS AS GA"` (home skaters, home goalie, away skaters, away goalie). `"1551"` = 5v5. | `situation_code` |
| `homeTeamDefendingSide` | str | `"left"` or `"right"` — home team defensive zone. | `home_team_defending_side` |
| `xCoord` | int | X-coordinate on ice (-100 to 100). | `x_coord` |
| `yCoord` | int | Y-coordinate on ice (-42 to 42). | `y_coord` |
| `zoneCode` | str | `"O"` (offensive), `"D"` (defensive), `"N"` (neutral). | `zone_code` |
| `shotType` | str | Shot type: `Wrist`, `Snap`, `Slap`, `Backhand`, `Tip-In`, `Wrap-around`, `Deflected`. | `shot_type` |
| `shootingPlayerId` | int | Player ID of the shooter (null for non-shot events). | `shooting_player_id` |
| `goalieInNetId` | int | Goalie ID in net at time of event. | `goalie_in_net_id` |
| `eventOwnerTeamId` | int | Team ID that owns the event (shooting team for shots). | `event_owner_team_id` |
| `blockingPlayerId` | int | Player ID who blocked the shot (null for non-block events). | `blocking_player_id` |
| `scoringPlayerId` | int | Player ID who scored (null for non-goal events). | `scoring_player_id` |
| `scoringPlayerTotal` | int | Scorer's goal total after this goal. | `scoring_player_total` |
| `assist1PlayerId` | int | Primary assist player ID. | `assist1_player_id` |
| `assist1PlayerTotal` | int | Primary assist player's assist total. | `assist1_player_total` |
| `assist2PlayerId` | int | Secondary assist player ID. | `assist2_player_id` |
| `assist2PlayerTotal` | int | Secondary assist player's assist total. | `assist2_player_total` |
| `awayScore` | int | Away team score after this event. | `away_score` |
| `homeScore` | int | Home team score after this event. | `home_score` |
| `awaySog` | int | Away team shots on goal after this event. | `away_sog` |
| `homeSog` | int | Home team shots on goal after this event. | `home_sog` |

> **Note:** The PBP endpoint returns a top-level `"plays"` array. Each item contains the fields above. Events are sequential within the game. The `situationCode` field is used to filter by strength state (e.g., `"1551"` for 5v5).

---

## 🛑 10. Shift Charts Endpoint (Used by `5_advanced_stats/ingest_shifts.py`)
- **URL Pattern:** `https://api.nhle.com/stats/rest/en/shiftcharts?cayenneExp=gameId={gameId}`
- **HTTP Method:** GET
- **Description:** Shift-by-shift ice time data for all players in a game. Each shift records start/end times and duration.
- **Note:** Uses the older `api.nhle.com/stats/rest/en` endpoint (not `api-web.nhle.com`).

### Shift Object Keys (inside `"data"` array):
| JSON Key | Type | Description | Database Target |
| :--- | :--- | :--- | :--- |
| `gameId` | int | Game identifier. | `game_id` |
| `shiftId` | int | Unique shift identifier. | `shift_id` |
| `playerId` | int | NHL Player ID. | `player_id` |
| `playerName` | str | Full player name. | `player_name` |
| `teamAbbrev` | str | Team abbreviation (e.g., `"FLA"`, `"CHI"`). | `team_abbr` |
| `period` | int | Period number (1-5). | `period` |
| `shiftNumber` | int | Player's shift number in the game. | `shift_number` |
| `startTime` | str | Shift start time (`"MM:SS"` elapsed in period). | `start_time` |
| `endTime` | str | Shift end time (`"MM:SS"` elapsed in period). | `end_time` |
| `duration` | str | Shift duration (`"MM:SS"`). | `duration` |
| `detailCode` | int | Shift detail code. | `detail_code` |
| `eventNumber` | int | Event number at shift start. | `event_number` |
| `hexValue` | str | Hex color value for visualization. | `hex_value` |
| `teamId` | int | Numeric team ID. | `team_id` |
| `teamName` | str | Full team name. | `team_name` |
| `typeCode` | int | Shift type code. | `type_code` |

> **Note:** The shiftcharts endpoint returns a top-level `"data"` array. All players (skaters + goalies) are included. The `startTime`/`endTime` format is `"MM:SS"` elapsed within the period (0:00 to 20:00 for regular periods). Use `duration` for TOI calculations.
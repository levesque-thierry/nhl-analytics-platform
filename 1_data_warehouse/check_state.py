"""Check current DB state and plan expansion."""
import sqlite3

conn = sqlite3.connect("nhl_data.db")

print("=== Current DB state ===")
print("Players:", conn.execute("SELECT COUNT(*) FROM players").fetchone()[0])
print("Team seasons:", conn.execute("SELECT COUNT(*) FROM player_team_seasons").fetchone()[0])
print("Game logs:", conn.execute("SELECT COUNT(*) FROM player_game_logs").fetchone()[0])
print("  Regular season:", conn.execute("SELECT COUNT(*) FROM player_game_logs WHERE game_type=2").fetchone()[0])
print("  Playoffs:", conn.execute("SELECT COUNT(*) FROM player_game_logs WHERE game_type=3").fetchone()[0])

seasons = [r[0] for r in conn.execute("SELECT DISTINCT season FROM player_game_logs ORDER BY season").fetchall()]
print("Seasons:", seasons)

teams = [r[0] for r in conn.execute("SELECT DISTINCT current_team FROM v_player_current_team ORDER BY current_team").fetchall()]
print("Teams:", teams)

players_with_logs = conn.execute("SELECT COUNT(DISTINCT player_id) FROM player_game_logs").fetchone()[0]
total_players = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
print(f"Players with game logs: {players_with_logs} / {total_players}")

# Estimate full season: 32 teams x ~23 players = ~736 players, each ~82 reg + ~20 playoff games
print()
print("=== Full 20252026 season estimate ===")
print("32 teams, ~23 players each = ~736 players")
print("~82 regular season games + ~20 playoff games per player")
print("Estimated rows: ~736 x 100 = ~73,600 game log records")
print("Estimated API calls: ~736 (one per player per game type)")
print("At 0.5s rate limit: ~736 seconds = ~12 minutes")

conn.close()

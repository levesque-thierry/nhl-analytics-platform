"""
baselines.py — Historical Frequency Baseline Computation

Computes empirical probability distributions for streak types at three levels:
    - League: across all players and teams
    - Team: per-team frequency distributions
    - Player: personal career max streaks

Streak types include positive (hot streaks) and negative (droughts).

CLI Usage:
    # Rebuild baselines from database
    python baselines.py --rebuild

    # Show baselines summary
    python baselines.py --summary
"""

import argparse
import json
import logging
import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

CACHE_PATH: str = str(Path(__file__).parent / "baselines_cache.json")

# Import DB_PATH from data warehouse — fall back to relative path
try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "1_data_warehouse"))
    from database_setup import DB_PATH
except ImportError:
    DB_PATH = str(Path(__file__).parent.parent / "1_data_warehouse" / "nhl_data.db")


# ---------------------------------------------------------------------------
# Position grouping
# ---------------------------------------------------------------------------

FORWARD_POSITIONS: frozenset[str] = frozenset({"C", "L", "R"})


def get_position_group(position: str) -> str:
    """Map raw position code to group: F (forward), D (defenseman), or ALL."""
    if position in FORWARD_POSITIONS:
        return "F"
    if position == "D":
        return "D"
    return "ALL"


# ---------------------------------------------------------------------------
# Streak type definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StreakType:
    """Definition of a streak type with its predicate and metadata."""
    name: str
    predicate: Callable[[dict], bool]
    sign: str  # "positive" or "negative"
    description: str


def _point_streak_pred(game: dict) -> bool:
    return game["points"] >= 1

def _goal_streak_pred(game: dict) -> bool:
    return game["goals"] >= 1

def _multi_point_pred(game: dict) -> bool:
    return game["points"] >= 2

def _hat_trick_pred(game: dict) -> bool:
    return game["goals"] >= 3

def _takeaway_streak_pred(game: dict) -> bool:
    return game["takeaways"] >= 1

def _scoreless_drought_pred(game: dict) -> bool:
    return game["goals"] == 0

def _pointless_drought_pred(game: dict) -> bool:
    return game["points"] == 0

def _shot_drought_pred(game: dict) -> bool:
    return game["shots"] <= 1

def _minus_streak_pred(game: dict) -> bool:
    return game["plus_minus"] < 0


STREAK_TYPES: dict[str, StreakType] = {
    "point_streak": StreakType("point_streak", _point_streak_pred, "positive",
                               "Consecutive games with 1+ points"),
    "goal_streak": StreakType("goal_streak", _goal_streak_pred, "positive",
                              "Consecutive games with 1+ goals"),
    "multi_point": StreakType("multi_point", _multi_point_pred, "positive",
                              "Consecutive games with 2+ points"),
    "hat_trick": StreakType("hat_trick", _hat_trick_pred, "positive",
                            "Games with 3+ goals"),
    "takeaway_streak": StreakType("takeaway_streak", _takeaway_streak_pred, "positive",
                                  "Consecutive games with 1+ takeaways"),
    "scoreless_drought": StreakType("scoreless_drought", _scoreless_drought_pred, "negative",
                                    "Consecutive games with 0 goals"),
    "pointless_drought": StreakType("pointless_drought", _pointless_drought_pred, "negative",
                                    "Consecutive games with 0 points"),
    "shot_drought": StreakType("shot_drought", _shot_drought_pred, "negative",
                               "Consecutive games with 1 or fewer shots"),
    "minus_streak": StreakType("minus_streak", _minus_streak_pred, "negative",
                               "Consecutive games with negative plus/minus"),
}


# ---------------------------------------------------------------------------
# Streak extraction
# ---------------------------------------------------------------------------

def extract_streaks(
    game_logs: list[dict],
    predicate: Callable[[dict], bool],
    label: str,
) -> list[dict]:
    """
    Walk an ordered list of game logs and extract consecutive streaks
    matching the predicate. Returns list of dicts with keys:
        streak_type, length, start_date, end_date, games
    """
    if not game_logs:
        return []

    streaks: list[dict] = []
    current_games: list[dict] = []

    for game in game_logs:
        if predicate(game):
            current_games.append(game)
        else:
            if current_games:
                streaks.append({
                    "streak_type": label,
                    "length": len(current_games),
                    "start_date": current_games[0]["game_date"],
                    "end_date": current_games[-1]["game_date"],
                    "games": current_games,
                })
                current_games = []

    # Flush final streak
    if current_games:
        streaks.append({
            "streak_type": label,
            "length": len(current_games),
            "start_date": current_games[0]["game_date"],
            "end_date": current_games[-1]["game_date"],
            "games": current_games,
        })

    return streaks


# ---------------------------------------------------------------------------
# Baseline computation — League level
# ---------------------------------------------------------------------------

def compute_league_baselines(conn: sqlite3.Connection) -> dict:
    """
    Compute league-wide streak frequency distributions by position group.
    Returns: {position_group: {streak_type: {length: probability}}}
    """
    logger.info("Computing league baselines...")

    # Fetch all skater game logs (no goalies), grouped by player+season
    cursor = conn.execute("""
        SELECT pgl.player_id, pgl.season, pgl.game_date,
               pgl.goals, pgl.assists, pgl.points, pgl.shots,
               pgl.plus_minus, pgl.takeaways, pgl.blocked_shots,
               p.position
        FROM player_game_logs pgl
        JOIN players p ON pgl.player_id = p.id
        WHERE p.position != 'G'
        ORDER BY pgl.player_id, pgl.season, pgl.game_date
    """)

    columns = ["player_id", "season", "game_date", "goals", "assists",
               "points", "shots", "plus_minus", "takeaways", "blocked_shots",
               "position"]

    # Group by (player_id, season)
    player_seasons: dict[tuple, list[dict]] = defaultdict(list)
    position_map: dict[int, str] = {}

    for row in cursor.fetchall():
        game = dict(zip(columns, row))
        key = (game["player_id"], game["season"])
        player_seasons[key].append(game)
        position_map[game["player_id"]] = game["position"]

    # Count player-seasons per position group
    ps_counts: dict[str, int] = defaultdict(int)
    for (pid, _), games in player_seasons.items():
        pg = get_position_group(position_map[pid])
        ps_counts[pg] += 1
        ps_counts["ALL"] += 1

    logger.info("Player-seasons by position: %s", dict(ps_counts))

    # Track per-player-season max streak length for each streak type
    # This gives us the probability that a player-season produces a streak >= N
    ps_max_streaks: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for (pid, _), games in player_seasons.items():
        pg = get_position_group(position_map[pid])

        for st_name, st_def in STREAK_TYPES.items():
            streaks = extract_streaks(games, st_def.predicate, st_name)
            if streaks:
                max_len = max(s["length"] for s in streaks)
            else:
                max_len = 0
            ps_max_streaks[pg][st_name].append(max_len)
            ps_max_streaks["ALL"][st_name].append(max_len)

    # Convert to probabilities: P(a player-season has a streak >= N)
    baselines: dict[str, dict[str, dict[int, float]]] = {}

    for pg in ["F", "D", "ALL"]:
        baselines[pg] = {}
        max_lengths = ps_max_streaks.get(pg, {})

        for st_name in STREAK_TYPES:
            lengths = max_lengths.get(st_name, [])
            if not lengths:
                baselines[pg][st_name] = {}
                continue

            max_observed = max(lengths) if lengths else 0
            total = len(lengths)
            freq: dict[int, float] = {}

            for n in range(1, max_observed + 1):
                count_at_least_n = sum(1 for ml in lengths if ml >= n)
                freq[n] = count_at_least_n / total if total > 0 else 0.0

            baselines[pg][st_name] = freq

    logger.info("League baselines computed for %d player-seasons.", sum(ps_counts.values()))
    return baselines


# ---------------------------------------------------------------------------
# Baseline computation — Team level
# ---------------------------------------------------------------------------

def compute_team_baselines(conn: sqlite3.Connection) -> dict:
    """
    Compute per-team streak frequency distributions by position group.
    Returns: {team: {position_group: {streak_type: {length: probability}}}}
    """
    logger.info("Computing team baselines...")

    cursor = conn.execute("""
        SELECT pgl.player_id, pgl.season, pgl.team_abbr, pgl.game_date,
               pgl.goals, pgl.assists, pgl.points, pgl.shots,
               pgl.plus_minus, pgl.takeaways, pgl.blocked_shots,
               p.position
        FROM player_game_logs pgl
        JOIN players p ON pgl.player_id = p.id
        WHERE p.position != 'G'
        ORDER BY pgl.team_abbr, pgl.player_id, pgl.season, pgl.game_date
    """)

    columns = ["player_id", "season", "team_abbr", "game_date", "goals",
               "assists", "points", "shots", "plus_minus", "takeaways",
               "blocked_shots", "position"]

    # Group by (team, player_id, season)
    team_player_seasons: dict[str, dict[tuple, list[dict]]] = defaultdict(
        lambda: defaultdict(list)
    )
    team_positions: dict[str, dict[int, str]] = defaultdict(dict)

    for row in cursor.fetchall():
        game = dict(zip(columns, row))
        team = game["team_abbr"]
        key = (game["player_id"], game["season"])
        team_player_seasons[team][key].append(game)
        team_positions[team][game["player_id"]] = game["position"]

    baselines: dict[str, dict[str, dict[str, dict[int, float]]]] = {}

    for team, player_seasons in team_player_seasons.items():
        # Count player-seasons per position group for this team
        ps_counts: dict[str, int] = defaultdict(int)
        for (pid, _), games in player_seasons.items():
            pg = get_position_group(team_positions[team][pid])
            ps_counts[pg] += 1
            ps_counts["ALL"] += 1

        # Track per-player-season max streak length
        ps_max_streaks: dict[str, dict[str, list[int]]] = defaultdict(
            lambda: defaultdict(list)
        )

        for (pid, _), games in player_seasons.items():
            pg = get_position_group(team_positions[team][pid])

            for st_name, st_def in STREAK_TYPES.items():
                streaks = extract_streaks(games, st_def.predicate, st_name)
                if streaks:
                    max_len = max(s["length"] for s in streaks)
                else:
                    max_len = 0
                ps_max_streaks[pg][st_name].append(max_len)
                ps_max_streaks["ALL"][st_name].append(max_len)

        # Convert to probabilities
        team_baselines: dict[str, dict[str, dict[int, float]]] = {}

        for pg in ["F", "D", "ALL"]:
            team_baselines[pg] = {}
            max_lengths = ps_max_streaks.get(pg, {})

            for st_name in STREAK_TYPES:
                lengths = max_lengths.get(st_name, [])
                if not lengths:
                    team_baselines[pg][st_name] = {}
                    continue

                max_observed = max(lengths) if lengths else 0
                total = len(lengths)
                freq: dict[int, float] = {}

                for n in range(1, max_observed + 1):
                    count_at_least_n = sum(1 for ml in lengths if ml >= n)
                    freq[n] = count_at_least_n / total if total > 0 else 0.0

                team_baselines[pg][st_name] = freq

        baselines[team] = team_baselines

    logger.info("Team baselines computed for %d teams.", len(baselines))
    return baselines


# ---------------------------------------------------------------------------
# Baseline computation — Player level
# ---------------------------------------------------------------------------

def compute_player_baselines(conn: sqlite3.Connection) -> dict:
    """
    Compute personal career max streaks for each player.
    Returns: {player_id: {streak_type: max_length}}
    """
    logger.info("Computing player baselines...")

    cursor = conn.execute("""
        SELECT pgl.player_id, pgl.season, pgl.game_date,
               pgl.goals, pgl.assists, pgl.points, pgl.shots,
               pgl.plus_minus, pgl.takeaways, pgl.blocked_shots,
               p.position
        FROM player_game_logs pgl
        JOIN players p ON pgl.player_id = p.id
        WHERE p.position != 'G'
        ORDER BY pgl.player_id, pgl.season, pgl.game_date
    """)

    columns = ["player_id", "season", "game_date", "goals", "assists",
               "points", "shots", "plus_minus", "takeaways", "blocked_shots",
               "position"]

    # Group by (player_id, season)
    player_seasons: dict[tuple, list[dict]] = defaultdict(list)
    for row in cursor.fetchall():
        game = dict(zip(columns, row))
        key = (game["player_id"], game["season"])
        player_seasons[key].append(game)

    # Extract streaks per player across all seasons, track career max
    player_max: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for (pid, _), games in player_seasons.items():
        for st_name, st_def in STREAK_TYPES.items():
            streaks = extract_streaks(games, st_def.predicate, st_name)
            for streak in streaks:
                if streak["length"] > player_max[pid][st_name]:
                    player_max[pid][st_name] = streak["length"]

    logger.info("Player baselines computed for %d players.", len(player_max))
    return {str(pid): dict(maxes) for pid, maxes in player_max.items()}


# ---------------------------------------------------------------------------
# Compute all baselines
# ---------------------------------------------------------------------------

def compute_all_baselines(db_path: str = DB_PATH) -> dict:
    """Compute league, team, and player baselines. Returns the full cache dict."""
    conn = sqlite3.connect(db_path)
    try:
        league = compute_league_baselines(conn)
        team = compute_team_baselines(conn)
        player = compute_player_baselines(conn)

        # Count player-seasons for metadata
        cursor = conn.execute("""
            SELECT p.position, COUNT(DISTINCT pgl.player_id || pgl.season)
            FROM player_game_logs pgl
            JOIN players p ON pgl.player_id = p.id
            WHERE p.position != 'G'
            GROUP BY p.position
        """)
        ps_counts: dict[str, int] = {}
        total = 0
        for pos, count in cursor.fetchall():
            pg = get_position_group(pos)
            ps_counts[pg] = ps_counts.get(pg, 0) + count
            total += count
        ps_counts["ALL"] = total

        seasons_cursor = conn.execute(
            "SELECT DISTINCT season FROM player_game_logs ORDER BY season"
        )
        seasons = [row[0] for row in seasons_cursor.fetchall()]

        from datetime import datetime, timezone

        baselines = {
            "metadata": {
                "seasons": seasons,
                "total_player_seasons": ps_counts,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            },
            "league": league,
            "team": team,
            "player": player,
        }

        logger.info(
            "All baselines computed: %d seasons, %d player-seasons.",
            len(seasons), total,
        )
        return baselines

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Save / Load cache
# ---------------------------------------------------------------------------

def save_baselines(baselines: dict, path: str = CACHE_PATH) -> None:
    """Write baselines to JSON cache file."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(baselines, f, indent=2, ensure_ascii=False)
        logger.info("Baselines saved to %s", path)
    except OSError as exc:
        logger.error("Failed to save baselines to %s: %s", path, exc)
        raise


def load_baselines(path: str = CACHE_PATH) -> dict:
    """Load baselines from JSON cache file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            baselines = json.load(f)
        logger.info("Baselines loaded from %s", path)
        return baselines
    except FileNotFoundError:
        logger.error("Baselines cache not found at %s. Run with --rebuild first.", path)
        raise
    except json.JSONDecodeError as exc:
        logger.error("Corrupt baselines cache at %s: %s", path, exc)
        raise


# ---------------------------------------------------------------------------
# Summary display
# ---------------------------------------------------------------------------

def print_summary(baselines: dict) -> None:
    """Print a human-readable summary of the baselines."""
    meta = baselines.get("metadata", {})
    print("\n=== Baselines Summary ===")
    print(f"Seasons: {', '.join(meta.get('seasons', []))}")
    print(f"Player-seasons: {meta.get('total_player_seasons', {})}")
    print(f"Computed at: {meta.get('computed_at', 'N/A')}")

    league = baselines.get("league", {})
    for pg in ["F", "D", "ALL"]:
        if pg not in league:
            continue
        print(f"\n--- {pg} ---")
        for st_name, freqs in league[pg].items():
            if not freqs:
                continue
            samples = [f"  >={n}: {p:.3f}" for n, p in sorted(freqs.items()) if n <= 10]
            print(f"  {st_name}:")
            for s in samples:
                print(s)

    teams = baselines.get("team", {})
    print(f"\nTeams with baselines: {len(teams)}")
    for team in sorted(teams.keys())[:5]:
        team_f = teams[team].get("F", {}).get("point_streak", {})
        max_len = max(team_f.keys()) if team_f else 0
        print(f"  {team}: max point streak prob length = {max_len}")

    players = baselines.get("player", {})
    print(f"\nPlayers with baselines: {len(players)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Compute and manage historical streak baselines."
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild baselines from database and save to cache.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Display a summary of cached baselines.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=DB_PATH,
        help="Path to the NHL data warehouse database.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    if args.rebuild:
        baselines = compute_all_baselines(args.db_path)
        save_baselines(baselines)
        print_summary(baselines)
    elif args.summary:
        try:
            baselines = load_baselines()
            print_summary(baselines)
        except FileNotFoundError:
            logger.info("No cache found. Computing from scratch...")
            baselines = compute_all_baselines(args.db_path)
            save_baselines(baselines)
            print_summary(baselines)
    else:
        print("Use --rebuild to compute baselines or --summary to view them.")

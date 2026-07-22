"""
streak_engine.py — StreakAnomaly Core Engine

Detects active player streaks (hot streaks and cold droughts) and scores
them against historical baselines to produce a novelty / rarity index.

Scoring:
    rarity_score = 1 - P(player-season has a streak >= current_length)
    A score near 1.0 = extremely rare. A score near 0.0 = common.

Three evaluation levels:
    - League: how rare is this streak league-wide?
    - Team:   how rare is this streak within the team's history?
    - Player: is this a personal-career-best streak?

CLI Usage:
    python streak_engine.py --player-id 8478402 --season 20252026
    python streak_engine.py --all-active
"""

import argparse
import logging
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from baselines import (
    STREAK_TYPES,
    StreakType,
    extract_streaks,
    get_position_group,
    load_baselines,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "1_data_warehouse"))
    from database_setup import DB_PATH
except ImportError:
    DB_PATH = str(Path(__file__).parent.parent / "1_data_warehouse" / "nhl_data.db")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ActiveStreak:
    """A single detected streak in a player's recent game sequence."""
    streak_type: str
    length: int
    start_date: str
    end_date: str
    sign: str  # "positive" or "negative"
    description: str
    recent_games: list[dict] = field(default_factory=list)


@dataclass
class RarityScore:
    """Rarity evaluation at a single comparison level."""
    level: str          # "league", "team", or "player"
    probability: float  # P(player-season has streak >= N) from baselines
    rarity: float       # 1.0 - probability


@dataclass
class StreakAnomaly:
    """Complete anomaly record for one active streak."""
    player_id: int
    player_name: str
    team: str
    position: str
    season: str
    streak: ActiveStreak
    rarity_scores: list[RarityScore] = field(default_factory=list)

    @property
    def novelty_index(self) -> float:
        """Weighted composite rarity: 40% league + 35% team + 25% player."""
        scores = {r.level: r.rarity for r in self.rarity_scores}
        league_r = scores.get("league", 0.0)
        team_r = scores.get("team", 0.0)
        player_r = scores.get("player", 0.0)
        return 0.40 * league_r + 0.35 * team_r + 0.25 * player_r

    @property
    def severity(self) -> str:
        """Human-readable severity label based on novelty index."""
        ni = self.novelty_index
        if ni >= 0.90:
            return "EXTREMELY RARE"
        if ni >= 0.75:
            return "VERY RARE"
        if ni >= 0.50:
            return "RARE"
        if ni >= 0.25:
            return "UNCOMMON"
        return "COMMON"

    def to_dict(self) -> dict:
        """Serialize to a plain dictionary for JSON / downstream consumption."""
        return {
            "player_id": self.player_id,
            "player_name": self.player_name,
            "team": self.team,
            "position": self.position,
            "season": self.season,
            "streak": {
                "type": self.streak.streak_type,
                "length": self.streak.length,
                "start_date": self.streak.start_date,
                "end_date": self.streak.end_date,
                "sign": self.streak.sign,
                "description": self.streak.description,
            },
            "rarity_scores": [
                {"level": r.level, "probability": r.probability, "rarity": r.rarity}
                for r in self.rarity_scores
            ],
            "novelty_index": round(self.novelty_index, 4),
            "severity": self.severity,
        }


# ---------------------------------------------------------------------------
# Rarity lookup helpers
# ---------------------------------------------------------------------------

def _lookup_league_rarity(
    baselines: dict,
    position_group: str,
    streak_type: str,
    length: int,
) -> RarityScore:
    """Look up league-level rarity for a given streak."""
    freq = baselines.get("league", {}).get(position_group, {}).get(streak_type, {})
    prob = freq.get(str(length), 0.0)
    # If streak exceeds max observed, extrapolate to 0
    if prob == 0.0 and length > 0:
        max_key = max((int(k) for k in freq), default=0)
        if length > max_key:
            prob = 0.0
    return RarityScore(level="league", probability=prob, rarity=1.0 - prob)


def _lookup_team_rarity(
    baselines: dict,
    team: str,
    position_group: str,
    streak_type: str,
    length: int,
) -> RarityScore:
    """Look up team-level rarity for a given streak."""
    freq = (
        baselines.get("team", {})
        .get(team, {})
        .get(position_group, {})
        .get(streak_type, {})
    )
    prob = freq.get(str(length), 0.0)
    if prob == 0.0 and length > 0:
        max_key = max((int(k) for k in freq), default=0)
        if length > max_key:
            prob = 0.0
    return RarityScore(level="team", probability=prob, rarity=1.0 - prob)


def _lookup_player_rarity(
    baselines: dict,
    player_id: int,
    streak_type: str,
    length: int,
) -> RarityScore:
    """Check if this streak exceeds the player's career best."""
    career_max = (
        baselines.get("player", {}).get(str(player_id), {}).get(streak_type, 0)
    )
    # If current streak > career best, probability is near-zero (brand new territory)
    if length > career_max and career_max > 0:
        prob = 0.01  # Slight epsilon — it's happening, but extremely rare for them
    elif length >= career_max and career_max > 0:
        prob = 0.10  # At career best — uncommon
    elif career_max > 0:
        prob = length / career_max  # Fraction of career best
    else:
        # No career data — cannot evaluate
        prob = 0.5
    return RarityScore(level="player", probability=prob, rarity=1.0 - prob)


# ---------------------------------------------------------------------------
# Active streak detection
# ---------------------------------------------------------------------------

def detect_active_streaks(
    game_logs: list[dict],
    min_length: int = 2,
) -> list[ActiveStreak]:
    """
    Detect all currently active streaks in the tail of a player's game log.
    A streak is 'active' if the most recent game continues it.

    Args:
        game_logs: Ordered list of game dicts (oldest first).
        min_length: Minimum streak length to report.

    Returns:
        List of ActiveStreak objects.
    """
    if len(game_logs) < min_length:
        return []

    active: list[ActiveStreak] = []

    for st_name, st_def in STREAK_TYPES.items():
        # Walk from the most recent game backwards
        tail_length = 0
        for game in reversed(game_logs):
            if st_def.predicate(game):
                tail_length += 1
            else:
                break

        if tail_length >= min_length:
            start_idx = len(game_logs) - tail_length
            recent = game_logs[start_idx:]
            active.append(ActiveStreak(
                streak_type=st_name,
                length=tail_length,
                start_date=recent[0]["game_date"],
                end_date=recent[-1]["game_date"],
                sign=st_def.sign,
                description=st_def.description,
                recent_games=recent,
            ))

    return active


# ---------------------------------------------------------------------------
# Full anomaly evaluation
# ---------------------------------------------------------------------------

def evaluate_streaks(
    player_id: int,
    player_name: str,
    team: str,
    position: str,
    season: str,
    game_logs: list[dict],
    baselines: dict,
    min_length: int = 2,
) -> list[StreakAnomaly]:
    """
    Detect active streaks and evaluate each against baselines.

    Returns a list of StreakAnomaly objects sorted by novelty_index descending.
    """
    pg = get_position_group(position)
    active_streaks = detect_active_streaks(game_logs, min_length=min_length)

    anomalies: list[StreakAnomaly] = []

    for streak in active_streaks:
        rarity_scores = [
            _lookup_league_rarity(baselines, pg, streak.streak_type, streak.length),
            _lookup_team_rarity(baselines, team, pg, streak.streak_type, streak.length),
            _lookup_player_rarity(baselines, player_id, streak.streak_type, streak.length),
        ]

        anomaly = StreakAnomaly(
            player_id=player_id,
            player_name=player_name,
            team=team,
            position=position,
            season=season,
            streak=streak,
            rarity_scores=rarity_scores,
        )
        anomalies.append(anomaly)

    anomalies.sort(key=lambda a: a.novelty_index, reverse=True)
    return anomalies


# ---------------------------------------------------------------------------
# Database query layer
# ---------------------------------------------------------------------------

def fetch_player_game_logs(
    conn: sqlite3.Connection,
    player_id: int,
    season: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Fetch recent game logs for a player, ordered oldest first.

    Args:
        conn: Open SQLite connection.
        player_id: NHL player ID.
        season: Optional season filter (e.g. '20252026').
        limit: Max games to return.

    Returns:
        List of game dicts.
    """
    if season:
        cursor = conn.execute("""
            SELECT game_id, game_date, team_abbr, opponent_abbr, home_road_flag,
                   goals, assists, points, shots, pim, plus_minus,
                   time_on_ice, shifts, power_play_goals, power_play_points,
                   shorthanded_goals, game_winning_goals, ot_goals,
                   blocked_shots, giveaways, takeaways, faceoff_pct
            FROM player_game_logs
            WHERE player_id = ? AND season = ?
            ORDER BY game_date DESC
            LIMIT ?
        """, (player_id, season, limit))
    else:
        cursor = conn.execute("""
            SELECT game_id, game_date, team_abbr, opponent_abbr, home_road_flag,
                   goals, assists, points, shots, pim, plus_minus,
                   time_on_ice, shifts, power_play_goals, power_play_points,
                   shorthanded_goals, game_winning_goals, ot_goals,
                   blocked_shots, giveaways, takeaways, faceoff_pct
            FROM player_game_logs
            WHERE player_id = ?
            ORDER BY game_date DESC
            LIMIT ?
        """, (player_id, limit))

    columns = [
        "game_id", "game_date", "team_abbr", "opponent_abbr", "home_road_flag",
        "goals", "assists", "points", "shots", "pim", "plus_minus",
        "time_on_ice", "shifts", "power_play_goals", "power_play_points",
        "shorthanded_goals", "game_winning_goals", "ot_goals",
        "blocked_shots", "giveaways", "takeaways", "faceoff_pct",
    ]

    rows = cursor.fetchall()
    # Reverse so oldest first (streak detection walks forward)
    games = [dict(zip(columns, row)) for row in reversed(rows)]
    return games


def fetch_player_info(conn: sqlite3.Connection, player_id: int) -> dict | None:
    """Fetch basic player info (name, position, team)."""
    cursor = conn.execute("""
        SELECT p.first_name, p.last_name, p.position, pts.team_abbr
        FROM players p
        LEFT JOIN player_team_seasons pts ON p.id = pts.player_id
        WHERE p.id = ?
        ORDER BY pts.season DESC
        LIMIT 1
    """, (player_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "first_name": row[0],
        "last_name": row[1],
        "position": row[2],
        "team": row[3],
    }


def fetch_active_scorers(
    conn: sqlite3.Connection,
    season: str,
    min_games: int = 10,
) -> list[dict]:
    """
    Fetch skaters who have played at least min_games in the given season.
    Returns list of {player_id, first_name, last_name, position, team}.
    """
    cursor = conn.execute("""
        SELECT
            pgl.player_id,
            p.first_name,
            p.last_name,
            p.position,
            pgl.team_abbr,
            COUNT(*) as gp
        FROM player_game_logs pgl
        JOIN players p ON pgl.player_id = p.id
        WHERE pgl.season = ? AND p.position != 'G'
        GROUP BY pgl.player_id
        HAVING gp >= ?
        ORDER BY pgl.player_id
    """, (season, min_games))

    return [
        {
            "player_id": row[0],
            "first_name": row[1],
            "last_name": row[2],
            "position": row[3],
            "team": row[4],
            "games_played": row[5],
        }
        for row in cursor.fetchall()
    ]


# ---------------------------------------------------------------------------
# Batch evaluation
# ---------------------------------------------------------------------------

def evaluate_all_players(
    season: str,
    db_path: str = DB_PATH,
    baselines_path: str | None = None,
    min_streak_length: int = 2,
    min_games: int = 10,
    min_novelty: float = 0.0,
) -> list[StreakAnomaly]:
    """
    Evaluate all active skaters for a season and return their active anomalies.

    Args:
        season: Season string (e.g. '20252026').
        db_path: Path to nhl_data.db.
        baselines_path: Path to baselines cache JSON. None = default.
        min_streak_length: Minimum streak length to detect.
        min_games: Minimum games played to be evaluated.
        min_novelty: Filter out anomalies below this novelty index.

    Returns:
        List of StreakAnomaly objects, sorted by novelty_index descending.
    """
    conn = sqlite3.connect(db_path)
    try:
        if baselines_path:
            baselines = load_baselines(baselines_path)
        else:
            baselines = load_baselines()

        players = fetch_active_scorers(conn, season, min_games)
        all_anomalies: list[StreakAnomaly] = []

        for p in players:
            games = fetch_player_game_logs(
                conn, p["player_id"], season=season, limit=40,
            )
            if len(games) < min_streak_length:
                continue

            name = f"{p['first_name']} {p['last_name']}"
            anomalies = evaluate_streaks(
                player_id=p["player_id"],
                player_name=name,
                team=p["team"],
                position=p["position"],
                season=season,
                game_logs=games,
                baselines=baselines,
                min_length=min_streak_length,
            )

            for a in anomalies:
                if a.novelty_index >= min_novelty:
                    all_anomalies.append(a)

        all_anomalies.sort(key=lambda a: a.novelty_index, reverse=True)
        logger.info(
            "Evaluated %d players, found %d anomalies (min_novelty=%.2f).",
            len(players), len(all_anomalies), min_novelty,
        )
        return all_anomalies

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="StreakAnomaly engine — detect and score active player streaks."
    )
    parser.add_argument(
        "--player-id",
        type=int,
        help="Evaluate a specific player by NHL ID.",
    )
    parser.add_argument(
        "--season",
        type=str,
        default="20252026",
        help="Season to evaluate (default: 20252026).",
    )
    parser.add_argument(
        "--all-active",
        action="store_true",
        help="Evaluate all active skaters for the season.",
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=2,
        help="Minimum streak length to detect (default: 2).",
    )
    parser.add_argument(
        "--min-novelty",
        type=float,
        default=0.5,
        help="Minimum novelty index to report (default: 0.5).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON lines.",
    )
    return parser.parse_args()


def _print_anomaly(a: StreakAnomaly) -> None:
    """Pretty-print a single anomaly."""
    print(
        f"  [{a.severity}] {a.player_name} ({a.team}, {a.position}) — "
        f"{a.streak.description}: {a.streak.length} games "
        f"({a.streak.start_date} to {a.streak.end_date})"
    )
    for r in a.rarity_scores:
        print(
            f"    {r.level:>6}: P={r.probability:.4f}  rarity={r.rarity:.4f}"
        )
    print(f"    novelty_index={a.novelty_index:.4f}")


if __name__ == "__main__":
    import json as json_mod
    args = parse_args()

    if args.all_active:
        anomalies = evaluate_all_players(
            season=args.season,
            min_streak_length=args.min_length,
            min_novelty=args.min_novelty,
        )
        if args.json:
            for a in anomalies:
                print(json_mod.dumps(a.to_dict()))
        else:
            print(f"\n=== Active Anomalies for {args.season} "
                  f"(novelty >= {args.min_novelty}) ===")
            print(f"Found {len(anomalies)} anomalies.\n")
            for a in anomalies:
                _print_anomaly(a)
                print()

    elif args.player_id:
        conn = sqlite3.connect(DB_PATH)
        try:
            info = fetch_player_info(conn, args.player_id)
            if info is None:
                print(f"Player {args.player_id} not found.")
                raise SystemExit(1)

            games = fetch_player_game_logs(
                conn, args.player_id, season=args.season, limit=40,
            )
            baselines = load_baselines()

            name = f"{info['first_name']} {info['last_name']}"
            anomalies = evaluate_streaks(
                player_id=args.player_id,
                player_name=name,
                team=info["team"],
                position=info["position"],
                season=args.season,
                game_logs=games,
                baselines=baselines,
                min_length=args.min_length,
            )

            if args.json:
                for a in anomalies:
                    print(json_mod.dumps(a.to_dict()))
            else:
                print(f"\n=== Streaks for {name} ({info['team']}) — {args.season} ===")
                if not anomalies:
                    print("No active streaks detected.")
                for a in anomalies:
                    _print_anomaly(a)
                    print()
        finally:
            conn.close()

    else:
        print("Specify --player-id <ID> or --all-active. Use --help for details.")

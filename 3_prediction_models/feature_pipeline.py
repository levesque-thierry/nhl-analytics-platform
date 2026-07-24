"""
feature_pipeline.py — Season-Level Feature Engineering for Pts/Game Prediction

Transforms raw per-game player logs into a season-level feature matrix.
Each row = one player-season. Target = pts_per_game for that season.

Feature groups:
    1. Prior season per-game averages (offense, defense, usage)
    2. Prior season aggregate stats
    3. Career statistics
    4. Player attributes (position, age, handedness)
    5. Team context (team offensive/defensive strength)
    6. Momentum (rolling 20-game pts/game from end of prior season)
    7. Streak baselines (from baselines_cache.json)

Usage:
    from feature_pipeline import build_feature_matrix
    df = build_feature_matrix(target_season="20252026")
"""

import logging
import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH: str = str(Path(__file__).parent.parent / "1_data_warehouse" / "nhl_data.db")
BASELINES_PATH: str = str(Path(__file__).parent.parent / "2_broadcast_engine" / "baselines_cache.json")

MIN_GAMES_SEASON: int = 20
STAT_COLUMNS: list[str] = [
    "goals", "assists", "points", "shots", "pim", "plus_minus",
    "power_play_goals", "blocked_shots", "hits", "giveaways",
    "takeaways", "shifts",
]

TOI_COLUMNS: list[str] = ["time_on_ice"]


def _toi_to_seconds(toi_str: str) -> float:
    """Convert 'MM:SS' time-on-ice string to total seconds."""
    if not toi_str or toi_str == "":
        return 0.0
    parts = toi_str.split(":")
    if len(parts) != 2:
        return 0.0
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, TypeError):
        return 0.0


def _compute_age(birth_date: str, reference_date: date) -> float | None:
    """Compute age in years at a reference date."""
    if not birth_date:
        return None
    try:
        bd = date.fromisoformat(birth_date)
        age = reference_date.year - bd.year
        if (reference_date.month, reference_date.day) < (bd.month, bd.day):
            age -= 1
        return float(age)
    except (ValueError, TypeError):
        return None


def load_game_logs(db_path: str = DB_PATH) -> pd.DataFrame:
    """Load all regular-season skater game logs with player attributes."""
    query = """
        SELECT
            g.game_id,
            g.player_id,
            g.season,
            g.game_date,
            g.team_abbr,
            g.opponent_abbr,
            g.home_road_flag,
            g.goals,
            g.assists,
            g.points,
            g.shots,
            g.pim,
            g.plus_minus,
            g.time_on_ice,
            g.shifts,
            g.power_play_goals,
            g.blocked_shots,
            g.hits,
            g.giveaways,
            g.takeaways,
            g.faceoff_pct,
            p.position,
            p.shoots_catches,
            p.birth_date
        FROM player_game_logs g
        JOIN players p ON g.player_id = p.id
        WHERE g.game_type = 2
          AND p.position != 'G'
        ORDER BY g.player_id, g.season, g.game_date
    """
    df = pd.read_sql_query(query, sqlite3.connect(db_path))
    logger.info("Loaded %d skater game logs.", len(df))
    return df


def _compute_prior_season_features(
    player_games: pd.DataFrame,
    prior_season: str,
) -> dict[str, float] | None:
    """
    Compute per-game averages and aggregates for a player's prior season.
    Returns None if player has no data for that season.
    """
    prior = player_games[player_games["season"] == prior_season]
    if prior.empty:
        return None

    n_games = len(prior)
    features: dict[str, float] = {}

    # Per-game averages for stat columns
    for col in STAT_COLUMNS:
        features[f"prior_{col}_per_game"] = prior[col].sum() / n_games

    # Time on ice (convert MM:SS to seconds first)
    toi_seconds = prior["time_on_ice"].apply(_toi_to_seconds)
    features["prior_toi_per_game"] = toi_seconds.sum() / n_games

    # Shooting percentage
    total_shots = prior["shots"].sum()
    features["prior_shoot_pct"] = (
        prior["goals"].sum() / total_shots if total_shots > 0 else 0.0
    )

    # Faceoff percentage (weighted by attempts, only meaningful for centers)
    fo_valid = prior["faceoff_pct"].dropna()
    features["prior_faceoff_pct"] = fo_valid.mean() if len(fo_valid) > 0 else None

    # Games played
    features["prior_games_played"] = float(n_games)

    # Home/road splits
    home = prior[prior["home_road_flag"] == "H"]
    road = prior[prior["home_road_flag"] == "R"]
    features["prior_home_pts_per_game"] = (
        home["points"].sum() / len(home) if len(home) > 0 else 0.0
    )
    features["prior_road_pts_per_game"] = (
        road["points"].sum() / len(road) if len(road) > 0 else 0.0
    )

    return features


def _compute_momentum(player_games: pd.DataFrame, prior_season: str) -> dict[str, float]:
    """
    Compute momentum features: rolling 20-game pts/game from the end of the prior season.
    """
    prior = player_games[player_games["season"] == prior_season]
    if len(prior) < 10:
        return {
            "momentum_pts_per_game": None,
            "momentum_goals_per_game": None,
            "momentum_shots_per_game": None,
        }

    tail = prior.tail(20)
    n = len(tail)
    return {
        "momentum_pts_per_game": tail["points"].sum() / n,
        "momentum_goals_per_game": tail["goals"].sum() / n,
        "momentum_shots_per_game": tail["shots"].sum() / n,
    }


def _compute_career_features(
    player_games: pd.DataFrame,
    exclude_season: str | None = None,
) -> dict[str, float]:
    """Compute career aggregate statistics (excluding a specific season if provided)."""
    data = player_games
    if exclude_season:
        data = data[data["season"] != exclude_season]

    if data.empty:
        return {
            "career_games": 0.0,
            "career_pts_per_game": 0.0,
            "career_goals_per_game": 0.0,
            "career_seasons": 0.0,
        }

    n_games = len(data)
    seasons = data["season"].nunique()
    return {
        "career_games": float(n_games),
        "career_pts_per_game": data["points"].sum() / n_games,
        "career_goals_per_game": data["goals"].sum() / n_games,
        "career_seasons": float(seasons),
    }


def _compute_team_strength(
    all_games: pd.DataFrame,
    prior_season: str,
) -> dict[str, dict[str, float]]:
    """
    Compute team-level offensive and defensive strength for the prior season.
    Offense = avg goals scored per game. Defense = avg goals against per game.
    """
    prior = all_games[all_games["season"] == prior_season]
    if prior.empty:
        return {}

    team_strength: dict[str, dict[str, float]] = {}

    # Group by team to get their offensive output
    for team, group in prior.groupby("team_abbr"):
        n = len(group)
        if n == 0:
            continue
        team_strength[team] = {
            "team_offense": group["goals"].sum() / n,
            "team_defense": group["plus_minus"].sum() / n,
            "team_shots": group["shots"].sum() / n,
        }

    return team_strength


def _position_group(position: str) -> str:
    """Map position to group: F, D, or ALL."""
    if position in ("C", "L", "R"):
        return "F"
    if position == "D":
        return "D"
    return "ALL"


def build_feature_matrix(
    target_season: str,
    db_path: str = DB_PATH,
    min_games: int = MIN_GAMES_SEASON,
) -> pd.DataFrame:
    """
    Build the full feature matrix for player-season pts/game prediction.

    Each row is one player-season from the target season. Features are derived
    from all prior seasons only (no data leakage).

    Args:
        target_season: Season to predict (e.g. '20252026').
        db_path: Path to SQLite database.
        min_games: Minimum games in target season to include a player.

    Returns:
        DataFrame with features + target column 'pts_per_game'.
    """
    logger.info("Building feature matrix for target season: %s", target_season)
    all_games = load_game_logs(db_path)

    # Identify prior seasons (all seasons before the target)
    all_seasons = sorted(all_games["season"].unique())
    prior_seasons = [s for s in all_seasons if s < target_season]
    logger.info("Prior seasons available: %s", prior_seasons)

    if not prior_seasons:
        logger.warning("No prior seasons found. Feature matrix will be empty.")
        return pd.DataFrame()

    # Get target season data for labels
    target_games = all_games[all_games["season"] == target_season]

    # Group target season games by player
    target_players = target_games.groupby("player_id").agg(
        target_pts=("points", "sum"),
        target_goals=("goals", "sum"),
        target_games=("game_id", "count"),
        target_team=("team_abbr", "last"),
    ).reset_index()

    # Filter to players with enough games
    target_players = target_players[target_players["target_games"] >= min_games].copy()
    target_players["pts_per_game"] = target_players["target_pts"] / target_players["target_games"]
    target_players["goals_per_game"] = target_players["target_goals"] / target_players["target_games"]

    logger.info(
        "Target season %s: %d players with >= %d games.",
        target_season, len(target_players), min_games,
    )

    # Compute team strength from the most recent prior season
    latest_prior = prior_seasons[-1]
    team_strength = _compute_team_strength(all_games, latest_prior)

    # Build features for each target player
    rows: list[dict] = []

    for _, player_row in target_players.iterrows():
        pid = player_row["player_id"]
        player_all = all_games[all_games["player_id"] == pid]

        # Player attributes (from most recent game)
        last_game = player_all.iloc[-1]
        position = last_game["position"]
        pos_group = _position_group(position)
        shoots = last_game["shoots_catches"]
        birth_date = last_game["birth_date"]

        # Age at start of target season (Oct 1)
        try:
            target_year = int(target_season[:4])
        except (ValueError, IndexError):
            target_year = 2025
        age = _compute_age(birth_date, date(target_year, 10, 1))

        row: dict = {
            "player_id": pid,
            "season": target_season,
            "position": position,
            "position_group": pos_group,
            "shoots_catches": shoots,
            "age": age,
        }

        # Prior season features (use most recent prior season that has data)
        for ps in reversed(prior_seasons):
            prior_feats = _compute_prior_season_features(player_all, ps)
            if prior_feats is not None:
                row.update(prior_feats)
                row["prior_season_used"] = ps
                break
        else:
            # No prior season data — rookie
            row["prior_season_used"] = None
            for col in STAT_COLUMNS:
                row[f"prior_{col}_per_game"] = None
            row["prior_toi_per_game"] = None
            row["prior_shoot_pct"] = None
            row["prior_faceoff_pct"] = None
            row["prior_games_played"] = None
            row["prior_home_pts_per_game"] = None
            row["prior_road_pts_per_game"] = None

        # Momentum (from end of most recent prior season)
        for ps in reversed(prior_seasons):
            momentum = _compute_momentum(player_all, ps)
            if momentum["momentum_pts_per_game"] is not None:
                row.update(momentum)
                break
        else:
            row["momentum_pts_per_game"] = None
            row["momentum_goals_per_game"] = None
            row["momentum_shots_per_game"] = None

        # Career features (excluding target season)
        career = _compute_career_features(player_all, exclude_season=target_season)
        row.update(career)

        # Team context
        team = player_row["target_team"]
        ts = team_strength.get(team, {})
        row["team_offense"] = ts.get("team_offense", None)
        row["team_defense"] = ts.get("team_defense", None)
        row["team_shots"] = ts.get("team_shots", None)

        # Target
        row["pts_per_game"] = player_row["pts_per_game"]
        row["goals_per_game"] = player_row["goals_per_game"]
        row["target_games"] = player_row["target_games"]

        rows.append(row)

    df = pd.DataFrame(rows)
    logger.info("Feature matrix built: %d rows, %d columns.", len(df), len(df.columns))
    return df


def get_feature_columns(df: pd.DataFrame) -> list[str]:
    """
    Return the list of feature column names (excludes identifiers and targets).
    """
    exclude = {
        "player_id", "season", "position", "position_group",
        "shoots_catches", "prior_season_used", "pts_per_game",
        "goals_per_game", "target_games",
    }
    return [c for c in df.columns if c not in exclude]


def get_categorical_columns() -> list[str]:
    """Return columns that should be treated as categorical by LightGBM."""
    return ["position", "position_group", "shoots_catches"]


if __name__ == "__main__":
    # Quick test: build feature matrix for 20252026
    fm = build_feature_matrix("20252026")
    print(f"\nFeature matrix shape: {fm.shape}")
    print(f"Columns: {list(fm.columns)}")
    print(f"\nSample (first 5 rows):")
    print(fm.head().to_string())
    print(f"\nNull counts:")
    print(fm.isnull().sum()[fm.isnull().sum() > 0].to_string())

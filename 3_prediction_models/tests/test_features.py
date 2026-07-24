"""
test_features.py — Tests for the Prediction Model Feature Pipeline

Tests feature engineering functions, edge cases, and data quality.
Run: pytest 3_prediction_models/tests/test_features.py -v
"""

import sqlite3
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from feature_pipeline import (
    _compute_age,
    _compute_career_features,
    _compute_momentum,
    _compute_prior_season_features,
    _compute_team_strength,
    _position_group,
    _toi_to_seconds,
    build_feature_matrix,
    get_categorical_columns,
    get_feature_columns,
)

DB_PATH = str(Path(__file__).parent.parent.parent / "1_data_warehouse" / "nhl_data.db")


# ---------------------------------------------------------------------------
# Unit tests: helper functions
# ---------------------------------------------------------------------------

class TestToiConversion:
    """Tests for time_on_ice string to seconds conversion."""

    def test_normal_toi(self) -> None:
        assert _toi_to_seconds("20:30") == 1230

    def test_zero_toi(self) -> None:
        assert _toi_to_seconds("0:00") == 0

    def test_empty_string(self) -> None:
        assert _toi_to_seconds("") == 0.0

    def test_none_toi(self) -> None:
        assert _toi_to_seconds(None) == 0.0  # type: ignore[arg-type]

    def test_invalid_format(self) -> None:
        assert _toi_to_seconds("abc") == 0.0

    def test_high_toi(self) -> None:
        # 30:00 = 1800 seconds
        assert _toi_to_seconds("30:00") == 1800


class TestAgeComputation:
    """Tests for age calculation from birth date."""

    def test_birthday_before_reference(self) -> None:
        # Born 2000-01-15, reference 2025-10-01 → age 25
        assert _compute_age("2000-01-15", date(2025, 10, 1)) == 25.0

    def test_birthday_after_reference(self) -> None:
        # Born 2000-12-15, reference 2025-10-01 → age 24
        assert _compute_age("2000-12-15", date(2025, 10, 1)) == 24.0

    def test_birthday_on_reference(self) -> None:
        # Born 2000-10-01, reference 2025-10-01 → age 25
        assert _compute_age("2000-10-01", date(2025, 10, 1)) == 25.0

    def test_empty_birth_date(self) -> None:
        assert _compute_age("", date(2025, 10, 1)) is None

    def test_none_birth_date(self) -> None:
        assert _compute_age(None, date(2025, 10, 1)) is None  # type: ignore[arg-type]


class TestPositionGroup:
    """Tests for position to group mapping."""

    def test_center(self) -> None:
        assert _position_group("C") == "F"

    def test_left_wing(self) -> None:
        assert _position_group("L") == "F"

    def test_right_wing(self) -> None:
        assert _position_group("R") == "F"

    def test_defenseman(self) -> None:
        assert _position_group("D") == "D"

    def test_unknown_position(self) -> None:
        assert _position_group("G") == "ALL"


# ---------------------------------------------------------------------------
# Unit tests: feature computation on synthetic data
# ---------------------------------------------------------------------------

def _make_game_log(
    player_id: int,
    season: str,
    n_games: int,
    goals_per_game: float = 0.5,
    assists_per_game: float = 0.5,
    shots_per_game: float = 3.0,
    position: str = "C",
) -> pd.DataFrame:
    """Create a synthetic game log DataFrame for testing."""
    rows = []
    for i in range(n_games):
        rows.append({
            "player_id": player_id,
            "season": season,
            "game_date": f"2024-{(i // 10) + 1:02d}-{(i % 10) + 1:02d}",
            "team_abbr": "EDM",
            "opponent_abbr": "TOR",
            "home_road_flag": "H" if i % 2 == 0 else "R",
            "goals": int(goals_per_game * (1 + 0.2 * np.random.randn())),
            "assists": int(assists_per_game * (1 + 0.2 * np.random.randn())),
            "points": int((goals_per_game + assists_per_game) * (1 + 0.2 * np.random.randn())),
            "shots": int(shots_per_game * (1 + 0.2 * np.random.randn())),
            "pim": 0,
            "plus_minus": 0,
            "time_on_ice": "18:00",
            "shifts": 20,
            "power_play_goals": 0,
            "blocked_shots": 1,
            "hits": 2,
            "giveaways": 1,
            "takeaways": 1,
            "faceoff_pct": 0.55 if position == "C" else None,
            "position": position,
            "shoots_catches": "R",
            "birth_date": "2000-01-15",
        })
    return pd.DataFrame(rows)


class TestPriorSeasonFeatures:
    """Tests for prior season feature computation."""

    def test_basic_features(self) -> None:
        games = _make_game_log(1, "20242025", n_games=40)
        result = _compute_prior_season_features(games, "20242025")
        assert result is not None
        assert result["prior_games_played"] == 40.0
        assert result["prior_goals_per_game"] >= 0
        assert result["prior_assists_per_game"] >= 0
        assert result["prior_shots_per_game"] > 0

    def test_missing_season_returns_none(self) -> None:
        games = _make_game_log(1, "20242025", n_games=40)
        result = _compute_prior_season_features(games, "20992100")
        assert result is None

    def test_empty_dataframe(self) -> None:
        games = pd.DataFrame(columns=["season", "goals", "assists", "points", "shots",
                                       "time_on_ice", "faceoff_pct", "home_road_flag"])
        result = _compute_prior_season_features(games, "20242025")
        assert result is None

    def test_home_road_split(self) -> None:
        games = _make_game_log(1, "20242025", n_games=40)
        result = _compute_prior_season_features(games, "20242025")
        assert result is not None
        assert "prior_home_pts_per_game" in result
        assert "prior_road_pts_per_game" in result


class TestMomentum:
    """Tests for momentum feature computation."""

    def test_basic_momentum(self) -> None:
        games = _make_game_log(1, "20242025", n_games=30)
        result = _compute_momentum(games, "20242025")
        assert "momentum_pts_per_game" in result
        assert "momentum_goals_per_game" in result
        assert "momentum_shots_per_game" in result

    def test_too_few_games(self) -> None:
        games = _make_game_log(1, "20242025", n_games=5)
        result = _compute_momentum(games, "20242025")
        assert result["momentum_pts_per_game"] is None

    def test_empty_season(self) -> None:
        games = pd.DataFrame(columns=["season", "goals", "shots", "points"])
        result = _compute_momentum(games, "20242025")
        assert result["momentum_pts_per_game"] is None


class TestCareerFeatures:
    """Tests for career feature computation."""

    def test_multi_season_career(self) -> None:
        s1 = _make_game_log(1, "20232024", n_games=82)
        s2 = _make_game_log(1, "20242025", n_games=70)
        all_games = pd.concat([s1, s2], ignore_index=True)
        result = _compute_career_features(all_games)
        assert result["career_games"] == 152.0
        assert result["career_seasons"] == 2.0
        assert result["career_pts_per_game"] > 0

    def test_exclude_season(self) -> None:
        s1 = _make_game_log(1, "20232024", n_games=82)
        s2 = _make_game_log(1, "20242025", n_games=70)
        all_games = pd.concat([s1, s2], ignore_index=True)
        result = _compute_career_features(all_games, exclude_season="20242025")
        assert result["career_games"] == 82.0
        assert result["career_seasons"] == 1.0

    def test_empty_career(self) -> None:
        result = _compute_career_features(pd.DataFrame())
        assert result["career_games"] == 0.0
        assert result["career_pts_per_game"] == 0.0


class TestTeamStrength:
    """Tests for team strength computation."""

    def test_basic_team_strength(self) -> None:
        games = _make_game_log(1, "20242025", n_games=40)
        result = _compute_team_strength(games, "20242025")
        assert "EDM" in result
        assert "team_offense" in result["EDM"]
        assert "team_defense" in result["EDM"]
        assert result["EDM"]["team_offense"] >= 0

    def test_missing_season(self) -> None:
        games = _make_game_log(1, "20242025", n_games=40)
        result = _compute_team_strength(games, "20992100")
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Integration tests: full feature matrix (requires DB)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not Path(DB_PATH).exists(),
    reason="Database not found",
)
class TestFeatureMatrix:
    """Integration tests against the real database."""

    def test_build_matrix_shape(self) -> None:
        fm = build_feature_matrix("20252026")
        assert len(fm) > 0
        assert "pts_per_game" in fm.columns
        assert "player_id" in fm.columns

    def test_feature_columns_defined(self) -> None:
        fm = build_feature_matrix("20252026")
        feature_cols = get_feature_columns(fm)
        assert len(feature_cols) > 0
        assert "player_id" not in feature_cols
        assert "pts_per_game" not in feature_cols

    def test_categorical_columns(self) -> None:
        cat_cols = get_categorical_columns()
        assert "position" in cat_cols
        assert "position_group" in cat_cols

    def test_no_target_leakage(self) -> None:
        fm = build_feature_matrix("20252026")
        feature_cols = get_feature_columns(fm)
        # No feature should contain 'target' or 'pts_per_game' in the name
        for col in feature_cols:
            assert "target" not in col.lower()
            assert col != "pts_per_game"
            assert col != "goals_per_game"

    def test_min_games_filter(self) -> None:
        fm = build_feature_matrix("20252026", min_games=40)
        assert (fm["target_games"] >= 40).all()

    def test_prior_season_used_is_valid(self) -> None:
        fm = build_feature_matrix("20252026")
        # Players with prior seasons should have a valid prior_season_used
        with_prior = fm[fm["prior_season_used"].notna()]
        assert len(with_prior) > 0
        for season in with_prior["prior_season_used"]:
            assert season in ("20232024", "20242025")

"""
test_compare.py — Tests for the Benchmark Comparison Engine

Tests player matching, metric computation, and edge cases.
Run: pytest 4_benchmark/tests/test_compare.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Add paths
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "3_prediction_models"))

from compare import _compute_metrics, _match_players, compute_summary_metrics
from fetch_moneypuck import load_moneypuck_data


# ---------------------------------------------------------------------------
# Unit tests: metric computation
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    """Tests for regression metric computation."""

    def test_perfect_prediction(self) -> None:
        y = np.array([1.0, 2.0, 3.0, 4.0])
        result = _compute_metrics(y, y)
        assert result["mae"] == 0.0
        assert result["rmse"] == 0.0
        assert result["r2"] == 1.0
        assert result["corr"] == pytest.approx(1.0)
        assert result["n"] == 4

    def test_known_error(self) -> None:
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.1, 2.1, 3.1])
        result = _compute_metrics(y_true, y_pred)
        assert result["mae"] == pytest.approx(0.1)
        assert result["n"] == 3

    def test_with_nan(self) -> None:
        y_true = np.array([1.0, 2.0, np.nan, 4.0])
        y_pred = np.array([1.0, 2.5, 3.0, 3.5])
        result = _compute_metrics(y_true, y_pred)
        assert result["n"] == 3  # NaN row excluded

    def test_empty_arrays(self) -> None:
        y = np.array([])
        result = _compute_metrics(y, y)
        assert np.isnan(result["mae"])
        assert result["n"] == 0


# ---------------------------------------------------------------------------
# Unit tests: player matching
# ---------------------------------------------------------------------------

class TestMatchPlayers:
    """Tests for player matching between our data and MoneyPuck."""

    def test_basic_match(self) -> None:
        our_df = pd.DataFrame({
            "first_name": ["Connor", "Leon"],
            "last_name": ["McDavid", "Draisaitl"],
            "player_id": [8478402, 8477953],
            "pts_per_game": [1.5, 1.3],
            "goals_per_game": [0.6, 0.7],
            "projected_pts_per_game": [1.4, 1.2],
            "target_games": [82, 80],
        })
        mp_df = pd.DataFrame({
            "name": ["Connor McDavid", "Leon Draisaitl", "Nathan MacKinnon"],
            "playerId": [8478402, 8477953, 8477492],
            "team": ["EDM", "EDM", "COL"],
            "position": ["C", "C", "C"],
            "I_F_xGoals": [30.0, 28.0, 35.0],
            "I_F_goals": [40.0, 35.0, 45.0],
            "I_F_points": [110.0, 100.0, 120.0],
            "I_F_primaryAssists": [50.0, 40.0, 45.0],
            "I_F_secondaryAssists": [20.0, 25.0, 30.0],
            "I_F_shotsOnGoal": [300.0, 250.0, 350.0],
            "games_played": [82, 80, 82],
        })

        result = _match_players(our_df, mp_df)
        assert len(result) == 2  # Only Connor and Leon match
        assert "Connor" in result["first_name"].values

    def test_no_match(self) -> None:
        our_df = pd.DataFrame({
            "first_name": ["Unknown"],
            "last_name": ["Player"],
        })
        mp_df = pd.DataFrame({
            "name": ["Connor McDavid"],
            "playerId": [8478402],
            "team": ["EDM"],
            "position": ["C"],
            "I_F_xGoals": [30.0],
            "I_F_goals": [40.0],
            "I_F_points": [110.0],
            "I_F_primaryAssists": [50.0],
            "I_F_secondaryAssists": [20.0],
            "I_F_shotsOnGoal": [300.0],
            "games_played": [82],
        })
        result = _match_players(our_df, mp_df)
        assert len(result) == 0

    def test_case_insensitive(self) -> None:
        our_df = pd.DataFrame({
            "first_name": ["connor"],
            "last_name": ["mcdavid"],
        })
        mp_df = pd.DataFrame({
            "name": ["Connor McDavid"],
            "playerId": [8478402],
            "team": ["EDM"],
            "position": ["C"],
            "I_F_xGoals": [30.0],
            "I_F_goals": [40.0],
            "I_F_points": [110.0],
            "I_F_primaryAssists": [50.0],
            "I_F_secondaryAssists": [20.0],
            "I_F_shotsOnGoal": [300.0],
            "games_played": [82],
        })
        result = _match_players(our_df, mp_df)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Unit tests: summary metrics
# ---------------------------------------------------------------------------

class TestSummaryMetrics:
    """Tests for summary metrics computation."""

    def test_basic_summary(self) -> None:
        comp = pd.DataFrame({
            "actual_pts_per_game": [1.0, 1.2, 0.8],
            "our_predicted_pts_per_game": [0.9, 1.1, 0.85],
            "actual_goals_per_game": [0.5, 0.6, 0.4],
            "mp_xgoals_per_game": [0.48, 0.58, 0.42],
        })
        result = compute_summary_metrics(comp)
        assert "our_model" in result
        assert "moneypuck" in result
        assert result["our_model"]["n"] == 3
        assert result["moneypuck"]["n"] == 3

    def test_empty_comparison(self) -> None:
        comp = pd.DataFrame()
        result = compute_summary_metrics(comp)
        assert result == {}


# ---------------------------------------------------------------------------
# Integration tests: MoneyPuck data loading
# ---------------------------------------------------------------------------

class TestMoneyPuckData:
    """Integration tests for MoneyPuck data loading."""

    def test_load_existing_data(self) -> None:
        df = load_moneypuck_data("20252026")
        assert df is not None
        assert len(df) > 0
        assert "name" in df.columns
        assert "I_F_xGoals" in df.columns
        assert "games_played" in df.columns

    def test_data_filtered_to_all_situations(self) -> None:
        df = load_moneypuck_data("20252026")
        assert df is not None
        # Should only have 'all' situation after fetch filtering
        if "situation" in df.columns:
            assert (df["situation"] == "all").all()

    def test_missing_season_returns_none(self) -> None:
        df = load_moneypuck_data("19992000")
        assert df is None

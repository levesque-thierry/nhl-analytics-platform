"""
test_experts.py — Tests for Expert Projection Loader & Hockey Reference Scraper

Tests source normalization, team normalization, name parsing, metric computation,
matching, and integration with real data.

Run: pytest 4_benchmark/tests/test_experts.py -v
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fetch_experts import (
    _is_source_column,
    _normalize_source,
    _normalize_team,
    _parse_name,
    compute_expert_metrics,
    load_actual_results,
    load_expert_projections,
    match_experts_to_actuals,
)


# ---------------------------------------------------------------------------
# Unit tests: source normalization
# ---------------------------------------------------------------------------

class TestNormalizeSource:
    """Tests for _normalize_source helper."""

    def test_hm_alias(self) -> None:
        assert _normalize_source("HM") == "Hockey Le Magazine"

    def test_pp_alias(self) -> None:
        assert _normalize_source("PP") == "PoolPro"

    def test_hn_alias(self) -> None:
        assert _normalize_source("HN") == "Hockey News"

    def test_for_alias(self) -> None:
        assert _normalize_source("FOR") == "Sports Forecaster"

    def test_lepool_alias(self) -> None:
        assert _normalize_source("lepool.com") == "Lepool"

    def test_ath_alias(self) -> None:
        assert _normalize_source("ATH") == "The Athletic"

    def test_passthrough(self) -> None:
        assert _normalize_source("ESPN") == "ESPN"

    def test_cbs(self) -> None:
        assert _normalize_source("CBS") == "CBS"

    def test_lowercase(self) -> None:
        assert _normalize_source("espn") == "ESPN"


# ---------------------------------------------------------------------------
# Unit tests: is_source_column
# ---------------------------------------------------------------------------

class TestIsSourceColumn:
    """Tests for _is_source_column filter."""

    def test_valid_sources(self) -> None:
        assert _is_source_column("ESPN") is True
        assert _is_source_column("CBS") is True
        assert _is_source_column("HN") is True
        assert _is_source_column("FOR") is True

    def test_skip_unnamed(self) -> None:
        assert _is_source_column("Unnamed: 3") is False

    def test_skip_colonne(self) -> None:
        assert _is_source_column("Colonne1") is False

    def test_skip_moy(self) -> None:
        assert _is_source_column("MOY") is False

    def test_skip_rg(self) -> None:
        assert _is_source_column("RG") is False

    def test_skip_age_variants(self) -> None:
        assert _is_source_column("Âge") is False
        assert _is_source_column("ège") is False

    def test_skip_ecart(self) -> None:
        assert _is_source_column("Ecart") is False
        assert _is_source_column("écart-type") is False

    def test_skip_hm_duplicate(self) -> None:
        assert _is_source_column("HM.1") is False
        assert _is_source_column("HM.2") is False


# ---------------------------------------------------------------------------
# Unit tests: team normalization
# ---------------------------------------------------------------------------

class TestNormalizeTeam:
    """Tests for _normalize_team helper."""

    def test_standard_teams(self) -> None:
        assert _normalize_team("EDM") == "EDM"
        assert _normalize_team("BOS") == "BOS"
        assert _normalize_team("TOR") == "TOR"

    def test_win_to_wpg(self) -> None:
        assert _normalize_team("WIN") == "WPG"

    def test_tb_to_tbl(self) -> None:
        assert _normalize_team("TB") == "TBL"

    def test_flo_to_fla(self) -> None:
        assert _normalize_team("FLO") == "FLA"

    def test_sj_to_sjs(self) -> None:
        assert _normalize_team("SJ") == "SJS"

    def test_empty(self) -> None:
        assert _normalize_team("") == ""

    def test_unknown_passthrough(self) -> None:
        assert _normalize_team("XYZ") == "XYZ"

    def test_lowercase(self) -> None:
        assert _normalize_team("edm") == "EDM"


# ---------------------------------------------------------------------------
# Unit tests: name parsing
# ---------------------------------------------------------------------------

class TestParseName:
    """Tests for _parse_name helper."""

    def test_two_parts(self) -> None:
        assert _parse_name("Connor McDavid") == ("Connor", "McDavid")

    def test_three_parts(self) -> None:
        assert _parse_name("Jean-Sebastien Dea") == ("Jean-Sebastien", "Dea")

    def test_single_part(self) -> None:
        assert _parse_name("Ovechkin") == ("", "Ovechkin")

    def test_empty(self) -> None:
        assert _parse_name("") == ("", "")


# ---------------------------------------------------------------------------
# Unit tests: metric computation
# ---------------------------------------------------------------------------

class TestComputeExpertMetrics:
    """Tests for compute_expert_metrics."""

    def _make_matched(self, projected: list[float], actual: list[float], source: str = "Test", season: int = 20232024) -> pd.DataFrame:
        return pd.DataFrame({
            "source": [source] * len(projected),
            "season_id": [season] * len(projected),
            "projected_points": projected,
            "actual_points": actual,
        })

    def test_perfect_predictions(self) -> None:
        pts = [60, 65, 70, 75, 80]
        matched = self._make_matched(pts, pts)
        metrics = compute_expert_metrics(matched)
        assert len(metrics) == 1
        assert metrics.iloc[0]["mae"] == 0.0
        assert metrics.iloc[0]["mean_bias"] == 0.0
        assert metrics.iloc[0]["within_10_pct"] == 100.0

    def test_known_bias(self) -> None:
        matched = self._make_matched([70, 75, 80, 85, 90], [60, 65, 70, 75, 80])
        metrics = compute_expert_metrics(matched)
        assert metrics.iloc[0]["mean_bias"] == pytest.approx(10.0)
        assert metrics.iloc[0]["mae"] == pytest.approx(10.0)

    def test_nan_actual_excluded(self) -> None:
        matched = pd.DataFrame({
            "source": ["Test"] * 6,
            "season_id": [20232024] * 6,
            "projected_points": [60.0, 65.0, 70.0, 75.0, 80.0, 90.0],
            "actual_points": [60.0, 65.0, 70.0, np.nan, 80.0, 90.0],
        })
        metrics = compute_expert_metrics(matched)
        assert metrics.iloc[0]["n_players"] == 5

    def test_fewer_than_5_skipped(self) -> None:
        matched = self._make_matched([60, 70], [60, 70])
        metrics = compute_expert_metrics(matched)
        assert len(metrics) == 0

    def test_correlation(self) -> None:
        matched = self._make_matched([50, 55, 60, 65, 70], [50, 55, 60, 65, 70])
        metrics = compute_expert_metrics(matched)
        assert metrics.iloc[0]["correlation"] == pytest.approx(1.0)

    def test_within_thresholds(self) -> None:
        matched = self._make_matched([60, 65, 70, 80, 100], [60, 65, 70, 80, 50])
        metrics = compute_expert_metrics(matched)
        row = metrics.iloc[0]
        assert row["within_5_pct"] == 80.0  # 4 of 5 within 5
        assert row["within_10_pct"] == 80.0
        assert row["within_15_pct"] == 80.0

    def test_multi_season(self) -> None:
        matched = pd.concat([
            self._make_matched([60, 70, 80, 90, 100], [60, 70, 80, 90, 100], season=20232024),
            self._make_matched([60, 70, 80, 90, 100], [62, 68, 82, 88, 102], season=20242025),
        ], ignore_index=True)
        metrics = compute_expert_metrics(matched)
        assert len(metrics) == 2


# ---------------------------------------------------------------------------
# Unit tests: matching
# ---------------------------------------------------------------------------

class TestMatchExpertsToActuals:
    """Tests for match_experts_to_actuals."""

    def test_basic_match(self) -> None:
        experts = pd.DataFrame({
            "first_name": ["Connor", "Leon"],
            "last_name": ["McDavid", "Draisaitl"],
            "player_name": ["Connor McDavid", "Leon Draisaitl"],
            "source": ["ESPN", "ESPN"],
            "projected_points": [110, 95],
            "season_id": [20232024, 20232024],
        })
        actuals = pd.DataFrame({
            "first_name": ["Connor", "Leon"],
            "last_name": ["McDavid", "Draisaitl"],
            "points": [130, 110],
            "games_played": [82, 80],
            "team": ["EDM", "EDM"],
            "season_id": [20232024, 20232024],
        })
        matched = match_experts_to_actuals(experts, actuals)
        assert len(matched) == 2
        assert matched.iloc[0]["error"] == pytest.approx(-20.0)
        assert matched.iloc[0]["abs_error"] == pytest.approx(20.0)

    def test_no_match(self) -> None:
        experts = pd.DataFrame({
            "first_name": ["Unknown"],
            "last_name": ["Player"],
            "player_name": ["Unknown Player"],
            "source": ["ESPN"],
            "projected_points": [60],
            "season_id": [20232024],
        })
        actuals = pd.DataFrame({
            "first_name": ["Connor"],
            "last_name": ["McDavid"],
            "points": [130],
            "games_played": [82],
            "team": ["EDM"],
            "season_id": [20232024],
        })
        matched = match_experts_to_actuals(experts, actuals)
        assert len(matched) == 1
        assert pd.isna(matched.iloc[0]["actual_points"])

    def test_multi_source_dedup(self) -> None:
        experts = pd.DataFrame({
            "first_name": ["Connor", "Connor"],
            "last_name": ["McDavid", "McDavid"],
            "player_name": ["Connor McDavid", "Connor McDavid"],
            "source": ["ESPN", "CBS"],
            "projected_points": [110, 105],
            "season_id": [20232024, 20232024],
        })
        actuals = pd.DataFrame({
            "first_name": ["Connor"],
            "last_name": ["McDavid"],
            "points": [130],
            "games_played": [82],
            "team": ["EDM"],
            "season_id": [20232024],
        })
        matched = match_experts_to_actuals(experts, actuals)
        assert len(matched) == 2
        assert all(matched["actual_points"] == 130)


# ---------------------------------------------------------------------------
# Integration tests: real data loading
# ---------------------------------------------------------------------------

class TestExpertProjections:
    """Integration tests against real Excel files."""

    def test_load_all_seasons(self) -> None:
        experts = load_expert_projections()
        assert len(experts) > 5000
        assert experts["source"].nunique() >= 6
        assert len(experts["season_id"].unique()) >= 6

    def test_load_single_season(self) -> None:
        experts = load_expert_projections(seasons=[2018])
        assert len(experts) > 500
        assert (experts["season_id"] == 20182019).all()

    def test_all_sources_have_projections(self) -> None:
        experts = load_expert_projections()
        for source in ["Hockey Le Magazine", "PoolPro", "Hockey News"]:
            n = (experts["source"] == source).sum()
            assert n > 500, f"{source} has only {n} projections"

    def test_projected_points_positive(self) -> None:
        experts = load_expert_projections()
        assert (experts["projected_points"] > 0).all()


class TestActualResults:
    """Integration tests against cached actual results."""

    def test_load_from_cache(self) -> None:
        actuals = load_actual_results()
        assert len(actuals) > 5000
        assert "points" in actuals.columns
        assert "season_id" in actuals.columns

    def test_all_seasons_present(self) -> None:
        actuals = load_actual_results()
        seasons = sorted(actuals["season_id"].unique())
        assert len(seasons) >= 6

    def test_points_non_negative(self) -> None:
        actuals = load_actual_results()
        assert (actuals["points"] >= 0).all()


class TestEndToEnd:
    """End-to-end test: load, match, compute metrics."""

    def test_full_pipeline(self) -> None:
        experts = load_expert_projections()
        actuals = load_actual_results()
        matched = match_experts_to_actuals(experts, actuals)
        assert len(matched) > 4000

        metrics = compute_expert_metrics(matched)
        assert len(metrics) > 10
        assert all(metrics["mae"] > 0)
        assert all(metrics["mae"] < 50)

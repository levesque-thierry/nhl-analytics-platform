"""
test_streak_engine.py — StreakAnomaly Engine Test Suites

Comprehensive pytest tests covering:
    - Streak extraction logic (positive, negative, gaps)
    - Active streak detection (tail of game log)
    - Rarity score lookups (league, team, player)
    - StreakAnomaly composite scoring and severity
    - Edge cases: empty input, single game, injury gaps, multiple streaks
    - Integration with baselines cache
"""

import sys
from pathlib import Path

import pytest

# Ensure imports resolve from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "2_broadcast_engine"))
sys.path.insert(0, str(Path(__file__).parent.parent / "1_data_warehouse"))

from baselines import STREAK_TYPES, extract_streaks, get_position_group
from streak_engine import (
    ActiveStreak,
    RarityScore,
    StreakAnomaly,
    _lookup_league_rarity,
    _lookup_player_rarity,
    _lookup_team_rarity,
    detect_active_streaks,
    evaluate_streaks,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_game(**overrides: object) -> dict:
    """Create a minimal game log dict with sensible defaults."""
    defaults: dict = {
        "game_id": 1,
        "game_date": "2026-01-01",
        "team_abbr": "EDM",
        "opponent_abbr": "TOR",
        "home_road_flag": "H",
        "goals": 0,
        "assists": 0,
        "points": 0,
        "shots": 2,
        "pim": 0,
        "plus_minus": 0,
        "time_on_ice": "15:00",
        "shifts": 20,
        "power_play_goals": 0,
        "power_play_points": 0,
        "shorthanded_goals": 0,
        "game_winning_goals": 0,
        "ot_goals": 0,
        "blocked_shots": 1,
        "giveaways": 0,
        "takeaways": 1,
        "faceoff_pct": None,
    }
    defaults.update(overrides)
    return defaults


def _make_season_games(
    n: int,
    points_per_game: int = 0,
    goals_per_game: int = 0,
    shots_per_game: int = 2,
    plus_minus_per_game: int = 0,
    takeaways_per_game: int = 1,
    start_date: str = "2026-01-01",
) -> list[dict]:
    """Create a sequence of n game logs with uniform stats."""
    games = []
    for i in range(n):
        day = (i % 28) + 1
        month = (i // 28) + 1
        date = f"2026-{month:02d}-{day:02d}"
        games.append(_make_game(
            game_id=1000 + i,
            game_date=date,
            goals=goals_per_game,
            assists=max(0, points_per_game - goals_per_game),
            points=points_per_game,
            shots=shots_per_game,
            plus_minus=plus_minus_per_game,
            takeaways=takeaways_per_game,
        ))
    return games


@pytest.fixture
def sample_baselines() -> dict:
    """Minimal baselines structure for unit tests."""
    return {
        "metadata": {
            "seasons": ["20252026"],
            "total_player_seasons": {"F": 100, "D": 50, "ALL": 150},
            "computed_at": "2026-07-20T00:00:00",
        },
        "league": {
            "F": {
                "point_streak": {"1": 0.95, "2": 0.80, "3": 0.60, "4": 0.40, "5": 0.25},
                "goal_streak": {"1": 0.90, "2": 0.50, "3": 0.20},
                "multi_point": {"1": 0.70, "2": 0.30, "3": 0.10},
                "hat_trick": {"1": 0.10},
                "takeaway_streak": {"1": 0.95, "2": 0.70, "3": 0.40},
                "scoreless_drought": {"1": 1.0, "2": 0.98, "3": 0.95, "4": 0.90, "5": 0.85},
                "pointless_drought": {"1": 1.0, "2": 0.97, "3": 0.90, "4": 0.80, "5": 0.70},
                "shot_drought": {"1": 0.99, "2": 0.90, "3": 0.80, "4": 0.65, "5": 0.50},
                "minus_streak": {"1": 0.95, "2": 0.80, "3": 0.50},
            },
            "D": {
                "point_streak": {"1": 0.90, "2": 0.60, "3": 0.30},
                "goal_streak": {"1": 0.70, "2": 0.20},
                "multi_point": {"1": 0.50, "2": 0.10},
                "hat_trick": {},
                "takeaway_streak": {"1": 0.90, "2": 0.60, "3": 0.30},
                "scoreless_drought": {"1": 1.0, "2": 0.99, "3": 0.97, "4": 0.95, "5": 0.93},
                "pointless_drought": {"1": 1.0, "2": 0.98, "3": 0.93, "4": 0.85, "5": 0.75},
                "shot_drought": {"1": 0.99, "2": 0.95, "3": 0.85, "4": 0.75, "5": 0.60},
                "minus_streak": {"1": 0.95, "2": 0.85, "3": 0.55},
            },
            "ALL": {
                "point_streak": {"1": 0.93, "2": 0.72, "3": 0.48, "4": 0.32, "5": 0.20},
                "goal_streak": {"1": 0.82, "2": 0.38, "3": 0.15},
                "multi_point": {"1": 0.62, "2": 0.22, "3": 0.07},
                "hat_trick": {"1": 0.07},
                "takeaway_streak": {"1": 0.93, "2": 0.65, "3": 0.35},
                "scoreless_drought": {"1": 1.0, "2": 0.98, "3": 0.96, "4": 0.92, "5": 0.88},
                "pointless_drought": {"1": 1.0, "2": 0.97, "3": 0.92, "4": 0.82, "5": 0.72},
                "shot_drought": {"1": 0.99, "2": 0.92, "3": 0.82, "4": 0.68, "5": 0.53},
                "minus_streak": {"1": 0.95, "2": 0.82, "3": 0.52},
            },
        },
        "team": {
            "EDM": {
                "F": {
                    "point_streak": {"1": 0.96, "2": 0.82, "3": 0.62, "4": 0.42},
                    "goal_streak": {"1": 0.92, "2": 0.55, "3": 0.22},
                    "multi_point": {"1": 0.72, "2": 0.32, "3": 0.12},
                    "hat_trick": {"1": 0.12},
                    "takeaway_streak": {"1": 0.96, "2": 0.72, "3": 0.42},
                    "scoreless_drought": {"1": 1.0, "2": 0.98, "3": 0.95, "4": 0.91},
                    "pointless_drought": {"1": 1.0, "2": 0.97, "3": 0.91, "4": 0.82},
                    "shot_drought": {"1": 0.99, "2": 0.91, "3": 0.81, "4": 0.66},
                    "minus_streak": {"1": 0.96, "2": 0.82, "3": 0.52},
                },
                "D": {
                    "point_streak": {"1": 0.91, "2": 0.62, "3": 0.32},
                    "goal_streak": {"1": 0.72, "2": 0.22},
                    "multi_point": {"1": 0.52, "2": 0.12},
                    "hat_trick": {},
                    "takeaway_streak": {"1": 0.91, "2": 0.62, "3": 0.32},
                    "scoreless_drought": {"1": 1.0, "2": 0.99, "3": 0.97},
                    "pointless_drought": {"1": 1.0, "2": 0.98, "3": 0.94},
                    "shot_drought": {"1": 0.99, "2": 0.96, "3": 0.86},
                    "minus_streak": {"1": 0.96, "2": 0.86, "3": 0.56},
                },
                "ALL": {
                    "point_streak": {"1": 0.94, "2": 0.74, "3": 0.50, "4": 0.34},
                    "goal_streak": {"1": 0.84, "2": 0.40, "3": 0.16},
                    "multi_point": {"1": 0.64, "2": 0.24, "3": 0.08},
                    "hat_trick": {"1": 0.08},
                    "takeaway_streak": {"1": 0.94, "2": 0.67, "3": 0.37},
                    "scoreless_drought": {"1": 1.0, "2": 0.98, "3": 0.96},
                    "pointless_drought": {"1": 1.0, "2": 0.97, "3": 0.92},
                    "shot_drought": {"1": 0.99, "2": 0.93, "3": 0.83},
                    "minus_streak": {"1": 0.96, "2": 0.83, "3": 0.53},
                },
            },
        },
        "player": {
            "8478402": {
                "point_streak": 5,
                "goal_streak": 3,
                "scoreless_drought": 8,
            },
            "9999999": {},
        },
    }


# ---------------------------------------------------------------------------
# Tests: get_position_group
# ---------------------------------------------------------------------------

class TestPositionGroup:
    def test_forward_positions(self) -> None:
        assert get_position_group("C") == "F"
        assert get_position_group("L") == "F"
        assert get_position_group("R") == "F"

    def test_defense(self) -> None:
        assert get_position_group("D") == "D"

    def test_unknown_returns_all(self) -> None:
        assert get_position_group("G") == "ALL"
        assert get_position_group("X") == "ALL"


# ---------------------------------------------------------------------------
# Tests: extract_streaks
# ---------------------------------------------------------------------------

class TestExtractStreaks:
    def test_empty_input(self) -> None:
        result = extract_streaks([], lambda g: g["points"] >= 1, "point_streak")
        assert result == []

    def test_single_scoring_game(self) -> None:
        games = [_make_game(points=1)]
        result = extract_streaks(games, lambda g: g["points"] >= 1, "point_streak")
        assert len(result) == 1
        assert result[0]["length"] == 1
        assert result[0]["streak_type"] == "point_streak"

    def test_consecutive_streak(self) -> None:
        games = [_make_game(points=1) for _ in range(5)]
        result = extract_streaks(games, lambda g: g["points"] >= 1, "point_streak")
        assert len(result) == 1
        assert result[0]["length"] == 5

    def test_broken_streak(self) -> None:
        games = (
            [_make_game(points=1) for _ in range(3)]
            + [_make_game(points=0)]
            + [_make_game(points=1) for _ in range(2)]
        )
        result = extract_streaks(games, lambda g: g["points"] >= 1, "point_streak")
        assert len(result) == 2
        assert result[0]["length"] == 3
        assert result[1]["length"] == 2

    def test_no_matching_games(self) -> None:
        games = [_make_game(points=0) for _ in range(5)]
        result = extract_streaks(games, lambda g: g["points"] >= 1, "point_streak")
        assert result == []

    def test_streak_at_end(self) -> None:
        """Streak that continues to the last game (no trailing break)."""
        games = (
            [_make_game(points=0)]
            + [_make_game(points=1) for _ in range(4)]
        )
        result = extract_streaks(games, lambda g: g["points"] >= 1, "point_streak")
        assert len(result) == 1
        assert result[0]["length"] == 4

    def test_injury_gap(self) -> None:
        """Games with gaps (missing dates) should not affect streak logic
        since we only evaluate predicates on provided games."""
        games = (
            [_make_game(points=1, game_date="2026-01-01")]
            + [_make_game(points=1, game_date="2026-01-03")]  # gap
            + [_make_game(points=1, game_date="2026-01-10")]  # bigger gap
        )
        result = extract_streaks(games, lambda g: g["points"] >= 1, "point_streak")
        assert len(result) == 1
        assert result[0]["length"] == 3

    def test_alternating_games(self) -> None:
        games = [
            _make_game(points=1),
            _make_game(points=0),
            _make_game(points=1),
            _make_game(points=0),
            _make_game(points=1),
        ]
        result = extract_streaks(games, lambda g: g["points"] >= 1, "point_streak")
        assert len(result) == 3
        assert all(s["length"] == 1 for s in result)

    def test_dates_preserved(self) -> None:
        games = [
            _make_game(points=1, game_date="2026-01-01"),
            _make_game(points=1, game_date="2026-01-02"),
        ]
        result = extract_streaks(games, lambda g: g["points"] >= 1, "point_streak")
        assert result[0]["start_date"] == "2026-01-01"
        assert result[0]["end_date"] == "2026-01-02"


# ---------------------------------------------------------------------------
# Tests: detect_active_streaks
# ---------------------------------------------------------------------------

class TestDetectActiveStreaks:
    def test_no_active_streak(self) -> None:
        games = [_make_game(points=0, takeaways=0) for _ in range(5)]
        active = detect_active_streaks(games, min_length=2)
        # Only negative streaks remain: scoreless_drought, pointless_drought, shot_drought, minus_streak
        positive = [s for s in active if s.sign == "positive"]
        assert positive == []

    def test_single_active_point_streak(self) -> None:
        games = (
            [_make_game(points=0)]
            + [_make_game(points=1) for _ in range(3)]
        )
        active = detect_active_streaks(games, min_length=2)
        types = [s.streak_type for s in active]
        assert "point_streak" in types
        point_streak = next(s for s in active if s.streak_type == "point_streak")
        assert point_streak.length == 3

    def test_min_length_filter(self) -> None:
        games = (
            [_make_game(points=0)]
            + [_make_game(points=1, game_date="2026-01-01")]
            + [_make_game(points=1, game_date="2026-01-02")]
        )
        active = detect_active_streaks(games, min_length=3)
        types = [s.streak_type for s in active]
        assert "point_streak" not in types

    def test_multiple_concurrent_streaks(self) -> None:
        """A player on a goal + point + takeaway streak simultaneously."""
        games = [_make_game(goals=1, points=1, takeaways=3, shots=3) for _ in range(4)]
        active = detect_active_streaks(games, min_length=2)
        types = {s.streak_type for s in active}
        assert "goal_streak" in types
        assert "point_streak" in types
        assert "takeaway_streak" in types

    def test_negative_streaks_detected(self) -> None:
        games = [_make_game(goals=0, points=0) for _ in range(6)]
        active = detect_active_streaks(games, min_length=2)
        types = {s.streak_type for s in active}
        assert "scoreless_drought" in types
        assert "pointless_drought" in types

    def test_sign_field(self) -> None:
        games = [_make_game(points=1) for _ in range(3)]
        active = detect_active_streaks(games, min_length=2)
        point_streak = next(s for s in active if s.streak_type == "point_streak")
        assert point_streak.sign == "positive"

        drought_games = [_make_game(goals=0) for _ in range(4)]
        active_drought = detect_active_streaks(drought_games, min_length=2)
        drought = next(s for s in active_drought if s.streak_type == "scoreless_drought")
        assert drought.sign == "negative"

    def test_two_games_meets_min_length(self) -> None:
        games = [_make_game(points=1) for _ in range(2)]
        active = detect_active_streaks(games, min_length=2)
        assert any(s.streak_type == "point_streak" for s in active)

    def test_recent_games_captured(self) -> None:
        games = (
            [_make_game(points=0)]
            + [_make_game(points=1, game_date=f"2026-01-{i:02d}") for i in range(1, 6)]
        )
        active = detect_active_streaks(games, min_length=2)
        point_streak = next(s for s in active if s.streak_type == "point_streak")
        assert len(point_streak.recent_games) == 5


# ---------------------------------------------------------------------------
# Tests: Rarity lookups
# ---------------------------------------------------------------------------

class TestRarityLookups:
    def test_league_rarity_found(self, sample_baselines: dict) -> None:
        r = _lookup_league_rarity(sample_baselines, "F", "point_streak", 3)
        assert r.level == "league"
        assert r.probability == pytest.approx(0.60)
        assert r.rarity == pytest.approx(0.40)

    def test_league_rarity_not_found(self, sample_baselines: dict) -> None:
        r = _lookup_league_rarity(sample_baselines, "F", "point_streak", 100)
        assert r.probability == 0.0
        assert r.rarity == pytest.approx(1.0)

    def test_team_rarity_found(self, sample_baselines: dict) -> None:
        r = _lookup_team_rarity(sample_baselines, "EDM", "F", "goal_streak", 2)
        assert r.level == "team"
        assert r.probability == pytest.approx(0.55)

    def test_team_rarity_missing_team(self, sample_baselines: dict) -> None:
        r = _lookup_team_rarity(sample_baselines, "XYZ", "F", "goal_streak", 2)
        assert r.probability == 0.0
        assert r.rarity == pytest.approx(1.0)

    def test_player_rarity_below_career_best(self, sample_baselines: dict) -> None:
        r = _lookup_player_rarity(sample_baselines, 8478402, "point_streak", 3)
        assert r.level == "player"
        assert r.probability == pytest.approx(0.6)

    def test_player_rarity_at_career_best(self, sample_baselines: dict) -> None:
        r = _lookup_player_rarity(sample_baselines, 8478402, "point_streak", 5)
        assert r.probability == pytest.approx(0.10)

    def test_player_rarity_above_career_best(self, sample_baselines: dict) -> None:
        r = _lookup_player_rarity(sample_baselines, 8478402, "point_streak", 7)
        assert r.probability == pytest.approx(0.01)

    def test_player_rarity_no_data(self, sample_baselines: dict) -> None:
        r = _lookup_player_rarity(sample_baselines, 9999999, "point_streak", 3)
        assert r.probability == 0.5


# ---------------------------------------------------------------------------
# Tests: StreakAnomaly composite scoring
# ---------------------------------------------------------------------------

class TestStreakAnomaly:
    def _make_anomaly(self, league_r: float, team_r: float, player_r: float) -> StreakAnomaly:
        streak = ActiveStreak(
            streak_type="point_streak",
            length=5,
            start_date="2026-01-01",
            end_date="2026-01-05",
            sign="positive",
            description="5 game point streak",
        )
        return StreakAnomaly(
            player_id=8478402,
            player_name="Connor McDavid",
            team="EDM",
            position="C",
            season="20252026",
            streak=streak,
            rarity_scores=[
                RarityScore(level="league", probability=1.0 - league_r, rarity=league_r),
                RarityScore(level="team", probability=1.0 - team_r, rarity=team_r),
                RarityScore(level="player", probability=1.0 - player_r, rarity=player_r),
            ],
        )

    def test_novelty_index_weights(self) -> None:
        a = self._make_anomaly(league_r=1.0, team_r=0.0, player_r=0.0)
        # 0.40 * 1.0 + 0.35 * 0.0 + 0.25 * 0.0 = 0.40
        assert a.novelty_index == pytest.approx(0.40)

    def test_perfect_score(self) -> None:
        a = self._make_anomaly(1.0, 1.0, 1.0)
        assert a.novelty_index == pytest.approx(1.0)
        assert a.severity == "EXTREMELY RARE"

    def test_zero_score(self) -> None:
        a = self._make_anomaly(0.0, 0.0, 0.0)
        assert a.novelty_index == pytest.approx(0.0)
        assert a.severity == "COMMON"

    def test_severity_labels(self) -> None:
        assert self._make_anomaly(1.0, 1.0, 1.0).severity == "EXTREMELY RARE"
        assert self._make_anomaly(0.9, 0.8, 0.8).severity == "VERY RARE"
        assert self._make_anomaly(0.6, 0.5, 0.5).severity == "RARE"
        assert self._make_anomaly(0.3, 0.25, 0.25).severity == "UNCOMMON"
        assert self._make_anomaly(0.0, 0.0, 0.0).severity == "COMMON"

    def test_to_dict(self) -> None:
        a = self._make_anomaly(0.9, 0.8, 0.7)
        d = a.to_dict()
        assert d["player_id"] == 8478402
        assert d["player_name"] == "Connor McDavid"
        assert d["streak"]["type"] == "point_streak"
        assert d["streak"]["length"] == 5
        assert len(d["rarity_scores"]) == 3
        assert "novelty_index" in d
        assert "severity" in d

    def test_sorted_by_novelty(self) -> None:
        a1 = self._make_anomaly(0.2, 0.2, 0.2)
        a2 = self._make_anomaly(0.9, 0.9, 0.9)
        anomalies = sorted([a1, a2], key=lambda a: a.novelty_index, reverse=True)
        assert anomalies[0].novelty_index > anomalies[1].novelty_index


# ---------------------------------------------------------------------------
# Tests: evaluate_streaks integration
# ---------------------------------------------------------------------------

class TestEvaluateStreaks:
    def test_returns_anomalies_for_active_streak(self, sample_baselines: dict) -> None:
        games = (
            [_make_game(points=0)]
            + [_make_game(points=1, shots=3) for _ in range(5)]
        )
        anomalies = evaluate_streaks(
            player_id=8478402,
            player_name="Connor McDavid",
            team="EDM",
            position="C",
            season="20252026",
            game_logs=games,
            baselines=sample_baselines,
            min_length=2,
        )
        assert len(anomalies) >= 1
        types = {a.streak.streak_type for a in anomalies}
        assert "point_streak" in types

    def test_no_anomalies_when_no_streak(self, sample_baselines: dict) -> None:
        games = [_make_game(points=0, goals=0, takeaways=0, plus_minus=0) for _ in range(10)]
        anomalies = evaluate_streaks(
            player_id=8478402,
            player_name="Connor McDavid",
            team="EDM",
            position="C",
            season="20252026",
            game_logs=games,
            baselines=sample_baselines,
            min_length=2,
        )
        # Negative droughts (scoreless, pointless) will be detected but check no positive anomalies
        positive = [a for a in anomalies if a.streak.sign == "positive"]
        assert positive == []

    def test_sorted_by_novelty(self, sample_baselines: dict) -> None:
        games = (
            [_make_game(points=0)]
            + [_make_game(goals=1, points=1, shots=3) for _ in range(5)]
        )
        anomalies = evaluate_streaks(
            player_id=8478402,
            player_name="Connor McDavid",
            team="EDM",
            position="C",
            season="20252026",
            game_logs=games,
            baselines=sample_baselines,
            min_length=2,
        )
        for i in range(len(anomalies) - 1):
            assert anomalies[i].novelty_index >= anomalies[i + 1].novelty_index

    def test_player_rarity_uses_career_best(self, sample_baselines: dict) -> None:
        """Player with career best = 5 should get rarity 0.10 at length 5."""
        games = (
            [_make_game(points=0)]
            + [_make_game(points=1) for _ in range(5)]
        )
        anomalies = evaluate_streaks(
            player_id=8478402,
            player_name="Connor McDavid",
            team="EDM",
            position="C",
            season="20252026",
            game_logs=games,
            baselines=sample_baselines,
            min_length=2,
        )
        point_anomaly = next(
            (a for a in anomalies if a.streak.streak_type == "point_streak"), None
        )
        assert point_anomaly is not None
        player_score = next(r for r in point_anomaly.rarity_scores if r.level == "player")
        assert player_score.probability == pytest.approx(0.10)

    def test_player_no_career_data(self, sample_baselines: dict) -> None:
        """Player with no career data should get default probability 0.5."""
        games = (
            [_make_game(points=0)]
            + [_make_game(points=1) for _ in range(3)]
        )
        anomalies = evaluate_streaks(
            player_id=9999999,
            player_name="Unknown Player",
            team="EDM",
            position="C",
            season="20252026",
            game_logs=games,
            baselines=sample_baselines,
            min_length=2,
        )
        point_anomaly = next(
            (a for a in anomalies if a.streak.streak_type == "point_streak"), None
        )
        assert point_anomaly is not None
        player_score = next(r for r in point_anomaly.rarity_scores if r.level == "player")
        assert player_score.probability == 0.5


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_game_logs(self, sample_baselines: dict) -> None:
        anomalies = evaluate_streaks(
            player_id=1,
            player_name="Test",
            team="EDM",
            position="C",
            season="20252026",
            game_logs=[],
            baselines=sample_baselines,
        )
        assert anomalies == []

    def test_single_game(self, sample_baselines: dict) -> None:
        games = [_make_game(points=1)]
        anomalies = evaluate_streaks(
            player_id=1,
            player_name="Test",
            team="EDM",
            position="C",
            season="20252026",
            game_logs=games,
            baselines=sample_baselines,
            min_length=2,
        )
        assert anomalies == []

    def test_two_games_with_streak(self, sample_baselines: dict) -> None:
        games = [_make_game(points=1) for _ in range(2)]
        anomalies = evaluate_streaks(
            player_id=1,
            player_name="Test",
            team="EDM",
            position="C",
            season="20252026",
            game_logs=games,
            baselines=sample_baselines,
            min_length=2,
        )
        assert len(anomalies) >= 1

    def test_all_streak_types_defined(self) -> None:
        """Ensure every streak type has a predicate and description."""
        for name, st in STREAK_TYPES.items():
            assert st.name == name
            assert st.sign in ("positive", "negative")
            assert len(st.description) > 0
            # Predicate should work on a sample game
            game = _make_game()
            result = st.predicate(game)
            assert isinstance(result, bool)

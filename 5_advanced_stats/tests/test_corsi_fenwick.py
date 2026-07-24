"""
test_corsi_fenwick.py — Tests for the Corsi/Fenwick Computation Engine

Tests time parsing, on-ice map building, single-game computation, and
season-level aggregation.
Run: pytest 5_advanced_stats/tests/test_corsi_fenwick.py -v
"""

import sqlite3
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from corsi_fenwick import (
    CORSI_EVENTS,
    FENWICK_EVENTS,
    SITUATION_5V5,
    _get_on_ice_at_time,
    _precompute_shifts,
    compute_game_corsi,
    compute_player_corsi,
    parse_time_to_seconds,
)


# ---------------------------------------------------------------------------
# Helpers: synthetic DataFrames
# ---------------------------------------------------------------------------

def _make_shift_row(
    player_id: int,
    player_name: str,
    team_abbr: str,
    period: int,
    start_time: str,
    end_time: str,
    duration: str = "",
) -> dict:
    if not duration:
        s = parse_time_to_seconds(start_time)
        e = parse_time_to_seconds(end_time)
        m, sec = divmod(e - s, 60)
        duration = f"{m}:{sec:02d}"
    return {
        "game_id": 2025020001,
        "player_id": player_id,
        "player_name": player_name,
        "team_abbr": team_abbr,
        "period": period,
        "start_time": start_time,
        "end_time": end_time,
        "duration": duration,
    }


def _make_pbp_row(
    event_type: str,
    period: int,
    time_in_period: str,
    situation_code: str = "1551",
    event_owner_team_id: int = 1,
    shooting_player_id: int = None,
    scoring_player_id: int = None,
) -> dict:
    return {
        "game_id": 2025020001,
        "period": period,
        "time_in_period": time_in_period,
        "type_desc_key": event_type,
        "situation_code": situation_code,
        "event_owner_team_id": event_owner_team_id,
        "shooting_player_id": shooting_player_id,
        "scoring_player_id": scoring_player_id,
    }


def _simple_game_data():
    """Create a minimal synthetic game: FLA vs CHI, 1 period, 5v5.

    FLA players: 100, 101, 102, 103, 104 (skaters)
    CHI players: 200, 201, 202, 203, 204 (skaters)

    Event: shot-on-goal at 10:00 by FLA player 100.
    """
    shifts = pd.DataFrame([
        _make_shift_row(100, "A", "FLA", 1, "0:00", "20:00"),
        _make_shift_row(101, "B", "FLA", 1, "0:00", "20:00"),
        _make_shift_row(102, "C", "FLA", 1, "0:00", "20:00"),
        _make_shift_row(103, "D", "FLA", 1, "0:00", "20:00"),
        _make_shift_row(104, "E", "FLA", 1, "0:00", "20:00"),
        _make_shift_row(200, "F", "CHI", 1, "0:00", "20:00"),
        _make_shift_row(201, "G", "CHI", 1, "0:00", "20:00"),
        _make_shift_row(202, "H", "CHI", 1, "0:00", "20:00"),
        _make_shift_row(203, "I", "CHI", 1, "0:00", "20:00"),
        _make_shift_row(204, "J", "CHI", 1, "0:00", "20:00"),
    ])
    pbp = pd.DataFrame([
        _make_pbp_row("shot-on-goal", 1, "10:00", "1551", 1, 100),
        _make_pbp_row("shot-on-goal", 1, "10:05", "1551", 2, 200),
        _make_pbp_row("blocked-shot", 1, "10:10", "1551", 1, 101),
        _make_pbp_row("goal", 1, "11:00", "1551", 2, 201, 201),
        _make_pbp_row("faceoff", 1, "12:00", "1551"),
        _make_pbp_row("hit", 1, "12:30", "1551"),
    ])
    return pbp, shifts


# ---------------------------------------------------------------------------
# Unit tests: parse_time_to_seconds
# ---------------------------------------------------------------------------

class TestParseTimeToSeconds:
    """Tests for MM:SS time string parsing."""

    def test_zero(self) -> None:
        assert parse_time_to_seconds("0:00") == 0

    def test_one_minute(self) -> None:
        assert parse_time_to_seconds("1:00") == 60

    def test_full_period(self) -> None:
        assert parse_time_to_seconds("20:00") == 1200

    def test_compound(self) -> None:
        assert parse_time_to_seconds("12:34") == 754

    def test_empty_string(self) -> None:
        assert parse_time_to_seconds("") == 0

    def test_none(self) -> None:
        assert parse_time_to_seconds(None) == 0

    def test_nan(self) -> None:
        assert parse_time_to_seconds(float("nan")) == 0

    def test_bad_format(self) -> None:
        assert parse_time_to_seconds("1234") == 0

    def test_non_string(self) -> None:
        assert parse_time_to_seconds(120) == 0

    def test_partial_seconds_only(self) -> None:
        assert parse_time_to_seconds(":30") == 0

    def test_large_time(self) -> None:
        assert parse_time_to_seconds("59:59") == 3599


# ---------------------------------------------------------------------------
# Unit tests: build_on_ice_map
# ---------------------------------------------------------------------------

class TestPrecomputeShifts:
    """Tests for shift time pre-computation."""

    def test_adds_start_sec_column(self) -> None:
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "0:00", "2:00"),
        ])
        result = _precompute_shifts(shifts)
        assert "_start_sec" in result.columns
        assert "_end_sec" in result.columns
        assert result["_start_sec"].iloc[0] == 0
        assert result["_end_sec"].iloc[0] == 120

    def test_does_not_mutate_original(self) -> None:
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "0:00", "2:00"),
        ])
        _precompute_shifts(shifts)
        assert "_start_sec" not in shifts.columns


class TestGetOnIceAtTime:
    """Tests for vectorized interval-based on-ice lookup."""

    def test_basic_lookup(self) -> None:
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "0:00", "2:00"),
            _make_shift_row(200, "B", "CHI", 1, "0:00", "2:00"),
        ])
        pre = _precompute_shifts(shifts)
        result = _get_on_ice_at_time(pre, 1, 60)
        assert 100 in result["FLA"]
        assert 200 in result["CHI"]

    def test_outside_shift_range(self) -> None:
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "0:30", "1:00"),
        ])
        pre = _precompute_shifts(shifts)
        result = _get_on_ice_at_time(pre, 1, 10)
        assert result == {}

    def test_at_shift_boundaries(self) -> None:
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "1:00", "2:00"),
        ])
        pre = _precompute_shifts(shifts)
        assert 100 in _get_on_ice_at_time(pre, 1, 60).get("FLA", set())
        assert 100 in _get_on_ice_at_time(pre, 1, 120).get("FLA", set())
        assert 100 not in _get_on_ice_at_time(pre, 1, 59).get("FLA", set())
        assert 100 not in _get_on_ice_at_time(pre, 1, 121).get("FLA", set())

    def test_staggered_shifts(self) -> None:
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "0:00", "1:00"),
            _make_shift_row(101, "B", "FLA", 1, "0:30", "1:30"),
        ])
        pre = _precompute_shifts(shifts)
        at_0 = _get_on_ice_at_time(pre, 1, 0)
        assert 100 in at_0["FLA"]
        assert 101 not in at_0["FLA"]
        at_45 = _get_on_ice_at_time(pre, 1, 45)
        assert 100 in at_45["FLA"]
        assert 101 in at_45["FLA"]
        at_70 = _get_on_ice_at_time(pre, 1, 70)
        assert 100 not in at_70.get("FLA", set())
        assert 101 in at_70["FLA"]

    def test_wrong_period(self) -> None:
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "0:00", "1:00"),
        ])
        pre = _precompute_shifts(shifts)
        result = _get_on_ice_at_time(pre, 2, 30)
        assert result == {}

    def test_empty_shifts(self) -> None:
        shifts = pd.DataFrame(columns=["game_id", "player_id", "player_name", "team_abbr", "period", "start_time", "end_time", "duration"])
        pre = _precompute_shifts(shifts)
        result = _get_on_ice_at_time(pre, 1, 30)
        assert result == {}


# ---------------------------------------------------------------------------
# Unit tests: compute_game_corsi
# ---------------------------------------------------------------------------

class TestComputeGameCorsi:
    """Tests for single-game Corsi/Fenwick computation."""

    def test_basic_computation(self) -> None:
        pbp, shifts = _simple_game_data()
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        assert len(result) == 10  # 5 FLA + 5 CHI

    def test_corsi_events_tracked(self) -> None:
        pbp, shifts = _simple_game_data()
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        # FLA player 100 took 1 shot, 1 blocked shot = 2 CF events
        fla_100 = result[100]
        assert fla_100["cf"] >= 1  # at least the shot-on-goal
        assert fla_100["team"] == "FLA"

    def test_fenwick_excludes_blocks(self) -> None:
        """Fenwick should count shot-on-goal but not blocked-shot."""
        pbp, shifts = _simple_game_data()
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        # FLA has 1 shot-on-goal + 1 blocked-shot in corsi events
        # Fenwick: only shot-on-goal counted (blocked excluded)
        fla_total_ff = sum(r["ff"] for r in result.values() if r["team"] == "FLA")
        fla_total_cf = sum(r["cf"] for r in result.values() if r["team"] == "FLA")
        # FF should be <= CF (blocked shots excluded from Fenwick)
        assert fla_total_ff <= fla_total_cf

    def test_defense_ca_increments(self) -> None:
        """When FLA shoots, CHI players should get CA."""
        pbp, shifts = _simple_game_data()
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        chi_total_ca = sum(r["ca"] for r in result.values() if r["team"] == "CHI")
        assert chi_total_ca > 0

    def test_strength_filter(self) -> None:
        """Non-matching strength states should yield empty or reduced results."""
        pbp, shifts = _simple_game_data()
        result_5v5 = compute_game_corsi(pbp, shifts, "", "", "1551")
        result_5v4 = compute_game_corsi(pbp, shifts, "", "", "1541")
        # 5v5 should have more events since our PBP is mostly 1551
        assert len(result_5v5) >= len(result_5v4)

    def test_empty_pbp(self) -> None:
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "0:00", "1:00"),
            _make_shift_row(200, "B", "CHI", 1, "0:00", "1:00"),
        ])
        pbp = pd.DataFrame(columns=["game_id", "period", "time_in_period", "type_desc_key", "situation_code", "event_owner_team_id", "shooting_player_id", "scoring_player_id"])
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        assert result == {}

    def test_empty_shifts(self) -> None:
        pbp = pd.DataFrame([
            _make_pbp_row("shot-on-goal", 1, "10:00", "1551", 1, 100),
        ])
        shifts = pd.DataFrame(columns=["game_id", "player_id", "player_name", "team_abbr", "period", "start_time", "end_time", "duration"])
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        assert result == {}

    def test_single_team_shifts(self) -> None:
        """Only one team in shifts should return empty."""
        pbp, _ = _simple_game_data()
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "0:00", "20:00"),
        ])
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        assert result == {}

    def test_toi_computed(self) -> None:
        pbp, shifts = _simple_game_data()
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        for pid, stats in result.items():
            assert stats["toi_seconds"] > 0

    def test_percentages_bounded(self) -> None:
        pbp, shifts = _simple_game_data()
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        for pid, stats in result.items():
            assert 0.0 <= stats["cf_pct"] <= 100.0
            assert 0.0 <= stats["ff_pct"] <= 100.0

    def test_blocked_shot_in_corsi_not_fenwick(self) -> None:
        """A blocked-shot counts for Corsi but not Fenwick."""
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "0:00", "20:00"),
            _make_shift_row(200, "B", "CHI", 1, "0:00", "20:00"),
        ])
        pbp = pd.DataFrame([
            _make_pbp_row("blocked-shot", 1, "10:00", "1551", 1, 100),
        ])
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        # Player 100 should have CF=1, FF=0 (blocked shot excluded from Fenwick)
        assert result[100]["cf"] == 1
        assert result[100]["ff"] == 0

    def test_goal_counts_for_both(self) -> None:
        """A goal counts for both Corsi and Fenwick."""
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "0:00", "20:00"),
            _make_shift_row(200, "B", "CHI", 1, "0:00", "20:00"),
        ])
        pbp = pd.DataFrame([
            _make_pbp_row("goal", 1, "10:00", "1551", 1, 100, 100),
        ])
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        assert result[100]["cf"] == 1
        assert result[100]["ff"] == 1

    def test_non_corsi_events_ignored(self) -> None:
        """Faceoffs, hits, penalties should not affect Corsi/Fenwick."""
        shifts = pd.DataFrame([
            _make_shift_row(100, "A", "FLA", 1, "0:00", "20:00"),
            _make_shift_row(200, "B", "CHI", 1, "0:00", "20:00"),
        ])
        pbp = pd.DataFrame([
            _make_pbp_row("faceoff", 1, "10:00", "1551"),
            _make_pbp_row("hit", 1, "10:30", "1551"),
            _make_pbp_row("penalty", 1, "11:00", "1551"),
            _make_pbp_row("giveaway", 1, "11:30", "1551"),
            _make_pbp_row("takeaway", 1, "12:00", "1551"),
        ])
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        # No shot attempts -> all CF/CA/FF/FA should be 0
        for pid, stats in result.items():
            assert stats["cf"] == 0
            assert stats["ca"] == 0
            assert stats["ff"] == 0
            assert stats["fa"] == 0


# ---------------------------------------------------------------------------
# Unit tests: event type constants
# ---------------------------------------------------------------------------

class TestEventConstants:
    """Tests for event type sets."""

    def test_corsi_events_superset_of_fenwick(self) -> None:
        assert FENWICK_EVENTS.issubset(CORSI_EVENTS)

    def test_blocked_shot_in_corsi_not_fenwick(self) -> None:
        assert "blocked-shot" in CORSI_EVENTS
        assert "blocked-shot" not in FENWICK_EVENTS

    def test_goal_in_both(self) -> None:
        assert "goal" in CORSI_EVENTS
        assert "goal" in FENWICK_EVENTS

    def test_shot_on_goal_in_both(self) -> None:
        assert "shot-on-goal" in CORSI_EVENTS
        assert "shot-on-goal" in FENWICK_EVENTS

    def test_missed_shot_in_both(self) -> None:
        assert "missed-shot" in CORSI_EVENTS
        assert "missed-shot" in FENWICK_EVENTS

    def test_situation_5v5(self) -> None:
        assert SITUATION_5V5 == "1551"


# ---------------------------------------------------------------------------
# Integration test: compute_game_corsi against real DB (single game)
# ---------------------------------------------------------------------------

class TestComputeGameCorsiReal:
    """Integration tests using a single real game from the database."""

    @pytest.fixture
    def real_game_data(self) -> tuple[pd.DataFrame, pd.DataFrame, int]:
        db_path = str(Path(__file__).parent.parent.parent / "1_data_warehouse" / "nhl_data.db")
        conn = sqlite3.connect(db_path)
        game_id = int(pd.read_sql_query(
            "SELECT p.game_id FROM play_by_play p "
            "JOIN player_shifts s ON p.game_id = s.game_id "
            "LIMIT 1", conn
        )["game_id"].iloc[0])
        pbp = pd.read_sql_query("SELECT * FROM play_by_play WHERE game_id = ?", conn, params=(game_id,))
        shifts = pd.read_sql_query("SELECT * FROM player_shifts WHERE game_id = ?", conn, params=(game_id,))
        conn.close()
        return pbp, shifts, game_id

    def test_returns_dict(self, real_game_data: tuple) -> None:
        pbp, shifts, _ = real_game_data
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        assert isinstance(result, dict)

    def test_has_players(self, real_game_data: tuple) -> None:
        pbp, shifts, _ = real_game_data
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        assert len(result) > 0

    def test_all_stats_present(self, real_game_data: tuple) -> None:
        pbp, shifts, _ = real_game_data
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        for pid, stats in result.items():
            assert "team" in stats
            assert "cf" in stats
            assert "ca" in stats
            assert "cf_pct" in stats
            assert "ff" in stats
            assert "fa" in stats
            assert "ff_pct" in stats
            assert "toi_seconds" in stats

    def test_teams_assigned(self, real_game_data: tuple) -> None:
        pbp, shifts, _ = real_game_data
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        teams = {s["team"] for s in result.values()}
        assert len(teams) == 2

    def test_percentages_bounded(self, real_game_data: tuple) -> None:
        pbp, shifts, _ = real_game_data
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        for pid, stats in result.items():
            assert 0.0 <= stats["cf_pct"] <= 100.0
            assert 0.0 <= stats["ff_pct"] <= 100.0

    def test_fenwick_le_corsi(self, real_game_data: tuple) -> None:
        """FF <= CF and FA <= CA for each player (Fenwick excludes blocks)."""
        pbp, shifts, _ = real_game_data
        result = compute_game_corsi(pbp, shifts, "", "", "1551")
        for pid, stats in result.items():
            assert stats["ff"] <= stats["cf"]
            assert stats["fa"] <= stats["ca"]

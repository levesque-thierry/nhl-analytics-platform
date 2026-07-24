"""
corsi_fenwick.py — Corsi & Fenwick On-Ice Statistics Computation

Computes per-player possession metrics from play-by-play and shift data:
    - Corsi (CF/CA/CF%): All shot attempts (goals + saves + misses + blocks)
    - Fenwick (FF/FA/FF%): Unblocked attempts only (excludes blocks)
    - Rates per 60 minutes of ice time

Usage:
    from corsi_fenwick import compute_player_corsi
    results = compute_player_corsi(conn, season="20252026")
"""

import logging
import sqlite3
from collections import defaultdict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Shot attempt event types in play_by_play.type_desc_key
CORSI_EVENTS = {"goal", "shot-on-goal", "missed-shot", "blocked-shot"}
FENWICK_EVENTS = {"goal", "shot-on-goal", "missed-shot"}  # no blocked shots

# Situation codes for 5v5 (5 skaters + 1 goalie each side)
SITUATION_5V5 = "1551"


def parse_time_to_seconds(time_str: str) -> int:
    """Convert 'MM:SS' time string to total seconds."""
    if not time_str or time_str == "" or pd.isna(time_str):
        return 0
    time_str = str(time_str)
    parts = time_str.split(":")
    if len(parts) != 2:
        return 0
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, TypeError):
        return 0


def _precompute_shifts(shifts_df: pd.DataFrame) -> pd.DataFrame:
    """Pre-compute start/end seconds for all shifts. Returns enriched DataFrame."""
    df = shifts_df.copy()
    df["_start_sec"] = df["start_time"].apply(parse_time_to_seconds)
    df["_end_sec"] = df["end_time"].apply(parse_time_to_seconds)
    return df


def _get_on_ice_at_time(
    shifts_df: pd.DataFrame,
    period: int,
    time_sec: int,
) -> dict[str, set[int]]:
    """
    Get players on ice at a specific time using vectorized interval filtering.

    Args:
        shifts_df: Pre-computed shifts with _start_sec/_end_sec columns.
        period: Period number.
        time_sec: Time in seconds within the period.

    Returns:
        dict[team_abbr] -> set of player_ids on ice.
    """
    mask = (
        (shifts_df["period"] == period)
        & (shifts_df["_start_sec"] <= time_sec)
        & (shifts_df["_end_sec"] >= time_sec)
    )
    on_ice_shifts = shifts_df.loc[mask]
    if on_ice_shifts.empty:
        return {}
    return on_ice_shifts.groupby("team_abbr")["player_id"].apply(set).to_dict()


def compute_game_corsi(
    pbp_df: pd.DataFrame,
    shifts_df: pd.DataFrame,
    home_team: str,
    away_team: str,
    strength_state: str = SITUATION_5V5,
) -> dict[int, dict]:
    """
    Compute Corsi/Fenwick for a single game.

    Returns:
        dict[player_id] -> {
            "team": str,
            "cf": int, "ca": int, "cf_pct": float,
            "ff": int, "fa": int, "ff_pct": float,
            "toi_seconds": int,
        }
    """
    # Filter to matching strength state
    game_pbp = pbp_df[pbp_df["situation_code"] == strength_state]
    if game_pbp.empty:
        return {}

    # Get the actual team abbreviations from the shifts
    teams_in_shifts = shifts_df["team_abbr"].unique()
    if len(teams_in_shifts) < 2:
        return {}

    team_list = list(teams_in_shifts)

    # Pre-compute shift intervals
    shifts_pre = _precompute_shifts(shifts_df)

    # Initialize counters
    stats: dict[int, dict] = {}

    def get_or_init(pid: int, team: str) -> dict:
        if pid not in stats:
            stats[pid] = {
                "team": team,
                "cf": 0, "ca": 0, "cf_pct": 0.0,
                "ff": 0, "fa": 0, "ff_pct": 0.0,
                "toi_seconds": 0,
            }
        return stats[pid]

    # Process each shot attempt event
    for _, event in game_pbp.iterrows():
        desc = event["type_desc_key"]
        if desc not in CORSI_EVENTS:
            continue

        period = event["period"]
        time_sec = parse_time_to_seconds(event["time_in_period"])
        ice_state = _get_on_ice_at_time(shifts_pre, period, time_sec)

        shooting_pid = event.get("shooting_player_id") or event.get("scoring_player_id")
        if shooting_pid is None:
            continue

        # Find which team this player belongs to
        shooting_team = None
        for team_abbr, players in ice_state.items():
            if shooting_pid in players:
                shooting_team = team_abbr
                break

        if shooting_team is None:
            continue

        # Find the opposing team
        opposing_team = None
        for t in team_list:
            if t != shooting_team:
                opposing_team = t
                break
        if opposing_team is None:
            continue

        shooter_on_ice = ice_state.get(shooting_team, set())
        defender_on_ice = ice_state.get(opposing_team, set())

        # Corsi: all shot attempts
        for pid in shooter_on_ice:
            s = get_or_init(pid, shooting_team)
            s["cf"] += 1
        for pid in defender_on_ice:
            s = get_or_init(pid, opposing_team)
            s["ca"] += 1

        # Fenwick: exclude blocked shots
        if desc in FENWICK_EVENTS:
            for pid in shooter_on_ice:
                s = get_or_init(pid, shooting_team)
                s["ff"] += 1
            for pid in defender_on_ice:
                s = get_or_init(pid, opposing_team)
                s["fa"] += 1

    # Compute TOI from shifts
    for _, shift in shifts_df.iterrows():
        pid = shift["player_id"]
        team = shift["team_abbr"]
        dur_sec = parse_time_to_seconds(shift["duration"])
        s = get_or_init(pid, team)
        s["toi_seconds"] += dur_sec

    # Compute percentages
    for pid, s in stats.items():
        cf_total = s["cf"] + s["ca"]
        s["cf_pct"] = round(s["cf"] / cf_total * 100, 1) if cf_total > 0 else 0.0
        ff_total = s["ff"] + s["fa"]
        s["ff_pct"] = round(s["ff"] / ff_total * 100, 1) if ff_total > 0 else 0.0

    return stats


def compute_player_corsi(
    conn: sqlite3.Connection,
    season: str = "20252026",
    game_type: int = 2,
    strength_state: str = SITUATION_5V5,
) -> pd.DataFrame:
    """
    Compute Corsi/Fenwick for all players in a season.

    Returns DataFrame with columns:
        player_id, player_name, team, gp, cf, ca, cf_pct, ff, fa, ff_pct,
        toi_seconds, cf_per_60, ff_per_60
    """
    # Get all games for the season from PBP
    game_ids = pd.read_sql_query(
        "SELECT DISTINCT game_id FROM play_by_play ORDER BY game_id",
        conn,
    )["game_id"].tolist()

    if not game_ids:
        logger.warning("No play-by-play data found for season %s", season)
        return pd.DataFrame()

    logger.info("Computing Corsi/Fenwick for %d games...", len(game_ids))

    # Accumulate across all games
    season_stats: dict[int, dict] = defaultdict(lambda: {
        "player_name": "", "team": "", "gp": 0,
        "cf": 0, "ca": 0, "ff": 0, "fa": 0, "toi_seconds": 0,
    })

    for i, game_id in enumerate(game_ids):
        pbp = pd.read_sql_query(
            "SELECT * FROM play_by_play WHERE game_id = ?", conn, params=(game_id,)
        )
        shifts = pd.read_sql_query(
            "SELECT * FROM player_shifts WHERE game_id = ?", conn, params=(game_id,)
        )

        if pbp.empty or shifts.empty:
            continue

        # Get team info from shifts
        team_info = shifts.groupby("player_id").agg({
            "team_abbr": "first",
            "player_name": "first",
        }).to_dict("index")

        game_stats = compute_game_corsi(pbp, shifts, "", "", strength_state)

        for pid, gs in game_stats.items():
            s = season_stats[pid]
            s["player_name"] = team_info.get(pid, {}).get("player_name", "")
            s["team"] = team_info.get(pid, {}).get("team_abbr", gs["team"])
            s["gp"] += 1
            s["cf"] += gs["cf"]
            s["ca"] += gs["ca"]
            s["ff"] += gs["ff"]
            s["fa"] += gs["fa"]
            s["toi_seconds"] += gs["toi_seconds"]

        if (i + 1) % 100 == 0:
            logger.info("  Processed %d/%d games", i + 1, len(game_ids))

    logger.info("Corsi/Fenwick computation complete.")

    # Build DataFrame
    rows = []
    for pid, s in season_stats.items():
        cf_total = s["cf"] + s["ca"]
        ff_total = s["ff"] + s["fa"]
        toi_min = s["toi_seconds"] / 60.0
        rows.append({
            "player_id": pid,
            "player_name": s["player_name"],
            "team": s["team"],
            "gp": s["gp"],
            "cf": s["cf"],
            "ca": s["ca"],
            "cf_pct": round(s["cf"] / cf_total * 100, 1) if cf_total > 0 else 0.0,
            "ff": s["ff"],
            "fa": s["fa"],
            "ff_pct": round(s["ff"] / ff_total * 100, 1) if ff_total > 0 else 0.0,
            "toi_seconds": s["toi_seconds"],
            "cf_per_60": round(s["cf"] / toi_min * 60, 2) if toi_min > 0 else 0.0,
            "ff_per_60": round(s["ff"] / toi_min * 60, 2) if toi_min > 0 else 0.0,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("cf_pct", ascending=False).reset_index(drop=True)
    return df

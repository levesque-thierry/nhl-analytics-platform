"""
fetch_experts.py — Expert Preseason Point Projection Loader

Parses the liste_des_listes Excel files (2016-2021) containing preseason
point projections from multiple expert sources (Hockey Le Magazine, Hockey News,
PoolPro, ESPN, CBS, Sports Forecaster, etc.).

Also fetches actual season results from the NHL Stats API for backtesting.

    Usage:
        from fetch_experts import load_expert_projections, load_actual_results

        experts = load_expert_projections()  # all seasons
        actuals = load_actual_results()  # fetched from Hockey Reference
"""

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_DIR = Path(__file__).parent / "data"
ACTUALS_CACHE = CACHE_DIR / "actuals_cache.json"

EXCEL_DIR = Path(__file__).parent.parent / "liste_des_listes"

HR_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Season mapping: file year → seasonId used by NHL API
# "2016" file contains projections for the 2016-2017 season
FILE_YEAR_TO_SEASON: dict[int, int] = {
    2016: 20162017,
    2017: 20172018,
    2018: 20182019,
    2019: 20192020,
    2020: 20202021,
    2021: 20212022,
    2022: 20222023,
    2023: 20232024,
    2024: 20242025,
    2025: 20252026,
}

# Source name normalization — map raw column names to canonical names
SOURCE_ALIASES: dict[str, str] = {
    "hm": "Hockey Le Magazine",
    "hockey le magazine": "Hockey Le Magazine",
    "lepool.com": "Lepool",
    "lepool": "Lepool",
    "pool pro": "PoolPro",
    "poolpro": "PoolPro",
    "pp": "PoolPro",
    "ppfor": "PoolPro",
    "pool expert": "PoolExpert",
    "pe": "PoolExpert",
    "fantrax": "Fantrax",
    "ft": "Fantrax",
    "hn": "Hockey News",
    "the hockey news": "Hockey News",
    "espn": "ESPN",
    "cbs": "CBS",
    "ath": "The Athletic",
    "the athletic": "The Athletic",
    "sports forecaster": "Sports Forecaster",
    "the sports forecaster": "Sports Forecaster",
    "for": "Sports Forecaster",
    "rg": "_rank",
    "prenom": "_first",
    "nom": "_last",
    "prénom": "_first",
    "équipe": "_team",
    "equipe": "_team",
    "moy": "_average",
    "moyenne": "_average",
    "ecart": "_std",
    "�ge": "_age",
}


def _normalize_source(name: str) -> str:
    """Map a raw column/source name to canonical form."""
    key = name.strip().lower()
    return SOURCE_ALIASES.get(key, name.strip())


# Columns to skip — not actual projection sources
SKIP_COLUMNS = {
    "_rank", "_first", "_last", "_team", "_age", "_average", "_std",
    "colonne1", "colonne2", "colonne3", "colonne32",
    "écart-type", "ecart-type", "ecart", "e�cart",
    "moy. sans hm", "moy - hm", "moyenne", "moy",
    "mj", "final", "différence", "difference",
    "rang",     "ége", "ège", "ège", "age", "�ge",
}


def _is_source_column(name: str) -> bool:
    """Check if a column name represents an actual projection source."""
    key = name.strip().lower()
    if key in SKIP_COLUMNS:
        return False
    if key.startswith("unnamed"):
        return False
    if key.startswith("colonne"):
        return False
    # Catch any "age" variant (encoding issues in Excel)
    if len(key) <= 5 and "ge" in key:
        return False
    norm = _normalize_source(name)
    if norm.startswith("_"):
        return False
    # Skip columns ending in "2" that are likely difference or ranking columns
    if key.endswith("2") and len(key) > 2:
        base = key[:-1].rstrip(".")
        if base in SKIP_COLUMNS or base in SOURCE_ALIASES:
            return False
    # Skip HM.1, HM.2 etc (duplicate columns from different sheets)
    if re.match(r"^hm\.\d+$", key):
        return False
    return True


def _normalize_team(team: str) -> str:
    """Normalize team abbreviations to standard 3-letter codes."""
    if not team:
        return ""
    t = team.strip().upper()
    # Common variations
    team_map = {
        "FLO": "FLA",
        "TBL": "TBL",
        "TB": "TBL",
        "SJ": "SJS",
        "LA": "LAK",
        "LAK": "LAK",
        "CGY": "CGY",
        "WIN": "WPG",
        "WPG": "WPG",
        "VGK": "VGK",
        "UTA": "UTA",
        "CLB": "CBJ",
        "CBJ": "CBJ",
        "COL": "COL",
        "OTT": "OTT",
        "NSH": "NSH",
        "ANA": "ANA",
        "DET": "DET",
        "BOS": "BOS",
        "TOR": "TOR",
        "MTL": "MTL",
        "NYR": "NYR",
        "NYI": "NYI",
        "NJD": "NJD",
        "WSH": "WSH",
        "PIT": "PIT",
        "PHI": "PHI",
        "CAR": "CAR",
        "FLA": "FLA",
        "TBL": "TBL",
        "EDM": "EDM",
        "MIN": "MIN",
        "DAL": "DAL",
        "CHI": "CHI",
        "STL": "STL",
        "ARI": "ARI",
        "BUF": "BUF",
        "VAN": "VAN",
        "CBJ": "CBJ",
        "SJS": "SJS",
    }
    return team_map.get(t, t)


def _parse_name(name: str) -> tuple[str, str]:
    """Split 'First Last' into (first, last). Handles multi-part names."""
    parts = name.strip().split()
    if len(parts) == 0:
        return ("", "")
    if len(parts) == 1:
        return ("", parts[0])
    return (parts[0], " ".join(parts[1:]))


# ---------------------------------------------------------------------------
# Excel parsers — one per year format
# ---------------------------------------------------------------------------


def _parse_2016(path: Path) -> pd.DataFrame:
    """2016 format: 'Liste' sheet with columns Prenom, Nom, then source columns."""
    df = pd.read_excel(path, sheet_name="Liste")
    source_cols = {}
    for col in df.columns:
        name = str(col).strip()
        if _is_source_column(name):
            source_cols[col] = _normalize_source(name)

    rows = []
    for _, row in df.iterrows():
        first = str(row.get("Prenom", "")).strip()
        last = str(row.get("Nom", "")).strip()
        if not first and not last:
            continue
        if last.lower() in ("nan", ""):
            continue
        for col, source in source_cols.items():
            val = row.get(col)
            if pd.notna(val) and isinstance(val, (int, float)) and val > 0:
                rows.append({
                    "first_name": first,
                    "last_name": last,
                    "player_name": f"{first} {last}",
                    "team": "",
                    "source": source,
                    "projected_points": int(val),
                })
    return pd.DataFrame(rows)


def _parse_2017(path: Path) -> pd.DataFrame:
    """2017 format: 'Liste' sheet with header on row 2 (index 1)."""
    df = pd.read_excel(path, sheet_name="Liste", header=1)
    source_cols = {}
    for col in df.columns:
        name = str(col).strip()
        if _is_source_column(name):
            source_cols[col] = _normalize_source(name)

    rows = []
    for _, row in df.iterrows():
        first = str(row.get("Prenom", "")).strip()
        last = str(row.get("Nom", "")).strip()
        if not first or not last or last.lower() == "nan":
            continue
        for col, source in source_cols.items():
            val = row.get(col)
            if pd.notna(val) and isinstance(val, (int, float)) and val > 0:
                rows.append({
                    "first_name": first,
                    "last_name": last,
                    "player_name": f"{first} {last}",
                    "team": "",
                    "source": source,
                    "projected_points": int(val),
                })
    return pd.DataFrame(rows)


def _parse_2018(path: Path) -> pd.DataFrame:
    """2018 format: 'Feuil1' with RG(rank), HM(rank), NOM, last, EQUIPE, HM.1(points), MOY, PP, FOR, HN."""
    df = pd.read_excel(path, sheet_name="Feuil1")

    # 2018 has duplicate HM columns: HM=rank, HM.1=actual points
    # Map columns explicitly to avoid confusion
    col_map = {}
    for col in df.columns:
        name = str(col).strip()
        if name == "HM.1":
            col_map[col] = "Hockey Le Magazine"
        elif name == "HM":
            continue  # skip — this is the rank column
        elif _is_source_column(name):
            col_map[col] = _normalize_source(name)

    rows = []
    for _, row in df.iterrows():
        first = str(row.get(df.columns[2], "")).strip()
        last = str(row.get(df.columns[3], "")).strip()
        team = str(row.get(df.columns[4], "")).strip()
        if not first or not last or last.lower() == "nan":
            continue
        for col, source in col_map.items():
            val = row.get(col)
            if pd.notna(val) and isinstance(val, (int, float)) and val > 0:
                rows.append({
                    "first_name": first,
                    "last_name": last,
                    "player_name": f"{first} {last}",
                    "team": _normalize_team(team),
                    "source": source,
                    "projected_points": int(val),
                })
    return pd.DataFrame(rows)


def _parse_2019(path: Path) -> pd.DataFrame:
    """2019 format: single cell per row with all data concatenated."""
    df = pd.read_excel(path, sheet_name="Feuil1", header=None)
    all_text = " ".join(str(v) for v in df.iloc[:, 0] if pd.notna(v))

    # Pattern: rank Name Team src1 src2 src3 src4 avg
    # Names can have accents, hyphens, dots
    pattern = (
        r"(\d+)\s+"
        r"([\w\s\.\-\u00e9\u00e8\u00ea\u00eb\u00ef\u00ee\u00f4\u00fb"
        r"\u00fc\u00e7\u00f1\u00e1\u00e0\u014c\u00d4]+?)\s+"
        r"([A-Z]{2,4})\s+"
        r"(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+"
        r"([\d,]+)"
    )
    matches = re.findall(pattern, all_text)

    # 2019 sources: HN, HM, PPFOR, FOR (inferred from context)
    source_names = ["Hockey News", "Hockey Le Magazine", "PoolPro", "Sports Forecaster"]
    rows = []
    for m in matches:
        rank, name, team, s1, s2, s3, s4, avg = m
        first, last = _parse_name(name)
        for i, val in enumerate([s1, s2, s3, s4]):
            rows.append({
                "first_name": first,
                "last_name": last,
                "player_name": f"{first} {last}",
                "team": _normalize_team(team),
                "source": source_names[i],
                "projected_points": int(val),
            })
    return pd.DataFrame(rows)


def _parse_2020(path: Path) -> pd.DataFrame:
    """2020 format: 'Feuil1' with Rang, Nom, Equipe, source columns, Moyenne."""
    df = pd.read_excel(path, sheet_name="Feuil1")
    source_cols = {}
    for col in df.columns:
        name = str(col).strip()
        if _is_source_column(name):
            source_cols[col] = _normalize_source(name)

    rows = []
    for _, row in df.iterrows():
        name = str(row.get("Nom", "")).strip()
        team = str(row.get("\u00c9quipe", row.get("Equipe", ""))).strip()
        if not name or name.lower() == "nan":
            continue
        first, last = _parse_name(name)
        for col, source in source_cols.items():
            val = row.get(col)
            if pd.notna(val) and isinstance(val, (int, float)) and val > 0:
                rows.append({
                    "first_name": first,
                    "last_name": last,
                    "player_name": f"{first} {last}",
                    "team": _normalize_team(team),
                    "source": source,
                    "projected_points": int(val),
                })
    return pd.DataFrame(rows)


def _parse_2021(path: Path) -> pd.DataFrame:
    """2021 format: 'Feuil1' with Rang, Prenom, Nom, Equipe, HM, PP, HN, ATH, MOY."""
    df = pd.read_excel(path, sheet_name="Feuil1")
    source_cols = {}
    for col in df.columns:
        name = str(col).strip()
        if _is_source_column(name):
            source_cols[col] = _normalize_source(name)

    rows = []
    for _, row in df.iterrows():
        first = str(row.get("Pr\u00e9nom", row.get("Prenom", ""))).strip()
        last = str(row.get("Nom", "")).strip()
        team = str(row.get("\u00c9quipe", row.get("Equipe", ""))).strip()
        if not first or not last or last.lower() == "nan":
            continue
        for col, source in source_cols.items():
            val = row.get(col)
            if pd.notna(val) and isinstance(val, (int, float)) and val > 0:
                rows.append({
                    "first_name": first,
                    "last_name": last,
                    "player_name": f"{first} {last}",
                    "team": _normalize_team(team),
                    "source": source,
                    "projected_points": int(val),
                })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# New multi-sheet file parser (2022-2025)
# ---------------------------------------------------------------------------

NEW_FILE_PATH = EXCEL_DIR / "Liste des listes 2022-2025.xlsx"

# Source column detection for the new file — map raw column names to canonical source names
NEW_SOURCE_MAP: dict[str, str] = {
    "fantrax": "Fantrax",
    "ft": "Fantrax",
    "pool pro": "PoolPro",
    "poolpro": "PoolPro",
    "pp": "PoolPro",
    "pool expert": "PoolExpert",
    "pe": "PoolExpert",
    "espn": "ESPN",
    "hm": "Hockey Le Magazine",
    "hn": "Hockey News",
}

# Columns to skip in the new file
NEW_SKIP_COLUMNS = {
    "rang", "rg", "position", "prénom", "prenom", "nom", "équipe", "equipe",
    "moyenne", "moy", "moy.", "écart-type", "ecart-type",
}


def _detect_new_source(col_name: str) -> Optional[str]:
    """Detect if a column name is a projection source in the new file format."""
    key = col_name.strip().lower()
    if key in NEW_SKIP_COLUMNS:
        return None
    if key.startswith("unnamed") or key.startswith("colonne"):
        return None
    return NEW_SOURCE_MAP.get(key)


def _parse_new_file(path: Path) -> pd.DataFrame:
    """
    Parse the multi-sheet file containing seasons 2022-2025.

    Each sheet has a different column layout:
    - 2022: RG, NOM (full name), Équipe, HM, PP, HN, ESPN, MOY
    - 2023: Rang, Prénom, Nom, Équipe, FT, ESPN, PE, Moy
    - 2024: Rang, Prénom, Nom, Position, Équipe, Fantrax, Pool Expert, ESPN, Moyenne
    - 2025: Rang, Prénom, Nom, Position, Équipe, Fantrax, Pool Pro, ESPN, Moyenne
    """
    xl = pd.ExcelFile(path)
    all_dfs = []

    for sheet_name in xl.sheet_names:
        year = int(sheet_name)
        season_id = FILE_YEAR_TO_SEASON.get(year)
        if not season_id:
            logger.warning("Unknown year %d in new file, skipping", year)
            continue

        df = pd.read_excel(xl, sheet_name=sheet_name)

        # Detect source columns
        source_cols: dict[str, str] = {}
        for col in df.columns:
            name = str(col).strip()
            src = _detect_new_source(name)
            if src:
                source_cols[col] = src

        if not source_cols:
            logger.warning("No source columns found in sheet %s", sheet_name)
            continue

        rows = []
        for _, row in df.iterrows():
            # 2022 has full name in NOM column, others have Prénom + Nom
            nom_val = str(row.get("NOM", "")).strip() if "NOM" in df.columns else ""
            prenom_col = "Prénom" if "Prénom" in df.columns else "Pr\u00e9nom"
            first = str(row.get(prenom_col, "")).strip() if prenom_col in df.columns else ""
            last = str(row.get("Nom", "")).strip()

            if nom_val and nom_val.lower() != "nan" and not first:
                # Full name in single column (2022 format)
                first, last = _parse_name(nom_val)

            team_col = "Équipe" if "Équipe" in df.columns else "\u00c9quipe"
            team = str(row.get(team_col, "")).strip() if team_col in df.columns else ""

            if not first or not last or last.lower() == "nan":
                continue

            for col, source in source_cols.items():
                val = row.get(col)
                if pd.notna(val) and isinstance(val, (int, float)) and val > 0:
                    rows.append({
                        "first_name": first,
                        "last_name": last,
                        "player_name": f"{first} {last}",
                        "team": _normalize_team(team),
                        "source": source,
                        "projected_points": int(val),
                    })

        sheet_df = pd.DataFrame(rows)
        if not sheet_df.empty:
            sheet_df["season_id"] = season_id
            sheet_df["file_year"] = year
            all_dfs.append(sheet_df)
            logger.info("  %s: %d projections from %d sources", sheet_name, len(sheet_df), sheet_df["source"].nunique())

    return pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()


PARSERS: dict[int, callable] = {
    2016: _parse_2016,
    2017: _parse_2017,
    2018: _parse_2018,
    2019: _parse_2019,
    2020: _parse_2020,
    2021: _parse_2021,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_expert_projections(
    seasons: Optional[list[int]] = None,
) -> pd.DataFrame:
    """
    Load expert preseason point projections from all available Excel files.

    Parameters
    ----------
    seasons : list[int], optional
        Filter to specific file years (2016-2025). None = all.

    Returns
    -------
    pd.DataFrame with columns:
        first_name, last_name, player_name, team, source, projected_points,
        season_id, file_year
    """
    all_dfs = []
    years = seasons if seasons else sorted(PARSERS.keys())

    # Parse individual year files (2016-2021)
    for year in years:
        if year > 2021:
            continue  # handled by new multi-sheet file
        path = EXCEL_DIR / f"Liste des listes_{year}.xlsx"
        if not path.exists():
            logger.warning("File not found: %s", path)
            continue
        parser = PARSERS.get(year)
        if not parser:
            logger.warning("No parser for year %d", year)
            continue
        logger.info("Parsing %s ...", path.name)
        df = parser(path)
        if df.empty:
            logger.warning("No data extracted from %s", path.name)
            continue
        df["season_id"] = FILE_YEAR_TO_SEASON[year]
        df["file_year"] = year
        all_dfs.append(df)
        logger.info("  → %d projections from %d sources", len(df), df["source"].nunique())

    # Parse new multi-sheet file (2022-2025)
    if NEW_FILE_PATH.exists():
        if seasons:
            new_years = [y for y in seasons if y >= 2022]
        else:
            new_years = list(range(2022, 2026))
        if new_years:
            logger.info("Parsing %s ...", NEW_FILE_PATH.name)
            new_df = _parse_new_file(NEW_FILE_PATH)
            if not new_df.empty:
                new_df = new_df[new_df["file_year"].isin(new_years)]
                if not new_df.empty:
                    all_dfs.append(new_df)
                    logger.info("  → %d total projections from new file", len(new_df))

    if not all_dfs:
        return pd.DataFrame()

    result = pd.concat(all_dfs, ignore_index=True)
    result["team"] = result["team"].apply(_normalize_team)
    return result


def load_actual_results(
    seasons: Optional[list[int]] = None,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Fetch actual results for multiple seasons.
    Tries cache first, then falls back to Hockey Reference scraping.

    Parameters
    ----------
    seasons : list[int], optional
        Season IDs to fetch. None = all expert projection seasons.
    use_cache : bool
        Whether to use/load from disk cache.

    Returns
    -------
    pd.DataFrame with actual results plus season_id column.
    """
    if seasons is None:
        seasons = sorted(FILE_YEAR_TO_SEASON.values())

    cache_path = CACHE_DIR / "actuals_cache.json"

    # Try loading from cache
    if use_cache and cache_path.exists():
        try:
            cached = pd.read_json(cache_path)
            cached_seasons = set(cached["season_id"].unique()) if "season_id" in cached.columns else set()
            missing = [s for s in seasons if s not in cached_seasons]
            if not missing:
                result = cached[cached["season_id"].isin(seasons)].copy()
                logger.info("Loaded %d records from cache for %d seasons", len(result), len(seasons))
                return result
            else:
                logger.info("Cache has %d seasons, missing: %s", len(cached_seasons), missing)
                existing = cached[cached["season_id"].isin([s for s in seasons if s in cached_seasons])]
                # Fetch missing seasons from Hockey Reference
                new_dfs = [existing]
                for sid in missing:
                    df = _fetch_from_hockey_reference(sid)
                    if not df.empty:
                        new_dfs.append(df)
                    time.sleep(1)
                result = pd.concat(new_dfs, ignore_index=True)
                result.to_json(cache_path, orient="records", force_ascii=False, indent=1)
                logger.info("Updated cache with %d total records", len(result))
                return result
        except Exception as e:
            logger.warning("Cache load failed: %s", e)

    # No cache — fetch all from Hockey Reference
    all_dfs = []
    for sid in seasons:
        df = _fetch_from_hockey_reference(sid)
        if not df.empty:
            all_dfs.append(df)
        time.sleep(1)

    result = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
    if not result.empty:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        result.to_json(cache_path, orient="records", force_ascii=False, indent=1)
    return result


def _fetch_from_hockey_reference(season_id: int) -> pd.DataFrame:
    """Fetch actual results from Hockey Reference (fast, ~1 request per season)."""
    from io import StringIO

    SEASON_TO_HR_YEAR = {
        20162017: 2017,
        20172018: 2018,
        20182019: 2019,
        20192020: 2020,
        20202021: 2021,
        20212022: 2022,
        20222023: 2023,
        20232024: 2024,
        20242025: 2025,
    }

    year = SEASON_TO_HR_YEAR.get(season_id)
    if not year:
        logger.warning("No Hockey Reference mapping for season %d", season_id)
        return pd.DataFrame()

    url = f"https://www.hockey-reference.com/leagues/NHL_{year}_skaters.html"
    logger.info("Fetching %s ...", url)

    try:
        resp = requests.get(url, headers=HR_HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return pd.DataFrame()

    try:
        tables = pd.read_html(StringIO(resp.text), attrs={"id": "player_stats"})
    except Exception as e:
        logger.error("Failed to parse HTML for %d: %s", season_id, e)
        return pd.DataFrame()

    if not tables:
        logger.warning("No stats table found for %d", season_id)
        return pd.DataFrame()

    df = tables[0]
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(-1)

    df = df[df["Player"].notna()].copy()
    df = df[~df["Player"].str.match(r"^Rk$", na=False)]
    df = df[~df["Player"].str.startswith("Rk", na=False)]

    team_col = "Team" if "Team" in df.columns else "Tm"

    result = pd.DataFrame({
        "player_name": df["Player"].str.strip(),
        "first_name": df["Player"].str.strip().str.split(" ").str[0],
        "last_name": df["Player"].str.strip().str.split(" ").str[1:].str.join(" "),
        "team": df[team_col].str.strip(),
        "position": df.get("Pos", pd.Series([""] * len(df))).str.strip(),
        "games_played": pd.to_numeric(df["GP"], errors="coerce").fillna(0).astype(int),
        "goals": pd.to_numeric(df["G"], errors="coerce").fillna(0).astype(int),
        "assists": pd.to_numeric(df["A"], errors="coerce").fillna(0).astype(int),
        "points": pd.to_numeric(df["PTS"], errors="coerce").fillna(0).astype(int),
    })
    result["points_per_game"] = (result["points"] / result["games_played"].clip(lower=1)).round(3)
    result["season_id"] = season_id
    result = result[result["team"] != "TOT"].copy()

    logger.info("  Got %d players for %d", len(result), season_id)
    return result


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def match_experts_to_actuals(
    experts: pd.DataFrame,
    actuals: pd.DataFrame,
) -> pd.DataFrame:
    """
    Match expert projections to actual results by player name + season.

    Returns
    -------
    pd.DataFrame with columns:
        player_name, team, season_id, source, projected_points,
        actual_points, games_played, error, abs_error
    """
    merged_parts = []

    for season in experts["season_id"].unique():
        exp_s = experts[experts["season_id"] == season].copy()
        act_s = actuals[actuals["season_id"] == season].copy()
        if act_s.empty:
            continue

        # Normalize names for matching
        exp_s["_match_key"] = (
            exp_s["first_name"].str.lower().str.strip()
            + " "
            + exp_s["last_name"].str.lower().str.strip()
        )

        # Build actuals lookup: name -> (points, gp, team) — take highest-scoring entry per name
        act_lookup = (
            act_s.copy()
            .assign(_match_key=lambda d: (
                d["first_name"].str.lower().str.strip()
                + " "
                + d["last_name"].str.lower().str.strip()
            ))
            .sort_values("points", ascending=False)
            .drop_duplicates(subset="_match_key", keep="first")
            .set_index("_match_key")[["points", "games_played", "team"]]
        )

        # Merge
        merged = exp_s.merge(
            act_lookup.reset_index(),
            on="_match_key",
            how="left",
            suffixes=("", "_actual"),
        )
        merged["actual_points"] = merged["points"]
        merged["error"] = merged["projected_points"] - merged["actual_points"]
        merged["abs_error"] = merged["error"].abs()
        merged = merged.drop(columns=["points", "_match_key"], errors="ignore")
        merged_parts.append(merged)

    return pd.concat(merged_parts, ignore_index=True) if merged_parts else pd.DataFrame()


# ---------------------------------------------------------------------------
# Comparison metrics
# ---------------------------------------------------------------------------


def compute_expert_metrics(matched: pd.DataFrame) -> pd.DataFrame:
    """
    Compute accuracy metrics per expert source per season.

    Returns
    -------
    pd.DataFrame with columns:
        source, season_id, n_players, mae, median_ae, rmse,
        correlation, within_5, within_10, within_15
    """
    import numpy as np

    # Drop rows with no actual result
    matched = matched.dropna(subset=["actual_points"]).copy()
    matched["actual_points"] = matched["actual_points"].astype(float)

    rows = []
    for (source, season), grp in matched.groupby(["source", "season_id"]):
        n = len(grp)
        if n < 5:
            continue
        pred = grp["projected_points"].values.astype(float)
        actual = grp["actual_points"].values
        errors = pred - actual
        abs_errors = np.abs(errors)

        corr = np.corrcoef(pred, actual)[0, 1] if n > 2 else np.nan
        within_5 = (abs_errors <= 5).sum() / n * 100
        within_10 = (abs_errors <= 10).sum() / n * 100
        within_15 = (abs_errors <= 15).sum() / n * 100

        rows.append({
            "source": source,
            "season_id": season,
            "n_players": n,
            "mae": round(float(np.mean(abs_errors)), 2),
            "median_ae": round(float(np.median(abs_errors)), 2),
            "rmse": round(float(np.sqrt(np.mean(errors ** 2))), 2),
            "correlation": round(float(corr), 4) if not np.isnan(corr) else None,
            "within_5_pct": round(within_5, 1),
            "within_10_pct": round(within_10, 1),
            "within_15_pct": round(within_15, 1),
            "mean_bias": round(float(np.mean(errors)), 2),
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    print("Loading expert projections...")
    experts = load_expert_projections()
    print(f"  {len(experts)} total projections")
    print(f"  {experts['source'].nunique()} sources: {experts['source'].unique().tolist()}")
    print(f"  Seasons: {sorted(experts['season_id'].unique())}")

    print("\nFetching actual results...")
    actuals = load_actual_results()
    print(f"  {len(actuals)} player-seasons")

    print("\nMatching projections to actuals...")
    matched = match_experts_to_actuals(experts, actuals)
    print(f"  {len(matched)} matched records")

    if len(experts) > 0:
        print(f"  Match rate: {len(matched) / len(experts) * 100:.1f}%")

    print("\n=== EXPERT SOURCE METRICS (per source per season) ===")
    metrics = compute_expert_metrics(matched)
    if not metrics.empty:
        print(metrics.to_string(index=False))

        print("\n=== OVERALL RANKING (averaged across seasons) ===")
        import numpy as np
        overall = metrics.groupby("source").agg({
            "mae": "mean",
            "median_ae": "mean",
            "rmse": "mean",
            "correlation": "mean",
            "within_10_pct": "mean",
            "mean_bias": "mean",
            "n_players": "mean",
        }).sort_values("mae")
        print(overall.round(2).to_string())
    else:
        print("  No metrics computed (not enough matches)")

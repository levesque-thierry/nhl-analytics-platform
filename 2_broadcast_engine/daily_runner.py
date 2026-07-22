"""
daily_runner.py — Daily StreakAnomaly Pipeline

Scheduled pipeline that evaluates all active skaters for the current season
and outputs broadcast-ready streak anomaly reports.

Pipeline steps:
    1. Optionally refresh baselines cache
    2. Evaluate all active skaters against baselines
    3. Output results as JSON lines and/or pretty-printed report
    4. Write summary to output file

CLI Usage:
    # Full daily run (evaluate + output)
    python daily_runner.py

    # Refresh baselines first, then evaluate
    python daily_runner.py --refresh-baselines

    # Custom season and output
    python daily_runner.py --season 20252026 --output anomalies.jsonl

    # Pretty-print to stdout only
    python daily_runner.py --pretty
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Ensure imports resolve
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "1_data_warehouse"))

from baselines import compute_all_baselines, save_baselines, load_baselines
from streak_engine import (
    StreakAnomaly,
    evaluate_all_players,
)

DEFAULT_OUTPUT_DIR: Path = Path(__file__).parent / "output"


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------

def refresh_baselines(db_path: str) -> dict:
    """Rebuild baselines from the database and save to cache."""
    logger.info("Refreshing baselines from database...")
    baselines = compute_all_baselines(db_path)
    save_baselines(baselines)
    logger.info("Baselines refreshed and cached.")
    return baselines


def run_evaluation(
    season: str,
    min_streak_length: int = 2,
    min_novelty: float = 0.5,
    min_games: int = 10,
) -> list[StreakAnomaly]:
    """Run the streak anomaly evaluation for all active skaters."""
    logger.info(
        "Evaluating streaks for season %s (min_length=%d, min_novelty=%.2f)",
        season, min_streak_length, min_novelty,
    )
    anomalies = evaluate_all_players(
        season=season,
        min_streak_length=min_streak_length,
        min_games=min_games,
        min_novelty=min_novelty,
    )
    logger.info("Found %d anomalies meeting threshold.", len(anomalies))
    return anomalies


def write_jsonl(anomalies: list[StreakAnomaly], output_path: Path) -> None:
    """Write anomalies as JSON lines to a file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for a in anomalies:
            f.write(json.dumps(a.to_dict(), ensure_ascii=False) + "\n")
    logger.info("JSONL output written to %s (%d records).", output_path, len(anomalies))


def write_summary(anomalies: list[StreakAnomaly], output_path: Path) -> None:
    """Write a human-readable summary report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("  NHL StreakAnomaly Daily Report")
    lines.append(f"  Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"  Total anomalies: {len(anomalies)}")
    lines.append("=" * 72)
    lines.append("")

    # Group by severity
    severity_groups: dict[str, list[StreakAnomaly]] = {}
    for a in anomalies:
        severity_groups.setdefault(a.severity, []).append(a)

    for severity in ["EXTREMELY RARE", "VERY RARE", "RARE", "UNCOMMON", "COMMON"]:
        group = severity_groups.get(severity, [])
        if not group:
            continue
        lines.append(f"--- {severity} ({len(group)} streaks) ---")
        lines.append("")
        for a in group:
            s = a.streak
            lines.append(
                f"  {a.player_name} ({a.team}, {a.position}) — "
                f"{s.description}"
            )
            lines.append(
                f"    Length: {s.length} games  |  "
                f"{s.start_date} to {s.end_date}"
            )
            for r in a.rarity_scores:
                lines.append(
                    f"    {r.level:>6}: P={r.probability:.4f}  rarity={r.rarity:.4f}"
                )
            lines.append(f"    novelty_index={a.novelty_index:.4f}")
            lines.append("")

    # Top 10 most notable
    lines.append("=" * 72)
    lines.append("  TOP 10 NOTABLE STREAKS")
    lines.append("=" * 72)
    for i, a in enumerate(anomalies[:10], 1):
        s = a.streak
        lines.append(
            f"  {i}. [{a.severity}] {a.player_name} ({a.team}) — "
            f"{s.length}G {s.streak_type} (NI={a.novelty_index:.4f})"
        )
    lines.append("")

    report = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)
    logger.info("Summary report written to %s.", output_path)


def pretty_print(anomalies: list[StreakAnomaly]) -> None:
    """Print anomalies to stdout in human-readable format."""
    print(f"\n=== StreakAnomaly Report — {len(anomalies)} anomalies ===\n")
    for a in anomalies:
        s = a.streak
        print(
            f"  [{a.severity}] {a.player_name} ({a.team}, {a.position}) — "
            f"{s.description}: {s.length} games "
            f"({s.start_date} to {s.end_date})"
        )
        for r in a.rarity_scores:
            print(f"    {r.level:>6}: P={r.probability:.4f}  rarity={r.rarity:.4f}")
        print(f"    novelty_index={a.novelty_index:.4f}")
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Daily StreakAnomaly pipeline — evaluate active player streaks."
    )
    parser.add_argument(
        "--season",
        type=str,
        default="20252026",
        help="Season to evaluate (default: 20252026).",
    )
    parser.add_argument(
        "--refresh-baselines",
        action="store_true",
        help="Rebuild baselines from database before evaluating.",
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
        "--min-games",
        type=int,
        default=10,
        help="Minimum games played to evaluate (default: 10).",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSONL file path (default: output/<season>_anomalies.jsonl).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print results to stdout.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress stdout output (write files only).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    # Step 1: Optionally refresh baselines
    if args.refresh_baselines:
        refresh_baselines(None)  # Uses default DB_PATH

    # Step 2: Run evaluation
    anomalies = run_evaluation(
        season=args.season,
        min_streak_length=args.min_length,
        min_novelty=args.min_novelty,
        min_games=args.min_games,
    )

    # Step 3: Output results
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = DEFAULT_OUTPUT_DIR / f"{args.season}_anomalies.jsonl"

    write_jsonl(anomalies, output_path)

    summary_path = output_path.with_suffix(".summary.txt")
    write_summary(anomalies, summary_path)

    if args.pretty or not args.quiet:
        pretty_print(anomalies)

    logger.info("Pipeline complete.")

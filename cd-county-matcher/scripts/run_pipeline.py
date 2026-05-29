#!/usr/bin/env python3
"""
Run the full cd-county-matcher pipeline end to end:

    1. run_matcher.py          -> data/results/matches.csv
    2. backfill_state.py       -> data/results/matches_state_filled.csv
    3. add_uniform_cd.py       -> data/results/matches_with_uniform_cd_shifted.csv
    4. analyze_redistricting.py-> data/results/redistricting_analysis.csv (+ .txt)

Each stage is the same script you can run standalone; this just chains them in
the right order with consistent file paths so you don't have to.

    # Everything, 1984-2025:
    python scripts/run_pipeline.py --start 1984 --end 2025

    # Skip the (slow) matcher and re-run only the post-processing on an
    # existing matches.csv:
    python scripts/run_pipeline.py --skip-matcher

    # Pass options through to individual stages:
    python scripts/run_pipeline.py --years 2020 2022 2024 --threshold 10
"""

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent


def run_step(name, argv):
    """Run one pipeline script as a subprocess, aborting on failure."""
    print("\n" + "#" * 80)
    print(f"# STAGE: {name}")
    print("#" * 80)
    cmd = [sys.executable, str(SCRIPTS_DIR / name)] + argv
    print("$ " + " ".join(cmd) + "\n")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\n[abort] stage '{name}' exited with code {result.returncode}")
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", default="./data")

    # Matcher options (forwarded to run_matcher.py).
    parser.add_argument("--start", type=int, default=1984)
    parser.add_argument("--end", type=int, default=2025)
    parser.add_argument("--years", type=int, nargs="+", default=None)
    parser.add_argument("--nhgis-key", default=None)
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Forwarded to the matcher: compute from already-downloaded files",
    )
    parser.add_argument(
        "--skip-matcher",
        action="store_true",
        help="Skip stage 1 entirely; start from an existing matches.csv",
    )

    # Post-processing options.
    parser.add_argument(
        "--territory-policy", choices=["fill", "drop"], default="fill"
    )
    parser.add_argument("--year-shift", type=int, default=-1)
    parser.add_argument("--threshold", type=float, default=20.0)

    args = parser.parse_args()
    dd = ["--data-dir", args.data_dir]

    # Stage 1: matcher.
    if not args.skip_matcher:
        matcher_argv = list(dd)
        if args.years:
            matcher_argv += ["--years"] + [str(y) for y in args.years]
        else:
            matcher_argv += ["--start", str(args.start), "--end", str(args.end)]
        if args.nhgis_key:
            matcher_argv += ["--nhgis-key", args.nhgis_key]
        if args.skip_download:
            matcher_argv += ["--skip-download"]
        run_step("run_matcher.py", matcher_argv)
    else:
        print("[skip] stage 1 (matcher) — using existing matches.csv")

    # Stage 2: backfill state_name.
    run_step(
        "backfill_state.py",
        dd + ["--territory-policy", args.territory_policy],
    )

    # Stage 3: uniform CD + year shift.
    run_step("add_uniform_cd.py", dd + ["--year-shift", str(args.year_shift)])

    # Stage 4: redistricting analysis.
    run_step(
        "analyze_redistricting.py", dd + ["--threshold", str(args.threshold)]
    )

    results_dir = Path(args.data_dir) / "results"
    print("\n" + "=" * 80)
    print("PIPELINE COMPLETE")
    print("=" * 80)
    print("Final outputs in", results_dir, ":")
    print("  matches.csv")
    print("  matches_state_filled.csv")
    print("  matches_with_uniform_cd_shifted.csv")
    print("  redistricting_analysis.csv")
    print("  redistricting_summary.txt")


if __name__ == "__main__":
    main()

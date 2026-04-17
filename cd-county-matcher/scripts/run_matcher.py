#!/usr/bin/env python3
"""
Run the CD-to-County matcher for a range of years.

    python scripts/run_matcher.py --start 1984 --end 2025
    python scripts/run_matcher.py --years 2020 2022 2024 --nhgis-key YOUR_KEY

The NHGIS API key can also be supplied via the NHGIS_API_KEY environment
variable. If omitted, NHGIS sources are skipped (the other sources usually
cover every year).
"""

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.matcher import CDCountyMatcher  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=int, default=1984)
    parser.add_argument("--end", type=int, default=2025)
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        help="Explicit list of years (overrides --start/--end)",
    )
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument(
        "--nhgis-key",
        default=os.environ.get("NHGIS_API_KEY"),
        help="NHGIS API Bearer token (or set NHGIS_API_KEY env var)",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip the download phase; compute matches from existing files only",
    )
    args = parser.parse_args()

    years = args.years or list(range(args.start, args.end + 1))
    print(f"Running matcher for {len(years)} years: {years[0]}-{years[-1]}")

    matcher = CDCountyMatcher(
        data_dir=args.data_dir,
        nhgis_api_key=args.nhgis_key,
    )

    if not args.skip_download:
        matcher.download_data(years)
    matches = matcher.compute_matches(years)

    out_path = Path(args.data_dir) / "results" / "matches.csv"
    print(f"\nDone. {len(matches)} rows written to {out_path}")


if __name__ == "__main__":
    main()

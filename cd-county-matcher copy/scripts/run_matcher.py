#!/usr/bin/env python3
"""
Run the CD-to-County matcher for a range of years.

    python scripts/run_matcher.py --start 1984 --end 2025
    python scripts/run_matcher.py --years 2020 2022 2024 --nhgis-key YOUR_KEY

The NHGIS API key can also be supplied via the NHGIS_API_KEY environment
variable. If omitted, NHGIS sources are skipped (the other sources usually
cover every year).

This script also applies two runtime performance patches to CDCountyMatcher
so you don't need to edit src/matcher.py:

  1. Year-aware county source ordering — pre-2000 years go straight to
     Newberry instead of waiting on TIGER URLs that don't exist. Similarly,
     pre-2013 years skip the cartographic (GENZ) source that starts in 2013.
  2. Tighter HTTP timeouts — (10s connect, 300s read) instead of 600s, and
     404 responses aren't retried. This turns multi-minute hangs into
     multi-second "that URL doesn't exist, moving on" messages.
"""

import argparse
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.matcher import CDCountyMatcher  # noqa: E402


# ---------------------------------------------------------------------------
# Runtime patches — applied to CDCountyMatcher before we instantiate it so
# you get the fixes without editing the package source.
# ---------------------------------------------------------------------------

def _patched_get_strategy(self, year: int) -> Dict[str, List[str]]:
    """Year-aware source ordering.

    Skips sources that can't possibly have data for the given year, so we
    don't burn time on doomed HTTP requests:
        TIGER         starts ~2000
        Census GENZ   starts ~2013
        Newberry      covers 1629-2000 (our go-to for pre-2000)
    """
    if year in self.manual_cd_map:
        cd_sources = ["manual_cd"]
    else:
        cd_sources = ["ucla_github"]
        if year >= 2000:
            cd_sources.append("tiger")
        if self.nhgis_api_key:
            cd_sources.append("nhgis_api")

    county_sources = []
    if self._get_manual_county_path(year):
        county_sources.append("manual_nhgis")
    if year < 2000:
        county_sources.append("newberry_historical")
    elif year < 2013:
        county_sources.append("tiger")
        county_sources.append("newberry_historical")
    else:
        county_sources.append("tiger")
        county_sources.append("census_cartographic")
    if self.nhgis_api_key:
        county_sources.append("nhgis_api")

    return {"cd_sources": cd_sources, "county_sources": county_sources}


def _patched_download_and_extract_with_retry(
    self, url: str, filepath: Path, max_retries: int = 3, headers: dict = None
) -> bool:
    """Same as the original but with (connect=10s, read=300s) timeouts and
    no retries on 404 — so dead URLs fail fast instead of sitting on 600s
    timeouts three times in a row.
    """
    for attempt in range(max_retries):
        try:
            response = self.session.get(
                url, timeout=(10, 300), verify=False, stream=True, headers=headers
            )
            if response.status_code == 404:
                return False  # URL doesn't exist, no point retrying
            if response.status_code == 429:
                wait_time = 2 ** attempt
                print(f"Rate limited, waiting {wait_time}s...")
                time.sleep(wait_time)
                continue
            response.raise_for_status()
            with open(filepath, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            with zipfile.ZipFile(filepath, "r") as zip_ref:
                zip_ref.extractall(filepath.parent)
            return True
        except Exception as e:
            print(f"Download/extract failed for {url}: {str(e)[:120]}")
            if filepath.exists():
                filepath.unlink()
    return False


def apply_patches():
    """Monkey-patch the two methods on CDCountyMatcher."""
    CDCountyMatcher._get_strategy = _patched_get_strategy
    CDCountyMatcher._download_and_extract_with_retry = (
        _patched_download_and_extract_with_retry
    )
    print("[patch] Applied year-aware source ordering + tightened timeouts.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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
    parser.add_argument(
        "--no-patches",
        action="store_true",
        help="Disable the runtime performance patches (not recommended)",
    )
    args = parser.parse_args()

    if not args.no_patches:
        apply_patches()

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

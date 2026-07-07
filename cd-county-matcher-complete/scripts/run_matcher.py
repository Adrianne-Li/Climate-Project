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

  1. OSF-first source ordering — congressional districts use UCLA
     districts098.zip ... districts119.zip for every year/cycle in 1984-2025.
     OSF is tried first, and public online sources are used only as fallbacks.
  2. Tighter HTTP timeouts — (10s connect, 300s read) instead of 600s, and
     404 responses aren't retried. This turns multi-minute hangs into
     multi-second "that URL doesn't exist, moving on" messages.
"""

import argparse
import os
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.matcher import (  # noqa: E402
    CDCountyMatcher,
    discover_osf_files,
    load_osf_project_guid,
)

try:  # noqa: E402
    from src.matcher import _extract_guid
except ImportError:  # pragma: no cover - compatibility with older matcher.py
    def _extract_guid(value: str) -> str:
        return str(value).rstrip("/").split("/")[-1]


_ORIGINAL_GET_MANUAL_COUNTY_PATH = None
_ORIGINAL_DOWNLOAD_COUNTY = None
_ORIGINAL_CALCULATE_OVERLAP = None
DEFAULT_SOURCE_CRS = os.environ.get("MATCHER_SOURCE_CRS", "EPSG:4269")


# ---------------------------------------------------------------------------
# Runtime patches — applied to CDCountyMatcher before we instantiate it so
# you get the fixes without editing the package source.
# ---------------------------------------------------------------------------

def _patched_get_strategy(self, year: int) -> Dict[str, List[str]]:
    """OSF-first source ordering.

    Congressional districts use UCLA for all 1984-2025 cycles: 1984 ->
    districts098.zip, 1985-1986 -> districts099.zip, ..., 2023-2024 ->
    districts118.zip, 2025 -> districts119.zip.
    """
    cd_sources = ["osf_storage", "ucla_github"]
    if self.nhgis_api_key:
        cd_sources.append("nhgis_api")

    county_sources = []
    if self._get_manual_county_path(year):
        county_sources.append("manual_nhgis")
    county_sources.append("osf_storage")
    if year < 2000:
        county_sources.append("newberry_historical")
    elif year < 2013:
        county_sources.extend(["tiger", "newberry_historical"])
    else:
        county_sources.extend(["tiger", "census_cartographic"])
    if self.nhgis_api_key:
        county_sources.append("nhgis_api")

    return {"cd_sources": cd_sources, "county_sources": county_sources}


def _shapefile_bundle_exists(shp_path: Path) -> bool:
    """Return True only when the core shapefile sidecars are present."""
    return (
        shp_path.exists()
        and shp_path.with_suffix(".shx").exists()
        and shp_path.with_suffix(".dbf").exists()
    )


def _annual_manual_county_candidates(data_dir: Path, year: int) -> List[Path]:
    """Manual county shapefile locations produced by scripts/setup_data.py."""
    manual_dir = data_dir / "manual_sources"
    candidates = []

    if year <= 2010:
        candidates.append(
            manual_dir / "county_2010" / "tl_2010_us_county10.shp"
        )
    else:
        candidates.append(
            manual_dir / f"county_{year}" / f"tl_{year}_us_county.shp"
        )

    # Conservative fallbacks for repos that already have a slightly different
    # manual folder convention.
    candidates.extend(
        [
            manual_dir / f"county_{year}" / f"tl_{year}_us_county{year % 100}.shp",
            manual_dir / f"tl_{year}_us_county" / f"tl_{year}_us_county.shp",
        ]
    )
    return candidates


def _patched_get_manual_county_path(self, year: int):
    """Recognize annual county files fetched from OSF into manual_sources."""
    data_dir = Path(getattr(self, "data_dir", "data"))
    for shp_path in _annual_manual_county_candidates(data_dir, year):
        if _shapefile_bundle_exists(shp_path):
            return shp_path

    if _ORIGINAL_GET_MANUAL_COUNTY_PATH is not None:
        return _ORIGINAL_GET_MANUAL_COUNTY_PATH(self, year)
    return None


def _copy_shapefile_bundle(src_shp: Path, dest_dir: Path) -> Path:
    """Copy one shapefile bundle into the matcher's raw county directory."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied_shp = dest_dir / src_shp.name
    for sidecar in src_shp.parent.glob(src_shp.stem + ".*"):
        if sidecar.is_file():
            shutil.copy2(sidecar, dest_dir / sidecar.name)
    return copied_shp


def _patched_download_county(self, year: int, source: str):
    """Use OSF-fetched annual county files before trying online fallbacks.

    The original matcher did not recognize tl_2012_us_county.* in
    data/manual_sources/county_2012, so it fell through to Census TIGER and hit
    repeated 403s. This hook accepts the local shapefile bundle directly.
    """
    if source == "manual_nhgis":
        shp_path = self._get_manual_county_path(year)
        if shp_path:
            data_dir = Path(getattr(self, "data_dir", "data"))
            target_dir = data_dir / "raw" / f"county_{year}"
            copied_shp = _copy_shapefile_bundle(Path(shp_path), target_dir)
            if _shapefile_bundle_exists(copied_shp):
                msg = f"Using manual county shapefile {copied_shp.name}"
                print(f" SUCCESS: {msg}")
                return True, msg

    return _ORIGINAL_DOWNLOAD_COUNTY(self, year, source)


def _with_default_crs(gdf, label: str, year: int):
    """Assign a CRS to shapefiles that were downloaded without .prj metadata.

    Some UCLA/OSF raw congressional district bundles have .shp/.shx/.dbf but no
    .prj sidecar. GeoPandas can still read their geometry, but it marks the
    CRS as None and later refuses to transform it. These source files are in
    latitude/longitude NAD83, matching TIGER/Line's EPSG:4269 convention.
    """
    if getattr(gdf, "crs", None) is None:
        print(
            f"[patch] {label} for {year} has no CRS metadata; "
            f"assuming {DEFAULT_SOURCE_CRS}."
        )
        return gdf.set_crs(DEFAULT_SOURCE_CRS, allow_override=True)
    return gdf


def _patched_calculate_overlap(self, cd_gdf, county_gdf, year: int):
    """Repair missing CRS metadata before the original overlap calculation."""
    cd_gdf = _with_default_crs(cd_gdf, "CD shapefile", year)
    county_gdf = _with_default_crs(county_gdf, "county shapefile", year)
    return _ORIGINAL_CALCULATE_OVERLAP(self, cd_gdf, county_gdf, year)


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


def _matcher_osf_files(matcher, args) -> dict:
    """Return the matcher's discovered OSF file map, discovering if needed."""
    for attr in (
        "osf_files",
        "_osf_files",
        "osf_storage_files",
        "_osf_storage_files",
        "osf_file_map",
        "_osf_file_map",
    ):
        files = getattr(matcher, attr, None)
        if isinstance(files, dict) and files:
            return files

    project_guid = args.osf_project or load_osf_project_guid(args.osf_config)
    if not project_guid:
        return {}

    project_guid = _extract_guid(project_guid)
    print(f"[patch] Discovering OSF files for .prj sidecar backfill: {project_guid}")
    return discover_osf_files(project_guid, token=args.osf_token) or {}


def _download_raw_file(matcher, url: str, dest: Path, token: str = None) -> bool:
    """Download one raw OSF file without treating it as a zip archive."""
    headers = {}
    if token and "osf.io" in url:
        headers["Authorization"] = f"Bearer {token}"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with matcher.session.get(
            url, stream=True, timeout=(10, 300), verify=False, headers=headers
        ) as response:
            if response.status_code == 404:
                print(f"  [404] missing OSF sidecar: {url}")
                return False
            response.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        return dest.exists() and dest.stat().st_size > 0
    except Exception as e:
        print(f"  [warn] could not download {dest.name}: {str(e)[:120]}")
        if dest.exists():
            dest.unlink()
        return False


def backfill_missing_prj_sidecars(matcher, args) -> None:
    """Download missing .prj files for already-downloaded shapefiles.

    Some OSF raw-file download paths fetch only .shp/.shx/.dbf. If the matching
    .prj exists in OSF, this puts it next to the local .shp before GeoPandas
    reads the file.
    """
    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        return

    shp_paths = sorted(data_dir.rglob("*.shp"))
    missing = [p for p in shp_paths if not p.with_suffix(".prj").exists()]
    if not missing:
        return

    osf_files = _matcher_osf_files(matcher, args)
    if not osf_files:
        print("[patch] No OSF file map available for .prj sidecar backfill.")
        return

    osf_by_lower = {name.lower(): url for name, url in osf_files.items()}
    fixed = 0
    skipped = 0
    for shp_path in missing:
        prj_name = f"{shp_path.stem}.prj"
        url = osf_by_lower.get(prj_name.lower())
        if not url:
            skipped += 1
            continue
        dest = shp_path.with_suffix(".prj")
        if _download_raw_file(matcher, url, dest, token=args.osf_token):
            print(f"[patch] downloaded missing CRS sidecar: {dest}")
            fixed += 1
        else:
            skipped += 1

    if fixed or skipped:
        print(
            f"[patch] .prj sidecar backfill complete: "
            f"{fixed} downloaded, {skipped} still missing."
        )


def apply_patches():
    """Monkey-patch the two methods on CDCountyMatcher."""
    global _ORIGINAL_GET_MANUAL_COUNTY_PATH
    global _ORIGINAL_DOWNLOAD_COUNTY
    global _ORIGINAL_CALCULATE_OVERLAP
    _ORIGINAL_GET_MANUAL_COUNTY_PATH = getattr(
        CDCountyMatcher, "_get_manual_county_path", None
    )
    _ORIGINAL_DOWNLOAD_COUNTY = getattr(CDCountyMatcher, "_download_county")
    _ORIGINAL_CALCULATE_OVERLAP = getattr(
        CDCountyMatcher, "_calculate_overlap"
    )

    CDCountyMatcher._get_strategy = _patched_get_strategy
    CDCountyMatcher._get_manual_county_path = _patched_get_manual_county_path
    CDCountyMatcher._download_county = _patched_download_county
    CDCountyMatcher._calculate_overlap = _patched_calculate_overlap
    CDCountyMatcher._download_and_extract_with_retry = (
        _patched_download_and_extract_with_retry
    )
    print(
        "[patch] Applied OSF/manual-first source ordering, annual county "
        "manual-source lookup, CRS repair, and tightened timeouts."
    )


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
        "--osf-project",
        default=os.environ.get("OSF_PROJECT_GUID"),
        help="Public OSF project GUID/URL to use as the primary data source. "
        "If omitted, _osf_project in osf_sources.json is used.",
    )
    parser.add_argument(
        "--osf-config",
        default=os.environ.get("OSF_SOURCES_JSON"),
        help="Optional path to osf_sources.json.",
    )
    parser.add_argument(
        "--osf-token",
        default=os.environ.get("OSF_TOKEN"),
        help="Optional OSF token; not needed for the public project.",
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
        osf_project=args.osf_project,
        osf_token=args.osf_token,
        osf_config=args.osf_config,
    )

    if not args.skip_download:
        matcher.download_data(years)
    backfill_missing_prj_sidecars(matcher, args)
    matches = matcher.compute_matches(years)

    out_path = Path(args.data_dir) / "results" / "matches.csv"
    print(f"\nDone. {len(matches)} rows written to {out_path}")


if __name__ == "__main__":
    main()

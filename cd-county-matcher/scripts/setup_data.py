#!/usr/bin/env python3
"""
One-time data setup: fetches the large shapefiles that can't live in the git
repo from their public sources into ./data/manual_sources/.

Run this once after cloning:

    python scripts/setup_data.py

Files fetched:
  - TIGER 2010 US counties (~75 MB)       -> used for 2000-2010
  - CB 2023 CD118 cartographic (~6 MB)    -> used for 2023-2024
  - CB 2024 CD119 cartographic (~6 MB)    -> used for 2025
  - Newberry Atlas historical counties    -> used for 1984-1999

If any download fails (URLs occasionally move), the script prints the
fallback instructions for that file and continues with the others.
"""

import argparse
import sys
import zipfile
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Import the canonical URL table from the package.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from src.matcher import MANUAL_SOURCE_URLS  # noqa: E402


def download_with_progress(url: str, dest: Path, chunk_size: int = 1 << 15) -> bool:
    """Stream a file to disk with a simple progress print."""
    try:
        with requests.get(url, stream=True, timeout=600, verify=False) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded * 100 // total
                        print(
                            f"\r  {dest.name}: {pct}% "
                            f"({downloaded/1e6:.1f}/{total/1e6:.1f} MB)",
                            end="",
                            flush=True,
                        )
            print()
        return True
    except Exception as e:
        print(f"\n  [error] {url} -> {e}")
        if dest.exists():
            dest.unlink()
        return False


def fetch_source(key: str, spec: dict, manual_dir: Path, force: bool) -> bool:
    """Fetch and extract one manual source. Returns True on success."""
    extract_dir = manual_dir / spec["extract_dir"]
    shp_path = extract_dir / spec["shapefile_name"]

    if shp_path.exists() and not force:
        print(f"[skip] {key}: already present at {shp_path}")
        return True

    extract_dir.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] {key}: {spec['description']}")
    print(f"        URL: {spec['url']}")

    zip_path = extract_dir / Path(spec["url"]).name
    if not download_with_progress(spec["url"], zip_path):
        if "fallback_note" in spec:
            print(f"        {spec['fallback_note']}")
        return False

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        zip_path.unlink()
    except zipfile.BadZipFile:
        print(f"  [error] {zip_path} is not a valid zip file")
        if "fallback_note" in spec:
            print(f"        {spec['fallback_note']}")
        return False

    # Some archives put the shapefile in a subdirectory. Flatten if needed.
    if not shp_path.exists():
        candidates = list(extract_dir.rglob(spec["shapefile_name"]))
        if candidates:
            found = candidates[0]
            for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg", ".xml"]:
                companion = found.with_suffix(ext)
                if companion.exists():
                    companion.rename(extract_dir / companion.name)

    if shp_path.exists():
        print(f"  [ok] extracted to {shp_path}")
        return True
    else:
        print(f"  [warn] expected {shp_path} after extraction, not found")
        if "fallback_note" in spec:
            print(f"        {spec['fallback_note']}")
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default="./data",
        help="Root data directory (default: ./data)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if files already exist",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=list(MANUAL_SOURCE_URLS.keys()),
        help="Only fetch these keys (default: all)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    manual_dir = data_dir / "manual_sources"
    manual_dir.mkdir(parents=True, exist_ok=True)
    print(f"Manual sources directory: {manual_dir}\n")

    keys = args.only or list(MANUAL_SOURCE_URLS.keys())
    results = {}
    for key in keys:
        spec = MANUAL_SOURCE_URLS[key]
        results[key] = fetch_source(key, spec, manual_dir, args.force)
        print()

    print("=" * 60)
    print("Setup summary:")
    for key, ok in results.items():
        print(f"  {'OK ' if ok else 'FAIL'}  {key}")
    if not all(results.values()):
        print(
            "\nSome sources failed. The matcher will still run but may fall "
            "back to other sources (UCLA, TIGER, NHGIS) for the affected "
            "years. See the fallback notes above for manual download options."
        )
        sys.exit(1)
    print("\nAll manual sources ready. You can now run:")
    print("    python scripts/run_matcher.py")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
One-time data setup: fetches the large shapefiles that can't live in the git
repo into ./data/manual_sources/.

Run this once after cloning:

    python scripts/setup_data.py

Files fetched:
  - TIGER 2010 US counties (~75 MB)       -> used for 2000-2010
  - CB 2023 CD118 cartographic (~6 MB)    -> used for 2023-2024
  - CB 2024 CD119 cartographic (~6 MB)    -> used for 2025
  - Newberry Atlas historical counties    -> used for 1984-1999

Where the files come from
-------------------------
The PRIMARY source for each file is our project's OSF Storage, which is fast
and has stable URLs. This replaces the old behaviour of crawling the original
public servers (Census, Newberry), which were slow and whose URLs move around.

For each file the script tries, in order:
  1. OSF  -- https://osf.io/<guid>/download, if a GUID is configured.
  2. The original canonical public URL (the "fallback").

Configure the OSF GUIDs once, after uploading the zips to OSF, by editing
osf_sources.json at the repo root (see README "Hosting the large files on
OSF"). Until then the script transparently falls back to the public URLs, so
it keeps working with no configuration.

If every source for a file fails, the script prints that file's fallback note
and continues with the others.
"""

import argparse
import os
import sys
import zipfile
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Import the canonical URL table + OSF helpers from the package.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from src.matcher import (  # noqa: E402
    MANUAL_SOURCE_URLS,
    load_osf_guids,
    load_osf_project_guid,
    discover_osf_files,
    osf_download_url,
)


def _register_annual_county_sources() -> None:
    """Add annual TIGER county raw-file sources hosted in OSF.

    The matcher can use the 2010 county file for many pre-2011 years, but
    2011/2012 and later annual county files are uploaded to the OSF project as
    raw shapefile components. They are not always present in the package's
    original MANUAL_SOURCE_URLS table, so setup_data.py needs to register them
    before argparse builds the --only choices and before the fetch loop runs.
    """
    for year in range(2011, 2024):
        key = f"county_{year}"
        MANUAL_SOURCE_URLS.setdefault(
            key,
            {
                "description": f"TIGER {year} US counties",
                "extract_dir": key,
                "shapefile_name": f"tl_{year}_us_county.shp",
                "fallback_note": (
                    f"If OSF is unavailable, manually download the TIGER "
                    f"{year} county shapefile and place tl_{year}_us_county.* "
                    f"in data/manual_sources/{key}/."
                ),
            },
        )


_register_annual_county_sources()


def download_with_progress(
    url: str, dest: Path, chunk_size: int = 1 << 15, token: str = None
) -> bool:
    """Stream a file to disk with a simple progress print.

    If ``token`` is set and the URL is an OSF host (osf.io / files.osf.io),
    an ``Authorization: Bearer`` header is attached so PRIVATE projects work.
    The header is only sent to OSF hosts, never to the public fallback
    servers. (requests drops it automatically on any cross-host redirect to a
    signed storage URL, which is the desired behaviour.)
    """
    headers = {}
    if token and "osf.io" in url:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with requests.get(
            url, stream=True, timeout=(10, 600), verify=False, headers=headers
        ) as r:
            if r.status_code == 404:
                print(f"  [404] {url} (not found)")
                return False
            if r.status_code in (401, 403):
                print(f"  [{r.status_code}] {url} — auth required. For a "
                      f"private project set OSF_TOKEN (or pass --osf-token).")
                return False
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


def _zip_candidates(key: str, spec: dict, osf_guids: dict, osf_files: dict) -> list:
    """Ordered (label, url) list for ZIP-style sources.

    Priority: per-file OSF GUID -> a zip matched by name in the project's
    auto-discovered listing -> the canonical public URL.
    """
    candidates = []

    guid = osf_guids.get(key)
    if guid:
        candidates.append(("OSF (file GUID)", osf_download_url(guid)))

    if osf_files:
        wanted = spec.get("osf_filename") or Path(spec.get("url", "")).name
        match = None
        if wanted and wanted in osf_files:
            match = osf_files[wanted]
        elif wanted:
            for name, dl in osf_files.items():
                if name.lower() == wanted.lower():
                    match = dl
                    break
        if match:
            candidates.append(("OSF zip (auto-discovered)", match))

    if spec.get("url"):
        candidates.append(("public fallback", spec["url"]))
    return candidates


def _raw_component_urls(spec: dict, osf_files: dict) -> dict:
    """Find raw shapefile components in the project's OSF listing.

    Matches files sharing the shapefile's basename with extensions
    .shp/.shx/.dbf/.prj/.cpg (case-insensitive). Returns {filename: url}.
    Requires at least .shp + .shx + .dbf to be useful.
    """
    if not osf_files:
        return {}
    stem = Path(spec["shapefile_name"]).stem
    exts = [".shp", ".shx", ".dbf", ".prj", ".cpg"]
    wanted = {f"{stem}{e}".lower(): f"{stem}{e}" for e in exts}
    found = {}
    for name, dl in osf_files.items():
        low = name.lower()
        if low in wanted:
            found[wanted[low]] = dl
    have = {Path(n).suffix.lower() for n in found}
    if {".shp", ".shx", ".dbf"}.issubset(have):
        return found
    return {}


def _flatten_shapefile(extract_dir: Path, shapefile_name: str) -> None:
    """If the shapefile landed in a subdirectory, move the set to the top."""
    shp_path = extract_dir / shapefile_name
    if shp_path.exists():
        return
    matches = list(extract_dir.rglob(shapefile_name))
    if matches:
        found = matches[0]
        for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg", ".xml"]:
            companion = found.with_suffix(ext)
            if companion.exists():
                companion.rename(extract_dir / companion.name)


def fetch_source(
    key: str, spec: dict, manual_dir: Path, osf_guids: dict, osf_files: dict,
    force: bool, token: str = None,
) -> bool:
    """Fetch one manual source into manual_dir. Returns True on success.

    Supports two OSF upload styles plus the public fallback:
      * raw files  — the .shp/.shx/.dbf/.prj uploaded individually (no zip);
                     downloaded straight into the source folder.
      * zip        — a single .zip per source; downloaded, unzipped, flattened.
    """
    extract_dir = manual_dir / spec["extract_dir"]
    shp_path = extract_dir / spec["shapefile_name"]

    if shp_path.exists() and not force:
        print(f"[skip] {key}: already present at {shp_path}")
        return True

    extract_dir.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] {key}: {spec['description']}")

    # Path 1: raw shapefile components discovered in the OSF project.
    components = _raw_component_urls(spec, osf_files)
    if components:
        print(f"        OSF raw files (auto-discovered): "
              f"{', '.join(sorted(components))}")
        ok_all = True
        for fname, url in sorted(components.items()):
            if not download_with_progress(url, extract_dir / fname, token=token):
                ok_all = False
                print(f"        failed to download component {fname}")
        if ok_all and shp_path.exists():
            print(f"  [ok] downloaded raw shapefile set to {extract_dir}")
            return True
        print("        raw-file download incomplete, trying zip/public...")

    # Path 2: zip-style sources (per-file GUID, discovered zip, public URL).
    candidates = _zip_candidates(key, spec, osf_guids, osf_files)
    if not candidates:
        print("        No OSF source configured and no fallback URL available.")
        if "fallback_note" in spec:
            print(f"        {spec['fallback_note']}")
        return False

    zip_path = extract_dir / f"{key}.zip"
    downloaded = False
    for label, url in candidates:
        print(f"        trying {label}: {url}")
        if download_with_progress(url, zip_path, token=token):
            downloaded = True
            break
        print(f"        {label} failed, trying next source...")

    if not downloaded:
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

    _flatten_shapefile(extract_dir, spec["shapefile_name"])

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
    parser.add_argument(
        "--osf-config",
        default=None,
        help="Path to a JSON file of {source_key: osf_guid}. Defaults to "
        "osf_sources.json at the repo root (or the OSF_SOURCES_JSON env var).",
    )
    parser.add_argument(
        "--osf-project",
        default=None,
        help="OSF project GUID (or full osf.io URL) to auto-discover files "
        "from via the OSF API. Overrides _osf_project in osf_sources.json / "
        "the OSF_PROJECT_GUID env var.",
    )
    parser.add_argument(
        "--osf-token",
        default=os.environ.get("OSF_TOKEN"),
        help="OSF Personal Access Token for PRIVATE projects (or set the "
        "OSF_TOKEN env var). Not needed for public projects.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    manual_dir = data_dir / "manual_sources"
    manual_dir.mkdir(parents=True, exist_ok=True)
    print(f"Manual sources directory: {manual_dir}\n")

    osf_guids = load_osf_guids(args.osf_config)

    # Resolve the project GUID and, if we have one, ask the OSF API what files
    # live in its storage so we can match them by name (no per-file GUIDs).
    project_guid = args.osf_project or load_osf_project_guid(args.osf_config)
    osf_files = {}
    if project_guid:
        from src.matcher import _extract_guid

        project_guid = _extract_guid(project_guid)
        print(f"Discovering files in OSF project {project_guid} ...")
        osf_files = discover_osf_files(project_guid, token=args.osf_token)
        if osf_files:
            print(f"  found {len(osf_files)} file(s): "
                  + ", ".join(sorted(osf_files)) + "\n")
        else:
            print("  no files discovered (check the GUID, or set --osf-token "
                  "for a private project) — will use other sources.\n")

    if osf_guids:
        print("Per-file OSF GUIDs configured for: "
              + ", ".join(sorted(osf_guids)) + "\n")
    if not project_guid and not osf_guids:
        print(
            "No OSF project GUID or per-file GUIDs configured yet — using "
            "public fallback URLs. To fetch from OSF (recommended, much "
            "faster), put your project GUID in osf_sources.json. See README.\n"
        )

    keys = args.only or list(MANUAL_SOURCE_URLS.keys())
    results = {}
    for key in keys:
        spec = MANUAL_SOURCE_URLS[key]
        results[key] = fetch_source(
            key, spec, manual_dir, osf_guids, osf_files, args.force,
            token=args.osf_token,
        )
        print()

    print("=" * 60)
    print("Setup summary:")
    for key, ok in results.items():
        spec = MANUAL_SOURCE_URLS[key]
        if osf_guids.get(key):
            src = "OSF(guid)"
        elif osf_files and _raw_component_urls(spec, osf_files):
            src = "OSF(raw files)"
        elif osf_files and (
            (spec.get("osf_filename") or Path(spec.get("url", "")).name)
            in osf_files
        ):
            src = "OSF(zip)"
        else:
            src = "public"
        print(f"  {'OK ' if ok else 'FAIL'}  {key}  (primary: {src})")
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

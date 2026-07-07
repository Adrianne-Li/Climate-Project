"""
Congressional District to County Matcher.

Computes area-based overlaps between US Congressional Districts and counties
for years 1984-2025. The data path is OSF-first: the matcher discovers raw
shapefile components or zips in the public OSF project, downloads them, then
uses public sources only as fallbacks.
"""

import json
import os
import shutil
import time
import warnings
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import geopandas as gpd
import pandas as pd
import requests
import urllib3

warnings.filterwarnings("ignore")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

OSF_DOWNLOAD_BASE = "https://osf.io"
OSF_API_BASE = "https://api.osf.io/v2"
DEFAULT_SOURCE_CRS = os.environ.get("MATCHER_SOURCE_CRS", "EPSG:4269")


def osf_download_url(guid: str) -> str:
    guid = _extract_guid(guid)
    return f"{OSF_DOWNLOAD_BASE}/{guid}/download"


def _extract_guid(value: str) -> str:
    value = str(value or "").strip().strip("/")
    if "osf.io/" in value:
        value = value.split("osf.io/")[-1].strip("/").split("/")[0]
    return value


def discover_osf_files(
    project_guid: str,
    token: Optional[str] = None,
    session: Optional[requests.Session] = None,
    recurse: bool = True,
) -> Dict[str, str]:
    """Return a map from OSF filename/path to direct download URL."""
    project_guid = _extract_guid(project_guid)
    if not project_guid:
        return {}

    sess = session or requests.Session()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    wb_base = (
        f"https://files.osf.io/v1/resources/{project_guid}/providers/"
        "osfstorage"
    )
    root_url = f"{OSF_API_BASE}/nodes/{project_guid}/files/osfstorage/"
    file_map: Dict[str, str] = {}
    seen = set()

    def walk(listing_url: str, depth: int = 0) -> None:
        url = listing_url
        while url:
            if url in seen:
                return
            seen.add(url)
            try:
                resp = sess.get(url, headers=headers, timeout=30)
            except Exception as exc:
                print(f"  [osf] discovery error: {exc}")
                return
            if resp.status_code != 200:
                if depth == 0:
                    print(f"  [osf] listing returned HTTP {resp.status_code}")
                return
            payload = resp.json()
            for item in payload.get("data", []):
                attrs = item.get("attributes", {})
                name = attrs.get("name")
                kind = attrs.get("kind")
                path = attrs.get("path", "")
                links = item.get("links", {})
                if kind == "file" and name:
                    if path:
                        dl_url = f"{wb_base}{path}"
                    else:
                        dl_url = links.get("download")
                    if dl_url:
                        file_map[name] = dl_url
                        if path:
                            file_map[path.strip("/")] = dl_url
                elif kind == "folder" and recurse:
                    sub = (
                        item.get("relationships", {})
                        .get("files", {})
                        .get("links", {})
                        .get("related", {})
                        .get("href")
                    )
                    if sub:
                        walk(sub, depth + 1)
            url = payload.get("links", {}).get("next")

    walk(root_url)
    return file_map


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_osf_project_guid(config_path: Optional[str] = None) -> Optional[str]:
    env = os.environ.get("OSF_PROJECT_GUID")
    if env:
        return _extract_guid(env)

    candidates = []
    if config_path:
        candidates.append(Path(config_path))
    if os.environ.get("OSF_SOURCES_JSON"):
        candidates.append(Path(os.environ["OSF_SOURCES_JSON"]))
    candidates.append(_repo_root() / "osf_sources.json")

    for path in candidates:
        try:
            if path.exists():
                data = json.loads(path.read_text())
                for key in ("_osf_project_guid", "_osf_project"):
                    value = data.get(key)
                    if value:
                        return _extract_guid(value)
        except Exception:
            pass
    return None


MANUAL_SOURCE_URLS = {
    "county_2010": {
        "osf_guid": "",
        "osf_filename": "tl_2010_us_county10.zip",
        "url": (
            "https://www2.census.gov/geo/tiger/TIGER2010/COUNTY/2010/"
            "tl_2010_us_county10.zip"
        ),
        "extract_dir": "county_2010",
        "shapefile_name": "tl_2010_us_county10.shp",
        "description": "TIGER/Line 2010 US counties",
    },
    "cd_118th_2023": {
        "osf_guid": "",
        "osf_filename": "cb_2023_us_cd118_5m.zip",
        "url": (
            "https://www2.census.gov/geo/tiger/GENZ2023/shp/"
            "cb_2023_us_cd118_5m.zip"
        ),
        "extract_dir": "cd_118th_2023",
        "shapefile_name": "cb_2023_us_cd118_5m.shp",
        "description": "118th Congress district boundaries",
    },
    "cd_119th_2025": {
        "osf_guid": "",
        "osf_filename": "cb_2024_us_cd119_5m.zip",
        "url": (
            "https://www2.census.gov/geo/tiger/GENZ2024/shp/"
            "cb_2024_us_cd119_5m.zip"
        ),
        "extract_dir": "cd_119th_2025",
        "shapefile_name": "cb_2024_us_cd119_5m.shp",
        "description": "119th Congress district boundaries",
    },
    "newberry_historical": {
        "osf_guid": "",
        "osf_filename": "US_AtlasHCB_Counties.zip",
        "url": (
            "https://publications.newberry.org/ahcb/downloads/gis/"
            "US_AtlasHCB_Counties.zip"
        ),
        "extract_dir": "newberry_historical",
        "shapefile_name": "US_HistCounties.shp",
        "description": "Newberry Atlas of Historical County Boundaries",
    },
}


def _register_annual_county_sources() -> None:
    for year in range(2011, 2024):
        key = f"county_{year}"
        MANUAL_SOURCE_URLS.setdefault(
            key,
            {
                "osf_guid": "",
                "osf_filename": f"tl_{year}_us_county.zip",
                "url": "",
                "extract_dir": key,
                "shapefile_name": f"tl_{year}_us_county.shp",
                "description": f"TIGER {year} US counties",
            },
        )


_register_annual_county_sources()


def load_osf_guids(config_path: Optional[str] = None) -> Dict[str, str]:
    guids = {
        key: spec["osf_guid"]
        for key, spec in MANUAL_SOURCE_URLS.items()
        if spec.get("osf_guid")
    }
    candidates = [_repo_root() / "osf_sources.json"]
    if config_path:
        candidates.append(Path(config_path))
    if os.environ.get("OSF_SOURCES_JSON"):
        candidates.append(Path(os.environ["OSF_SOURCES_JSON"]))
    for path in candidates:
        try:
            if path.exists():
                data = json.loads(path.read_text())
                for key, value in data.items():
                    if not key.startswith("_") and str(value).strip():
                        guids[key] = str(value).strip()
        except Exception as exc:
            print(f"  [warn] could not read OSF config {path}: {exc}")
    return guids


class CDCountyMatcher:
    def __init__(
        self,
        data_dir: str = "./data",
        nhgis_api_key: Optional[str] = None,
        manual_sources_dir: Optional[str] = None,
        osf_project: Optional[str] = None,
        osf_token: Optional[str] = None,
        osf_config: Optional[str] = None,
    ):
        self.base_data_dir = Path(data_dir)
        self.data_dir = self.base_data_dir
        self.base_data_dir.mkdir(parents=True, exist_ok=True)
        for subdir in [
            "osf_storage",
            "ucla_github",
            "tiger",
            "census_cartographic",
            "newberry_historical",
            "manual_nhgis",
            "manual_cd",
            "nhgis_api",
            "results",
            "temp",
        ]:
            (self.base_data_dir / subdir).mkdir(exist_ok=True)

        self.manual_sources_dir = (
            Path(manual_sources_dir)
            if manual_sources_dir
            else self.base_data_dir / "manual_sources"
        )
        self.manual_sources_dir.mkdir(parents=True, exist_ok=True)

        self.nhgis_api_key = nhgis_api_key
        self.nhgis_api_base = "https://api.ipums.org"
        self.metadata_endpoint = f"{self.nhgis_api_base}/metadata"
        self.extracts_endpoint = f"{self.nhgis_api_base}/extracts"
        self.ucla_shapefile_base = "https://cdmaps.polisci.ucla.edu/shp/"
        self.census_cartographic_base = "https://www2.census.gov/geo/tiger/"

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; cd-county-matcher/1.0)"
                )
            }
        )

        self.osf_token = osf_token or os.environ.get("OSF_TOKEN")
        self.osf_project_guid = _extract_guid(
            osf_project or load_osf_project_guid(osf_config) or ""
        )
        self.osf_files: Dict[str, str] = {}
        if self.osf_project_guid:
            print(f"Discovering OSF Storage files for project {self.osf_project_guid}...")
            self.osf_files = discover_osf_files(
                self.osf_project_guid,
                token=self.osf_token,
                session=self.session,
            )
            print(f"  OSF discovery found {len(self.osf_files)} file(s).")

        self.congress_mappings = self._setup_congress_mappings()
        self.manual_county_map: Dict[int, str] = {}
        self.manual_cd_map: Dict[int, str] = {}
        self.local_newberry_path: Optional[str] = None
        self._resolve_manual_paths()
        self.newberry_loaded_gdf = None

        print(
            f"Initialized CDCountyMatcher. data_dir={self.base_data_dir}, "
            f"manual_sources_dir={self.manual_sources_dir}, "
            f"OSF={'enabled' if self.osf_files else 'not discovered'}, "
            f"NHGIS={'enabled' if self.nhgis_api_key else 'disabled'}."
        )

    def _setup_congress_mappings(self) -> Dict[int, str]:
        return {year: str((year - 1789) // 2 + 1) for year in range(1984, 2026)}

    def _resolve_manual_paths(self) -> None:
        for year in range(2000, 2011):
            p = self.manual_sources_dir / "county_2010" / "tl_2010_us_county10.shp"
            if p.exists():
                self.manual_county_map[year] = str(p)
        for year in range(2011, 2024):
            p = self.manual_sources_dir / f"county_{year}" / f"tl_{year}_us_county.shp"
            if p.exists():
                self.manual_county_map[year] = str(p)
        p118 = self.manual_sources_dir / "cd_118th_2023" / "cb_2023_us_cd118_5m.shp"
        if p118.exists():
            self.manual_cd_map[2023] = str(p118)
            self.manual_cd_map[2024] = str(p118)
        p119 = self.manual_sources_dir / "cd_119th_2025" / "cb_2024_us_cd119_5m.shp"
        if p119.exists():
            self.manual_cd_map[2025] = str(p119)
        newberry = self.manual_sources_dir / "newberry_historical" / "US_HistCounties.shp"
        self.local_newberry_path = str(newberry) if newberry.exists() else None

    def _get_strategy(self, year: int) -> Dict[str, List[str]]:
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

    def _get_manual_county_path(self, year: int) -> Optional[str]:
        return self.manual_county_map.get(year)

    def _find_osf_file(self, filename: str) -> Optional[str]:
        if filename in self.osf_files:
            return self.osf_files[filename]
        wanted = filename.lower()
        for name, url in self.osf_files.items():
            if name.lower() == wanted:
                return url
        return None

    def _osf_auth_headers(self, url: str) -> Optional[dict]:
        if self.osf_token and "osf.io" in url:
            return {"Authorization": f"Bearer {self.osf_token}"}
        return None

    def _download_file(
        self, url: str, filepath: Path, headers: dict = None, unzip: bool = False
    ) -> bool:
        filepath.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                response = self.session.get(
                    url, timeout=(10, 300), verify=False, stream=True, headers=headers
                )
                if response.status_code == 404:
                    return False
                if response.status_code == 429:
                    time.sleep(2**attempt)
                    continue
                response.raise_for_status()
                with open(filepath, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                if unzip:
                    with zipfile.ZipFile(filepath, "r") as zf:
                        zf.extractall(filepath.parent)
                return True
            except Exception as exc:
                print(f"Download failed for {url}: {str(exc)[:120]}")
                if filepath.exists():
                    filepath.unlink()
        return False

    def _download_and_extract_with_retry(
        self, url: str, filepath: Path, max_retries: int = 3, headers: dict = None
    ) -> bool:
        return self._download_file(url, filepath, headers=headers, unzip=True)

    def _download_osf_raw_components(self, stem: str, target_dir: Path) -> bool:
        exts = [".shp", ".shx", ".dbf", ".prj", ".cpg"]
        found = {}
        for ext in exts:
            fname = f"{stem}{ext}"
            url = self._find_osf_file(fname)
            if url:
                found[fname] = url
        have = {Path(name).suffix.lower() for name in found}
        if not {".shp", ".shx", ".dbf"}.issubset(have):
            return False
        ok = True
        for fname, url in sorted(found.items()):
            ok = self._download_file(
                url,
                target_dir / fname,
                headers=self._osf_auth_headers(url),
                unzip=False,
            ) and ok
        return ok and (target_dir / f"{stem}.shp").exists()

    def _download_osf_shapefile(
        self,
        zip_candidates: List[str],
        target_dir: Path,
        raw_stem_candidates: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        if not self.osf_files:
            return False, "No OSF files discovered"
        for filename in zip_candidates:
            url = self._find_osf_file(filename)
            if url:
                if self._download_and_extract_with_retry(
                    url, target_dir / filename, headers=self._osf_auth_headers(url)
                ):
                    return True, f"Downloaded OSF zip {filename}"
        stems = raw_stem_candidates or [Path(x).stem for x in zip_candidates]
        for stem in stems:
            if self._download_osf_raw_components(stem, target_dir):
                return True, f"Downloaded OSF raw shapefile components for {stem}"
        return False, "OSF file not found or download failed"

    def download_data(self, years: List[int]) -> Dict[int, Dict]:
        results = {}
        if any(y < 2000 for y in years):
            self.newberry_loaded_gdf = self._load_newberry_full()
        for year in sorted(years):
            strategy = self._get_strategy(year)
            year_result = {
                "cd_success": False,
                "county_success": False,
                "cd_source": None,
                "county_source": None,
            }
            for source in strategy["cd_sources"]:
                print(f"Trying CD source for {year}: {source}")
                ok, info = self._download_cd(year, source)
                print(f" {'SUCCESS' if ok else 'FAILED'}: {info}")
                if ok:
                    year_result["cd_success"] = True
                    year_result["cd_source"] = source
                    break
            for source in strategy["county_sources"]:
                print(f"Trying County source for {year}: {source}")
                ok, info = self._download_county(year, source)
                print(f" {'SUCCESS' if ok else 'FAILED'}: {info}")
                if ok:
                    year_result["county_success"] = True
                    year_result["county_source"] = source
                    break
            print(
                f"{year} SUMMARY: "
                f"CD={'SUCCESS' if year_result['cd_success'] else 'FAILED'}, "
                f"County={'SUCCESS' if year_result['county_success'] else 'FAILED'}"
            )
            results[year] = year_result
        return results

    def _copy_shapefile_bundle(self, src_shp: Path, target_dir: Path) -> bool:
        target_dir.mkdir(parents=True, exist_ok=True)
        for src in src_shp.parent.glob(src_shp.stem + ".*"):
            if src.is_file():
                shutil.copy2(src, target_dir / src.name)
        return (target_dir / src_shp.name).exists()

    def _download_cd(self, year: int, source: str) -> Tuple[bool, str]:
        target_dir = self.base_data_dir / source / str(year) / "cd"
        target_dir.mkdir(parents=True, exist_ok=True)
        if source == "osf_storage":
            congress_num = int(self.congress_mappings[year])
            pattern = f"districts{congress_num:03d}.zip"
            return self._download_osf_shapefile([pattern], target_dir, [Path(pattern).stem])
        if source == "ucla_github":
            congress_num = int(self.congress_mappings[year])
            pattern = f"districts{congress_num:03d}.zip"
            url = f"{self.ucla_shapefile_base}{pattern}"
            if self._download_and_extract_with_retry(url, target_dir / pattern):
                return True, f"Downloaded UCLA: {pattern}"
            return False, "UCLA failed"
        if source == "manual_cd":
            manual_path = self.manual_cd_map.get(year)
            if manual_path and self._copy_shapefile_bundle(Path(manual_path), target_dir):
                return True, f"Copied manual CD {year}"
            return False, "No manual CD path"
        if source == "nhgis_api":
            return False, "NHGIS CD fallback not enabled in this compact build"
        return False, "Unknown CD source"

    def _download_county(self, year: int, source: str) -> Tuple[bool, str]:
        target_dir = self.base_data_dir / source / str(year) / "county"
        target_dir.mkdir(parents=True, exist_ok=True)
        if source == "manual_nhgis":
            manual_path = self._get_manual_county_path(year)
            if manual_path and self._copy_shapefile_bundle(Path(manual_path), target_dir):
                return True, f"Copied manual county {year}"
            return False, "No manual path for year"
        if source == "osf_storage":
            if year < 2000:
                return self._download_county(year, "newberry_historical")
            zip_candidates = [f"tl_{year}_us_county.zip"]
            raw_stems = [f"tl_{year}_us_county"]
            if 2000 <= year <= 2010:
                zip_candidates.append("tl_2010_us_county10.zip")
                raw_stems.append("tl_2010_us_county10")
            zip_candidates.extend(
                [
                    f"cb_{year}_us_county_500k.zip",
                    f"cb_{year}_us_county_20m.zip",
                    f"cb_{year}_us_county_5m.zip",
                ]
            )
            raw_stems.extend(
                [
                    f"cb_{year}_us_county_500k",
                    f"cb_{year}_us_county_20m",
                    f"cb_{year}_us_county_5m",
                ]
            )
            return self._download_osf_shapefile(zip_candidates, target_dir, raw_stems)
        if source == "newberry_historical":
            if self.newberry_loaded_gdf is None:
                self.newberry_loaded_gdf = self._load_newberry_full()
            if self.newberry_loaded_gdf is None:
                return False, "Newberry load failed"
            output_file = target_dir / f"counties_{year}_newberry.shp"
            if output_file.exists():
                return True, "Already exists"
            year_start = datetime(year, 1, 1)
            year_end = datetime(year, 12, 31)
            filtered = self.newberry_loaded_gdf[
                (self.newberry_loaded_gdf["START_DATE"] <= year_end)
                & (self.newberry_loaded_gdf["END_DATE"] >= year_start)
            ]
            if len(filtered) == 0:
                return False, "No counties for year"
            filtered.to_file(output_file)
            return True, f"Filtered {len(filtered)} historical counties"
        if source == "tiger":
            filename = f"tl_{year}_us_county.zip"
            urls = [
                f"{self.census_cartographic_base}TIGER{year}/COUNTY/{filename}",
                f"{self.census_cartographic_base}TIGER{year}/county/{filename}",
            ]
            for url in urls:
                if self._download_and_extract_with_retry(url, target_dir / filename):
                    return True, "Downloaded TIGER county"
            return False, "TIGER county failed"
        if source == "census_cartographic":
            for pattern in [
                f"cb_{year}_us_county_500k.zip",
                f"cb_{year}_us_county_20m.zip",
                f"cb_{year}_us_county_5m.zip",
            ]:
                url = f"{self.census_cartographic_base}GENZ{year}/shp/{pattern}"
                if self._download_and_extract_with_retry(url, target_dir / pattern):
                    return True, f"Downloaded cartographic county {pattern}"
            return False, "Cartographic failed"
        if source == "nhgis_api":
            return False, "NHGIS county fallback not enabled in this compact build"
        return False, "Unknown county source"

    def _load_newberry_full(self) -> Optional[gpd.GeoDataFrame]:
        if self.local_newberry_path and Path(self.local_newberry_path).exists():
            path = Path(self.local_newberry_path)
        else:
            target_dir = self.base_data_dir / "newberry_historical" / "full"
            target_dir.mkdir(parents=True, exist_ok=True)
            ok, _ = self._download_osf_shapefile(
                ["US_AtlasHCB_Counties.zip"], target_dir, ["US_HistCounties"]
            )
            shp_files = list(target_dir.rglob("US_HistCounties.shp"))
            if not ok or not shp_files:
                return None
            path = shp_files[0]
        gdf = gpd.read_file(path)
        gdf["START_DATE"] = pd.to_datetime(gdf["START_DATE"], errors="coerce")
        gdf["END_DATE"] = pd.to_datetime(gdf["END_DATE"], errors="coerce")
        gdf["END_DATE"] = gdf["END_DATE"].fillna(datetime(2010, 12, 31))
        print(f"Loaded Newberry with {len(gdf)} records")
        return gdf

    def _repair_missing_prj_sidecars(self) -> None:
        if not self.osf_files:
            return
        osf_by_lower = {name.lower(): url for name, url in self.osf_files.items()}
        for shp_path in self.base_data_dir.rglob("*.shp"):
            prj_path = shp_path.with_suffix(".prj")
            if prj_path.exists():
                continue
            url = osf_by_lower.get(f"{shp_path.stem}.prj".lower())
            if url:
                if self._download_file(url, prj_path, headers=self._osf_auth_headers(url)):
                    print(f"[repair] downloaded missing CRS sidecar: {prj_path}")

    def compute_matches(self, years: List[int]) -> pd.DataFrame:
        self._repair_missing_prj_sidecars()
        all_overlaps = []
        for year in years:
            cd_gdf, county_gdf = self._load_shapefiles(year)
            if cd_gdf is None or county_gdf is None:
                print(f"Skipping {year}: Missing data")
                continue
            overlaps = self._calculate_overlap(cd_gdf, county_gdf, year)
            if len(overlaps):
                all_overlaps.append(overlaps)
        if not all_overlaps:
            return pd.DataFrame()
        combined = pd.concat(all_overlaps, ignore_index=True)
        standard_cols = [
            "year",
            "data_source",
            "processing_date",
            "state_name",
            "cd_number",
            "cd_geoid",
            "cd_name",
            "county_name",
            "county_fips",
            "cd_area_km2",
            "county_area_km2",
            "intersection_area_km2",
            "pct_cd_in_county",
            "pct_county_in_cd",
        ]
        combined = combined.reindex(columns=standard_cols, fill_value=pd.NA)
        out_path = self.base_data_dir / "results" / "matches.csv"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_csv(out_path, index=False)
        print(f"Saved matches to {out_path}")
        return combined

    def _load_shapefiles(
        self, year: int
    ) -> Tuple[Optional[gpd.GeoDataFrame], Optional[gpd.GeoDataFrame]]:
        cd_paths = [
            self.base_data_dir / "osf_storage" / str(year) / "cd",
            self.base_data_dir / "manual_cd" / str(year) / "cd",
            self.base_data_dir / "tiger" / str(year) / "cd",
            self.base_data_dir / "ucla_github" / str(year) / "cd",
            self.base_data_dir / "nhgis_api" / str(year) / "cd",
        ]
        county_paths = [
            self.base_data_dir / "osf_storage" / str(year) / "county",
            self.base_data_dir / "manual_nhgis" / str(year) / "county",
            self.base_data_dir / "tiger" / str(year) / "county",
            self.base_data_dir / "census_cartographic" / str(year) / "county",
            self.base_data_dir / "newberry_historical" / str(year) / "county",
            self.base_data_dir / "nhgis_api" / str(year) / "county",
        ]
        return self._first_gdf(cd_paths, "CD", year), self._first_gdf(
            county_paths, "County", year
        )

    def _first_gdf(self, paths: List[Path], label: str, year: int):
        for path in paths:
            if not path.exists():
                continue
            shp_files = sorted(path.rglob("*.shp"))
            if shp_files:
                gdf = gpd.read_file(shp_files[0])
                print(f"Loaded {label} for {year} from {path.parent.name}")
                return gdf
        return None

    def _ensure_crs(self, gdf: gpd.GeoDataFrame, label: str, year: int):
        if gdf.crs is None:
            print(f"[repair] {label} for {year} has no CRS; assuming {DEFAULT_SOURCE_CRS}")
            return gdf.set_crs(DEFAULT_SOURCE_CRS, allow_override=True)
        return gdf

    def _calculate_overlap(
        self, cd_gdf: gpd.GeoDataFrame, county_gdf: gpd.GeoDataFrame, year: int
    ) -> pd.DataFrame:
        print(f"Calculating overlaps for {year}...")
        cd_gdf = self._ensure_crs(cd_gdf, "CD shapefile", year).to_crs("EPSG:3857")
        county_gdf = self._ensure_crs(county_gdf, "county shapefile", year).to_crs(
            "EPSG:3857"
        )
        cd_gdf = self._standardize_columns(cd_gdf, "cd")
        county_gdf = self._standardize_columns(county_gdf, "county")

        cd_cols = ["geometry", "state_fips", "cd_number", "cd_geoid", "cd_name"]
        county_cols = ["geometry", "state_fips", "county_fips", "county_name"]
        cd_gdf = cd_gdf[cd_gdf.columns.intersection(cd_cols)].copy()
        county_gdf = county_gdf[county_gdf.columns.intersection(county_cols)].copy()
        cd_gdf["cd_area_km2"] = cd_gdf.geometry.area / 1e6
        county_gdf["county_area_km2"] = county_gdf.geometry.area / 1e6

        intersection = gpd.overlay(cd_gdf, county_gdf, how="intersection")
        if len(intersection) == 0:
            print(" No intersections found")
            return pd.DataFrame()
        intersection["intersection_area_km2"] = intersection.geometry.area / 1e6
        intersection["pct_cd_in_county"] = (
            intersection["intersection_area_km2"] / intersection["cd_area_km2"] * 100
        )
        intersection["pct_county_in_cd"] = (
            intersection["intersection_area_km2"]
            / intersection["county_area_km2"]
            * 100
        )
        result = intersection[intersection["pct_cd_in_county"] >= 1.0].copy()
        result["year"] = year
        result["data_source"] = "cd_county_matcher"
        result["processing_date"] = datetime.now().strftime("%Y-%m-%d")
        if "state_fips" in result.columns:
            result["state_name"] = result["state_fips"].map(self._get_state_mapping())
        output_cols = [
            "year",
            "data_source",
            "processing_date",
            "state_name",
            "cd_number",
            "cd_geoid",
            "cd_name",
            "county_name",
            "county_fips",
            "cd_area_km2",
            "county_area_km2",
            "intersection_area_km2",
            "pct_cd_in_county",
            "pct_county_in_cd",
        ]
        for col in output_cols:
            if col not in result.columns:
                result[col] = pd.NA
        result = result[output_cols].drop(columns="geometry", errors="ignore")
        print(f" Found {len(result)} overlaps")
        return result

    def _standardize_columns(self, gdf: gpd.GeoDataFrame, data_type: str):
        gdf = gdf.copy()
        if data_type == "cd":
            mappings = {
                "GEOID": "cd_geoid",
                "GEOID20": "cd_geoid",
                "GISJOIN": "cd_geoid",
                "CD": "cd_number",
                "DIST": "cd_number",
                "DISTRICT": "cd_number",
                "CDSESSN": "cd_number",
                "CD112FP": "cd_number",
                "CD113FP": "cd_number",
                "CD114FP": "cd_number",
                "CD115FP": "cd_number",
                "CD116FP": "cd_number",
                "CD118": "cd_number",
                "NAME": "cd_name",
                "NAMELSAD": "cd_name",
                "STATEFP": "state_fips",
                "STATEFP20": "state_fips",
                "STATE": "state_fips",
                "STATE_FIPS": "state_fips",
            }
            for old, new in mappings.items():
                if old in gdf.columns and new not in gdf.columns:
                    gdf = gdf.rename(columns={old: new})
            if "state_fips" not in gdf.columns and "cd_geoid" in gdf.columns:
                gdf["state_fips"] = gdf["cd_geoid"].astype(str).str[:2]
            if "cd_number" not in gdf.columns and "cd_geoid" in gdf.columns:
                gdf["cd_number"] = gdf["cd_geoid"].astype(str).str[-2:]
            if "cd_number" not in gdf.columns:
                gdf["cd_number"] = (gdf.index + 1).astype(str)
            if "cd_geoid" not in gdf.columns:
                if "state_fips" in gdf.columns:
                    gdf["cd_geoid"] = (
                        gdf["state_fips"].astype(str)
                        + gdf["cd_number"].astype(str).str.zfill(2)
                    )
                else:
                    gdf["cd_geoid"] = pd.NA
            if "cd_name" not in gdf.columns:
                gdf["cd_name"] = "Congressional District " + gdf["cd_number"].astype(str)
        else:
            mappings = {
                "GEOID": "county_fips",
                "GEOID10": "county_fips",
                "GEOID20": "county_fips",
                "GISJOIN": "county_fips",
                "FIPS": "county_fips",
                "FIPS_CODE": "county_fips",
                "COUNTYFP": "county_fp",
                "COUNTYFP10": "county_fp",
                "NAME": "county_name",
                "NAME10": "county_name",
                "NAMELSAD": "county_name",
                "NAMELSAD10": "county_name",
                "COUNTY": "county_name",
                "FULL_NAME": "county_name",
                "FROMCOUNTY": "county_name",
                "STATEFP": "state_fips",
                "STATEFP10": "state_fips",
                "STATEFP20": "state_fips",
                "STATE": "state_fips",
                "STATE_FIPS": "state_fips",
            }
            for old, new in mappings.items():
                if old in gdf.columns and new not in gdf.columns:
                    gdf = gdf.rename(columns={old: new})
            if (
                "county_fips" not in gdf.columns
                and "state_fips" in gdf.columns
                and "county_fp" in gdf.columns
            ):
                gdf["county_fips"] = (
                    gdf["state_fips"].astype(str).str.zfill(2)
                    + gdf["county_fp"].astype(str).str.zfill(3)
                )
            if "state_fips" not in gdf.columns and "county_fips" in gdf.columns:
                gdf["state_fips"] = gdf["county_fips"].astype(str).str[:2]
        return gdf

    def _get_state_mapping(self) -> Dict[str, str]:
        return {
            "01": "Alabama",
            "02": "Alaska",
            "04": "Arizona",
            "05": "Arkansas",
            "06": "California",
            "08": "Colorado",
            "09": "Connecticut",
            "10": "Delaware",
            "11": "District of Columbia",
            "12": "Florida",
            "13": "Georgia",
            "15": "Hawaii",
            "16": "Idaho",
            "17": "Illinois",
            "18": "Indiana",
            "19": "Iowa",
            "20": "Kansas",
            "21": "Kentucky",
            "22": "Louisiana",
            "23": "Maine",
            "24": "Maryland",
            "25": "Massachusetts",
            "26": "Michigan",
            "27": "Minnesota",
            "28": "Mississippi",
            "29": "Missouri",
            "30": "Montana",
            "31": "Nebraska",
            "32": "Nevada",
            "33": "New Hampshire",
            "34": "New Jersey",
            "35": "New Mexico",
            "36": "New York",
            "37": "North Carolina",
            "38": "North Dakota",
            "39": "Ohio",
            "40": "Oklahoma",
            "41": "Oregon",
            "42": "Pennsylvania",
            "44": "Rhode Island",
            "45": "South Carolina",
            "46": "South Dakota",
            "47": "Tennessee",
            "48": "Texas",
            "49": "Utah",
            "50": "Vermont",
            "51": "Virginia",
            "53": "Washington",
            "54": "West Virginia",
            "55": "Wisconsin",
            "56": "Wyoming",
        }


UpdatedMatcher = CDCountyMatcher

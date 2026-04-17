"""
Congressional District to County Matcher.

Computes area-based overlaps between US Congressional Districts and counties
for years 1984-2025, pulling shapefiles from TIGER/Line, NHGIS, UCLA's
congressional district boundary project, Census cartographic files, and the
Newberry Atlas of Historical County Boundaries.

All data paths are configurable via the constructor or config file. Large
shapefiles that cannot live in the git repo are fetched on first run by
`scripts/setup_data.py`.
"""

import os
import shutil
import time
import warnings
import zipfile
from datetime import datetime
from hashlib import md5
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import geopandas as gpd
import pandas as pd
import requests
import urllib3

warnings.filterwarnings("ignore")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ---------------------------------------------------------------------------
# Default remote URLs for the "manual" shapefiles. These used to be hardcoded
# local paths on the original author's machine. `scripts/setup_data.py` will
# fetch these into DATA_DIR/manual_sources/ on first run.
# ---------------------------------------------------------------------------
MANUAL_SOURCE_URLS = {
    # NHGIS 2010 county boundaries. The Census Bureau redistributes the same
    # TIGER/Line 2010 county file, which is equivalent for our overlap purpose.
    "county_2010": {
        "url": "https://www2.census.gov/geo/tiger/TIGER2010/COUNTY/2010/tl_2010_us_county10.zip",
        "extract_dir": "county_2010",
        "shapefile_name": "tl_2010_us_county10.shp",
        "description": "TIGER/Line 2010 US counties (used for 2000-2010 backfill)",
    },
    # 2023 CD cartographic boundary (118th Congress) - small enough to live
    # on Census servers and reused for 2024.
    "cd_118th_2023": {
        "url": "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_cd118_5m.zip",
        "extract_dir": "cd_118th_2023",
        "shapefile_name": "cb_2023_us_cd118_5m.shp",
        "description": "118th Congress district boundaries (used for 2023-2024)",
    },
    # 2025 / 119th Congress districts: the NTAD bulk file is large and
    # occasionally changes URL. We default to the Census 119th CB file.
    "cd_119th_2025": {
        "url": "https://www2.census.gov/geo/tiger/GENZ2024/shp/cb_2024_us_cd119_5m.zip",
        "extract_dir": "cd_119th_2025",
        "shapefile_name": "cb_2024_us_cd119_5m.shp",
        "description": "119th Congress district boundaries (used for 2025)",
        "fallback_note": (
            "If this URL 404s, download the NTAD Congressional Districts "
            "shapefile from https://data.transportation.gov/ and unzip it to "
            "DATA_DIR/manual_sources/cd_119th_2025/"
        ),
    },
    # Newberry Atlas of Historical County Boundaries - full US file, ~200MB.
    "newberry_historical": {
        "url": "https://publications.newberry.org/ahcb/downloads/gis/US_AtlasHCB_Counties.zip",
        "extract_dir": "newberry_historical",
        "shapefile_name": "US_HistCounties.shp",
        "description": "Newberry Atlas of Historical County Boundaries (full US)",
        "fallback_note": (
            "If the download fails, visit "
            "https://publications.newberry.org/ahcb/pages/United_States.html "
            "and place US_HistCounties.shp (plus .shx/.dbf/.prj) in "
            "DATA_DIR/manual_sources/newberry_historical/"
        ),
    },
}


class CDCountyMatcher:
    """Match Congressional Districts to counties by area overlap.

    Parameters
    ----------
    data_dir : str or Path
        Root directory for all downloaded/derived data. Created if missing.
    nhgis_api_key : str, optional
        IPUMS NHGIS API Bearer token. If omitted, NHGIS sources are skipped.
    manual_sources_dir : str or Path, optional
        Where the "manual" shapefiles (2010 counties, 2023/2025 CDs, Newberry)
        live. Defaults to ``<data_dir>/manual_sources``. These are populated by
        ``scripts/setup_data.py``.
    """

    def __init__(
        self,
        data_dir: str = "./data",
        nhgis_api_key: Optional[str] = None,
        manual_sources_dir: Optional[str] = None,
    ):
        self.base_data_dir = Path(data_dir)
        self.base_data_dir.mkdir(parents=True, exist_ok=True)
        for subdir in [
            "tiger",
            "nhgis_api",
            "ucla_github",
            "census_cartographic",
            "newberry_historical",
            "results",
            "temp",
            "manual_nhgis",
            "manual_cd",
        ]:
            (self.base_data_dir / subdir).mkdir(exist_ok=True)

        # Manual source shapefiles live outside the per-year tree so that
        # `setup_data.py` only has to populate them once.
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
        self.ucla_github_base = (
            "https://raw.githubusercontent.com/JeffreyBLewis/"
            "congressional-district-boundaries/master/"
        )
        self.census_cartographic_base = "https://www2.census.gov/geo/tiger/"
        self.newberry_base = (
            "https://publications.newberry.org/ahcb/downloads/united_states"
        )

        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; cd-county-matcher/1.0; "
                    "+https://github.com/)"
                )
            }
        )

        self.congress_mappings = self._setup_congress_mappings()

        self.state_fips_to_abbr = {
            "01": "al", "02": "ak", "04": "az", "05": "ar", "06": "ca",
            "08": "co", "09": "ct", "10": "de", "11": "dc", "12": "fl",
            "13": "ga", "15": "hi", "16": "id", "17": "il", "18": "in",
            "19": "ia", "20": "ks", "21": "ky", "22": "la", "23": "me",
            "24": "md", "25": "ma", "26": "mi", "27": "mn", "28": "ms",
            "29": "mo", "30": "mt", "31": "ne", "32": "nv", "33": "nh",
            "34": "nj", "35": "nm", "36": "ny", "37": "nc", "38": "nd",
            "39": "oh", "40": "ok", "41": "or", "42": "pa", "44": "ri",
            "45": "sc", "46": "sd", "47": "tn", "48": "tx", "49": "ut",
            "50": "vt", "51": "va", "53": "wa", "54": "wv", "55": "wi",
            "56": "wy",
        }

        # Resolve the manual sources lazily at the point of use, so a missing
        # optional file doesn't crash startup.
        self._resolve_manual_paths()

        self.newberry_loaded_gdf = None

        print(
            f"Initialized CDCountyMatcher. data_dir={self.base_data_dir}, "
            f"manual_sources_dir={self.manual_sources_dir}, "
            f"NHGIS={'enabled' if self.nhgis_api_key else 'disabled'}."
        )

    # ------------------------------------------------------------------
    # Path resolution for manual sources
    # ------------------------------------------------------------------
    def _resolve_manual_paths(self) -> None:
        """Build the {year: path} maps for manual shapefiles that exist on disk.

        Missing files are simply omitted from the map — the downloader will
        fall through to other sources (TIGER, UCLA, NHGIS, etc.) for that year.
        """
        county_2010_path = (
            self.manual_sources_dir
            / MANUAL_SOURCE_URLS["county_2010"]["extract_dir"]
            / MANUAL_SOURCE_URLS["county_2010"]["shapefile_name"]
        )
        cd_118_path = (
            self.manual_sources_dir
            / MANUAL_SOURCE_URLS["cd_118th_2023"]["extract_dir"]
            / MANUAL_SOURCE_URLS["cd_118th_2023"]["shapefile_name"]
        )
        cd_119_path = (
            self.manual_sources_dir
            / MANUAL_SOURCE_URLS["cd_119th_2025"]["extract_dir"]
            / MANUAL_SOURCE_URLS["cd_119th_2025"]["shapefile_name"]
        )
        newberry_path = (
            self.manual_sources_dir
            / MANUAL_SOURCE_URLS["newberry_historical"]["extract_dir"]
            / MANUAL_SOURCE_URLS["newberry_historical"]["shapefile_name"]
        )

        self.manual_county_map: Dict = {}
        if county_2010_path.exists():
            # 2000-2010 all point at the 2010 file (county boundaries are
            # effectively static across that decade for our purposes).
            for yr in range(2000, 2011):
                self.manual_county_map[yr] = str(county_2010_path)
        else:
            print(
                f"  [info] {county_2010_path} not found — 2000-2010 will fall "
                f"back to TIGER/per-state downloads. Run scripts/setup_data.py "
                f"to fetch it."
            )

        self.manual_cd_map: Dict[int, str] = {}
        if cd_118_path.exists():
            self.manual_cd_map[2023] = str(cd_118_path)
            self.manual_cd_map[2024] = str(cd_118_path)
        if cd_119_path.exists():
            self.manual_cd_map[2025] = str(cd_119_path)

        self.local_newberry_path = (
            str(newberry_path) if newberry_path.exists() else None
        )

    # ------------------------------------------------------------------
    # The rest of the class below is functionally identical to the original
    # — only the hardcoded path references have been replaced with the
    # configurable maps above.
    # ------------------------------------------------------------------
    def _setup_congress_mappings(self) -> Dict[int, str]:
        mappings = {}
        for year in range(1984, 2026):
            congress_num = (year - 1789) // 2 + 1
            mappings[year] = str(congress_num)
        verified = {
            2007: "110", 2008: "110", 2009: "111", 2010: "111",
            2011: "112", 2012: "112", 2013: "113", 2014: "113",
            2015: "114", 2016: "114", 2017: "115", 2018: "115",
            2019: "116", 2020: "116", 2021: "117", 2022: "117",
            2023: "118", 2024: "118", 2025: "119",
        }
        mappings.update(verified)
        return mappings

    def _get_state_dir(self, fips: str, state_name: str) -> str:
        upper_name = state_name.upper().replace(" ", "_")
        return f"{fips}_{upper_name}"

    def download_data(self, years: List[int]) -> Dict[int, Dict]:
        results = {}
        if any(y <= 2010 for y in years):
            self.newberry_loaded_gdf = self._load_newberry_full()
        for year in sorted(years):
            year_result = {
                "cd_success": False,
                "county_success": False,
                "cd_source": None,
                "county_source": None,
            }
            strategy = self._get_strategy(year)
            for source in strategy["cd_sources"]:
                print(f"Trying CD source for {year}: {source}")
                success, info = self._download_cd(year, source)
                if success:
                    year_result["cd_success"] = True
                    year_result["cd_source"] = source
                    print(f" SUCCESS: {info}")
                    break
                else:
                    print(f" FAILED: {info}")
            if (
                year == 2025
                and not year_result["cd_success"]
                and 2024 in results
                and results[2024]["cd_success"]
            ):
                self._reuse_cd(2024, year)
                year_result["cd_success"] = True
                year_result["cd_source"] = "reused_from_2024"
                print("Reused CD data from 2024 for 2025")

            previous_year = year - 1 if year > 1984 else None
            if (
                previous_year
                and previous_year in results
                and results[previous_year]["county_success"]
            ):
                if self._county_unchanged(previous_year, year):
                    self._reuse_county(previous_year, year)
                    year_result["county_success"] = True
                    year_result["county_source"] = f"reused_from_{previous_year}"
                    print(
                        f"Reused unchanged county data from {previous_year} "
                        f"for {year}"
                    )
            if not year_result["county_success"]:
                for source in strategy["county_sources"]:
                    print(f"Trying County source for {year}: {source}")
                    success, info = self._download_county(year, source)
                    if success:
                        year_result["county_success"] = True
                        year_result["county_source"] = source
                        print(f" SUCCESS: {info}")
                        break
                    else:
                        print(f" FAILED: {info}")
            if (
                2000 <= year <= 2010
                and not year_result["county_success"]
                and 2010 in results
                and results[2010]["county_success"]
            ):
                self._reuse_county(2010, year)
                year_result["county_success"] = True
                year_result["county_source"] = "reused_from_2010_stable_boundaries"
                print(f"Backfilled county data from 2010 for {year}")
            results[year] = year_result
            print(
                f"{year} SUMMARY: "
                f"CD={'SUCCESS' if year_result['cd_success'] else 'FAILED'}, "
                f"County={'SUCCESS' if year_result['county_success'] else 'FAILED'}"
            )
        return results

    def _get_strategy(self, year: int) -> Dict[str, List[str]]:
        cd_sources = (
            ["manual_cd"]
            if year in self.manual_cd_map
            else ["ucla_github"]
            + (["tiger"] if year >= 2000 else [])
            + (["nhgis_api"] if self.nhgis_api_key else [])
        )
        county_sources = []
        if self._get_manual_county_path(year):
            county_sources.append("manual_nhgis")
        county_sources += [
            "tiger",
            "census_cartographic",
            "newberry_historical",
        ] + (["nhgis_api"] if self.nhgis_api_key else [])
        return {"cd_sources": cd_sources, "county_sources": county_sources}

    def _get_manual_county_path(self, year: int) -> Optional[str]:
        return self.manual_county_map.get(year)

    def _download_cd(self, year: int, source: str) -> Tuple[bool, str]:
        target_dir = self.base_data_dir / source / str(year) / "cd"
        target_dir.mkdir(exist_ok=True, parents=True)
        if source == "manual_cd":
            manual_path = self.manual_cd_map.get(year)
            if not manual_path:
                return False, "No manual CD path for year"
            filepath = Path(manual_path)
            if filepath.suffix == ".shp":
                for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
                    src = filepath.with_suffix(ext)
                    if src.exists():
                        shutil.copy(src, target_dir / src.name)
                return True, f"Copied manual CD {year}"
            return False, "Invalid file type"
        elif source == "tiger":
            congress_num = self.congress_mappings.get(year)
            filename = f"tl_{year}_us_cd{congress_num}.zip"
            filepath = target_dir / filename
            if filepath.exists():
                return True, "Already exists"
            directory_patterns = ["CD", "CD116", "CD118", "CD{congress_num}"]
            congress_tries = [
                congress_num,
                str(int(congress_num) - 1),
                str(int(congress_num) + 1),
            ]
            for dir_pattern in directory_patterns:
                for congress_try in congress_tries:
                    try_filename = f"tl_{year}_us_cd{congress_try}.zip"
                    url = (
                        f"https://www2.census.gov/geo/tiger/TIGER{year}/"
                        f"{dir_pattern.format(congress_num=congress_try)}/"
                        f"{try_filename}"
                    )
                    if self._download_and_extract_with_retry(url, filepath):
                        return True, (
                            f"Downloaded TIGER CD (congress {congress_try})"
                        )
            if year >= 2023 and year not in self.manual_cd_map:
                if self._download_per_state_cd(year, congress_num, target_dir):
                    return True, "Downloaded and merged per-state TIGER CDs"
            return False, "All TIGER CD attempts failed"
        elif source == "ucla_github":
            congress_num = self.congress_mappings.get(year)
            if int(congress_num) > 112:
                return False, "UCLA only up to 112th Congress"
            patterns = [f"districts{int(congress_num):03d}.zip"]
            for pattern in patterns:
                url = f"{self.ucla_shapefile_base}{pattern}"
                filepath = target_dir / pattern
                if self._download_and_extract_with_retry(url, filepath):
                    return True, f"Downloaded UCLA: {pattern}"
            return False, "UCLA failed"
        elif source == "nhgis_api":
            if not self.nhgis_api_key:
                return False, "No key"
            return self._download_nhgis_via_extract(year, "cd")
        return False, "Unknown source"

    def _download_county(self, year: int, source: str) -> Tuple[bool, str]:
        target_dir = self.base_data_dir / source / str(year) / "county"
        target_dir.mkdir(exist_ok=True, parents=True)
        if source == "manual_nhgis":
            manual_path = self._get_manual_county_path(year)
            if not manual_path:
                return False, "No manual path for year"
            filepath = Path(manual_path)
            if filepath.suffix == ".zip":
                with zipfile.ZipFile(filepath, "r") as zip_ref:
                    zip_ref.extractall(target_dir)
                return True, f"Extracted manual NHGIS {year}"
            elif filepath.suffix == ".shp":
                for ext in [".shp", ".shx", ".dbf", ".prj", ".cpg"]:
                    src = filepath.with_suffix(ext)
                    if src.exists():
                        shutil.copy(src, target_dir / src.name)
                return True, f"Copied manual NHGIS {year}"
            return False, "Invalid file type"
        if source == "tiger":
            filename = f"tl_{year}_us_county.zip"
            filepath = target_dir / filename
            if filepath.exists():
                return True, "Already exists"
            urls = [
                f"{self.census_cartographic_base}TIGER{year}/COUNTY/{filename}",
                f"{self.census_cartographic_base}TIGER{year}/county/{filename}",
            ]
            for url in urls:
                if self._download_and_extract_with_retry(url, filepath):
                    return True, "Downloaded TIGER county"
            print(" National TIGER county failed, trying per-state")
            if self._download_per_state_county(year, target_dir):
                return True, "Downloaded and merged per-state TIGER counties"
            return False, "TIGER county failed"
        elif source == "census_cartographic":
            patterns = [
                f"cb_{year}_us_county_500k.zip",
                f"cb_{year}_us_county_20m.zip",
                f"cb_{year}_us_county_5m.zip",
            ]
            for pattern in patterns:
                url = f"{self.census_cartographic_base}GENZ{year}/shp/{pattern}"
                filepath = target_dir / pattern
                if self._download_and_extract_with_retry(url, filepath):
                    return True, f"Downloaded cartographic county {pattern}"
            return False, "Cartographic failed"
        elif source == "nhgis_api":
            return self._download_nhgis_via_extract(year, "county")
        elif source == "newberry_historical":
            output_file = target_dir / f"counties_{year}_newberry.shp"
            if output_file.exists():
                return True, "Already exists"
            if self.newberry_loaded_gdf is None:
                return False, "Newberry load failed. Check network or path."
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
        return False, "Unknown source"

    def _download_and_extract_with_retry(
        self, url: str, filepath: Path, max_retries: int = 3
    ) -> bool:
        for attempt in range(max_retries):
            try:
                response = self.session.get(
                    url, timeout=600, verify=False, stream=True
                )
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

    def _download_per_state_county(self, year: int, target_dir: Path) -> bool:
        gdfs = []
        state_map = self._get_state_mapping()
        if 2000 <= year <= 2010:
            year_use = 2010
            base_dir = f"TIGER{year_use}"
            suffix = "10"
            for fips, state_name in state_map.items():
                state_dir = self._get_state_dir(fips, state_name)
                filename = f"tl_{year_use}_{fips}_county{suffix}.zip"
                url = (
                    f"{self.census_cartographic_base}{base_dir}/"
                    f"{state_dir}/{filename}"
                )
                filepath = target_dir / filename
                if self._download_and_extract_with_retry(url, filepath):
                    shp_files = list(
                        target_dir.rglob(f"tl_{year_use}_{fips}_county{suffix}.shp")
                    )
                    if shp_files:
                        gdfs.append(gpd.read_file(shp_files[0]))
        elif year == 2007:
            base_dir = f"TIGER{year}FE"
            for fips, state_name in state_map.items():
                state_dir = self._get_state_dir(fips, state_name)
                filename = f"fe_{year}_{fips}_county.zip"
                url = (
                    f"{self.census_cartographic_base}{base_dir}/"
                    f"{state_dir}/{filename}"
                )
                filepath = target_dir / filename
                if self._download_and_extract_with_retry(url, filepath):
                    shp_files = list(
                        target_dir.rglob(f"fe_{year}_{fips}_county.shp")
                    )
                    if shp_files:
                        gdfs.append(gpd.read_file(shp_files[0]))
        elif year >= 2008:
            base_dir = f"TIGER{year}"
            for fips, state_name in state_map.items():
                state_dir = self._get_state_dir(fips, state_name)
                filename = f"tl_{year}_{fips}_county.zip"
                url = (
                    f"{self.census_cartographic_base}{base_dir}/"
                    f"{state_dir}/{filename}"
                )
                filepath = target_dir / filename
                if self._download_and_extract_with_retry(url, filepath):
                    shp_files = list(
                        target_dir.rglob(f"tl_{year}_{fips}_county.shp")
                    )
                    if shp_files:
                        gdfs.append(gpd.read_file(shp_files[0]))
        if gdfs:
            merged = gpd.GeoDataFrame(
                pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs
            )
            merged.to_file(target_dir / f"tl_{year}_us_county.shp")
            for file in target_dir.rglob("*.zip"):
                file.unlink()
            return True
        return False

    def _load_newberry_full(self) -> Optional[gpd.GeoDataFrame]:
        # Prefer the user-configured local path from manual sources.
        if self.local_newberry_path:
            local_path = Path(self.local_newberry_path)
            if local_path.exists():
                print(f"Loading local Newberry shapefile from {local_path}")
                gdf = gpd.read_file(local_path)
                gdf["START_DATE"] = pd.to_datetime(gdf["START_DATE"], errors="coerce")
                gdf["END_DATE"] = pd.to_datetime(gdf["END_DATE"], errors="coerce")
                gdf["END_DATE"] = gdf["END_DATE"].fillna(datetime(2010, 12, 31))
                print(f"Loaded Newberry with {len(gdf)} records")
                return gdf

        # Fall back to network download (note: URLs change periodically).
        target_dir = self.base_data_dir / "newberry_historical" / "full"
        target_dir.mkdir(exist_ok=True)
        patterns = [
            "US_HistCounties.zip",
            "US_HistCounties_Gen001.zip",
            "US_HistCounties_Gen01.zip",
            "US_HistCounties_Gen05.zip",
        ]
        for pattern in patterns:
            filepath = target_dir / pattern
            url = f"{self.newberry_base}/{pattern}"
            print(f"Trying Newberry URL: {url}")
            if self._download_and_extract_with_retry(url, filepath):
                shp_files = list(target_dir.rglob("*.shp"))
                if shp_files:
                    gdf = gpd.read_file(shp_files[0])
                    gdf["START_DATE"] = pd.to_datetime(
                        gdf["START_DATE"], errors="coerce"
                    )
                    gdf["END_DATE"] = pd.to_datetime(
                        gdf["END_DATE"], errors="coerce"
                    )
                    gdf["END_DATE"] = gdf["END_DATE"].fillna(
                        datetime(2010, 12, 31)
                    )
                    print(f"Loaded Newberry data from {pattern}")
                    return gdf
        print(
            "Newberry download failed. Run scripts/setup_data.py or place the "
            "shapefile manually at "
            f"{self.manual_sources_dir}/newberry_historical/"
        )
        return None

    def _county_unchanged(self, prev_year: int, year: int) -> bool:
        prev_shp = list(
            (self.base_data_dir / "tiger" / str(prev_year) / "county").rglob("*.shp")
        )
        current_shp = list(
            (self.base_data_dir / "tiger" / str(year) / "county").rglob("*.shp")
        )
        if not prev_shp or not current_shp:
            return False
        prev_gdf = gpd.read_file(prev_shp[0])
        current_gdf = gpd.read_file(current_shp[0])
        prev_hash = md5(
            pd.util.hash_pandas_object(prev_gdf.geometry).to_string().encode()
        ).hexdigest()
        current_hash = md5(
            pd.util.hash_pandas_object(current_gdf.geometry).to_string().encode()
        ).hexdigest()
        return prev_hash == current_hash

    def _reuse_county(self, prev_year: int, year: int):
        prev_dir = self.base_data_dir / "tiger" / str(prev_year) / "county"
        current_dir = self.base_data_dir / "tiger" / str(year) / "county"
        current_dir.mkdir(exist_ok=True, parents=True)
        for file in prev_dir.rglob("*"):
            if file.is_file():
                rel_path = file.relative_to(prev_dir)
                (current_dir / rel_path.parent).mkdir(exist_ok=True, parents=True)
                shutil.copy(file, current_dir / rel_path)

    def _reuse_cd(self, prev_year: int, year: int):
        prev_dir = self.base_data_dir / "tiger" / str(prev_year) / "cd"
        current_dir = self.base_data_dir / "tiger" / str(year) / "cd"
        current_dir.mkdir(exist_ok=True, parents=True)
        for file in prev_dir.rglob("*"):
            if file.is_file():
                rel_path = file.relative_to(prev_dir)
                (current_dir / rel_path.parent).mkdir(exist_ok=True, parents=True)
                shutil.copy(file, current_dir / rel_path)

    def _get_nhgis_shapefiles_for_year(
        self, year: int, geography_type: str
    ) -> List[Dict]:
        if not self.nhgis_api_key:
            return []
        headers = {"Authorization": f"Bearer {self.nhgis_api_key}"}
        url = f"{self.metadata_endpoint}/shapefiles?collection=nhgis&version=2"
        response = self.session.get(url, headers=headers, timeout=30)
        if response.status_code != 200:
            return []
        shapefiles = response.json().get("data", [])
        filtered = []
        for shapefile in shapefiles:
            shapefile_year = shapefile.get("year", "")
            geog_level = shapefile.get("geographicLevel", "").lower()
            if str(year) == shapefile_year:
                if geography_type == "cd" and any(
                    term in geog_level for term in ["congressional", "district"]
                ):
                    filtered.append(shapefile)
                elif geography_type == "county" and "county" in geog_level:
                    filtered.append(shapefile)
        return filtered

    def _monitor_and_download_nhgis_extract(
        self, extract_number: int, target_dir: Path, headers: Dict, desc: str
    ) -> Tuple[bool, str]:
        status_url = (
            f"{self.extracts_endpoint}/{extract_number}"
            f"?collection=nhgis&version=2"
        )
        for _ in range(60):
            response = self.session.get(status_url, headers=headers, timeout=30)
            if response.status_code != 200:
                return False, f"Status check failed: {response.status_code}"
            extract_info = response.json()
            status = extract_info.get("status")
            if status == "completed":
                gis_link = extract_info.get("downloadLinks", {}).get("gisData")
                if not gis_link:
                    return False, "No GIS data link"
                download_url = gis_link.get("url")
                filename = f"nhgis_{desc.replace(' ', '_')}_extract_{extract_number}.zip"
                filepath = target_dir / filename
                dl = self.session.get(download_url, headers=headers, timeout=300)
                dl.raise_for_status()
                with open(filepath, "wb") as f:
                    for chunk in dl.iter_content(chunk_size=8192):
                        f.write(chunk)
                with zipfile.ZipFile(filepath, "r") as zip_ref:
                    zip_ref.extractall(target_dir)
                return True, f"Downloaded NHGIS {desc}: {filename}"
            elif status == "failed":
                return False, "Extract failed"
            time.sleep(10)
        return False, "Timed out"

    def _download_nhgis_via_extract(
        self, year: int, geography_type: str
    ) -> Tuple[bool, str]:
        if not self.nhgis_api_key:
            return False, "NHGIS API key not configured"
        target_dir = self.base_data_dir / "nhgis_api" / str(year) / geography_type
        target_dir.mkdir(exist_ok=True, parents=True)
        shp_files = list(target_dir.rglob("*.shp"))
        if shp_files:
            return True, f"Already exists: {len(shp_files)} NHGIS shapefiles"
        headers = {
            "Authorization": f"Bearer {self.nhgis_api_key}",
            "Content-Type": "application/json",
        }
        try:
            shapefiles = self._get_nhgis_shapefiles_for_year(year, geography_type)
            if not shapefiles:
                return False, f"No NHGIS {geography_type} shapefiles for {year}"
            extract_request = {
                "description": f"Automated {geography_type} extract for {year}",
                "shapefiles": [shapefiles[0]["name"]],
                "dataFormat": "csv_no_header",
            }
            extract_url = (
                f"{self.extracts_endpoint}/?collection=nhgis&version=2"
            )
            response = self.session.post(
                extract_url, headers=headers, json=extract_request, timeout=60
            )
            if response.status_code != 200:
                return False, (
                    f"Extract request failed: {response.status_code} - "
                    f"{response.text[:100]}"
                )
            extract_number = response.json().get("number")
            if not extract_number:
                return False, "No extract number returned"
            return self._monitor_and_download_nhgis_extract(
                extract_number, target_dir, headers, f"{geography_type} {year}"
            )
        except Exception as e:
            return False, f"NHGIS extract error: {str(e)[:100]}"

    def compute_matches(self, years: List[int]) -> pd.DataFrame:
        all_overlaps = []
        for year in years:
            cd_gdf, county_gdf = self._load_shapefiles(year)
            if cd_gdf is None or county_gdf is None:
                print(f"Skipping {year}: Missing data")
                continue
            overlaps = self._calculate_overlap(cd_gdf, county_gdf, year)
            if len(overlaps):
                all_overlaps.append(overlaps)
        if all_overlaps:
            combined = pd.concat(all_overlaps, ignore_index=True)
            standard_cols = [
                "year", "data_source", "processing_date", "state_name",
                "cd_number", "cd_geoid", "cd_name", "county_name", "county_fips",
                "cd_area_km2", "county_area_km2", "intersection_area_km2",
                "pct_cd_in_county", "pct_county_in_cd",
            ]
            combined = combined.reindex(columns=standard_cols, fill_value=pd.NA)
            out_path = self.base_data_dir / "results" / "matches.csv"
            combined.to_csv(out_path, index=False)
            print(f"Saved matches to {out_path}")
            return combined
        return pd.DataFrame()

    def _load_shapefiles(
        self, year: int
    ) -> Tuple[Optional[gpd.GeoDataFrame], Optional[gpd.GeoDataFrame]]:
        cd_gdf = None
        county_gdf = None
        cd_search_paths = [
            self.base_data_dir / "manual_cd" / str(year) / "cd",
            self.base_data_dir / "tiger" / str(year) / "cd",
            self.base_data_dir / "ucla_github" / str(year) / "cd",
            self.base_data_dir / "nhgis_api" / str(year) / "cd",
        ]
        for path in cd_search_paths:
            if path.exists():
                shp_files = list(path.rglob("*.shp"))
                if shp_files:
                    cd_gdf = gpd.read_file(shp_files[0])
                    print(f"Loaded CD for {year} from {path.parent.name}")
                    break
        county_search_paths = [
            self.base_data_dir / "manual_nhgis" / str(year) / "county",
            self.base_data_dir / "tiger" / str(year) / "county",
            self.base_data_dir / "census_cartographic" / str(year) / "county",
            self.base_data_dir / "newberry_historical" / str(year) / "county",
            self.base_data_dir / "nhgis_api" / str(year) / "county",
        ]
        for path in county_search_paths:
            if path.exists():
                shp_files = list(path.rglob("*.shp"))
                if shp_files:
                    county_gdf = gpd.read_file(shp_files[0])
                    print(f"Loaded County for {year} from {path.parent.name}")
                    break
        return cd_gdf, county_gdf

    def _calculate_overlap(
        self, cd_gdf: gpd.GeoDataFrame, county_gdf: gpd.GeoDataFrame, year: int
    ) -> pd.DataFrame:
        print(f"Calculating overlaps for {year}...")
        target_crs = "EPSG:3857"
        cd_gdf = cd_gdf.to_crs(target_crs)
        county_gdf = county_gdf.to_crs(target_crs)
        cd_gdf = self._standardize_columns(cd_gdf, "cd", year)
        county_gdf = self._standardize_columns(county_gdf, "county", year)
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
            intersection["intersection_area_km2"] / intersection["county_area_km2"] * 100
        )
        significant = intersection[intersection["pct_cd_in_county"] >= 1.0].copy()
        significant["year"] = year
        significant["data_source"] = "cd_county_matcher"
        significant["processing_date"] = datetime.now().strftime("%Y-%m-%d")
        state_map = self._get_state_mapping()
        if "state_fips" in significant.columns:
            significant["state_name"] = significant["state_fips"].map(state_map)
        else:
            if "cd_geoid" in significant.columns:
                significant["state_fips"] = significant["cd_geoid"].str[:2]
            elif "county_fips" in significant.columns:
                significant["state_fips"] = significant["county_fips"].str[:2]
            significant["state_name"] = significant["state_fips"].map(state_map)
        if "cd_number" not in significant.columns:
            significant["cd_number"] = pd.NA
        output_cols = [
            "year", "data_source", "processing_date", "state_name", "cd_number",
            "cd_geoid", "cd_name", "county_name", "county_fips", "cd_area_km2",
            "county_area_km2", "intersection_area_km2", "pct_cd_in_county",
            "pct_county_in_cd",
        ]
        for col in output_cols:
            if col not in significant.columns:
                significant[col] = pd.NA
        result = significant[output_cols].drop(columns="geometry", errors="ignore")
        print(f" Found {len(result)} overlaps")
        return result

    def _standardize_columns(
        self, gdf: gpd.GeoDataFrame, data_type: str, year: int
    ) -> gpd.GeoDataFrame:
        gdf = gdf.copy()
        if data_type == "cd":
            mappings = {
                "GEOID": "cd_geoid", "GEOID20": "cd_geoid",
                "CD": "cd_number", "CD116": "cd_number", "CD118": "cd_number",
                "CD112FP": "cd_number", "NAME": "cd_name", "NAMELSAD": "cd_name",
                "STATEFP": "state_fips", "STATEFP20": "state_fips",
                "STATE": "state_fips", "STATE_CODE": "state_fips",
                "STATE_FIPS": "state_fips", "DISTRICT": "cd_number",
                "CONG_DIST": "cd_number", "STATE_ABBR": "state_abbr",
                "STATENAME": "state_name", "GISJOIN": "cd_geoid",
                "NHGISCODE": "cd_geoid", "DIST": "cd_number",
                "CDSESSN": "cd_number", "CD113FP": "cd_number",
                "CD114FP": "cd_number", "CD115FP": "cd_number",
                "CD116FP": "cd_number",
            }
            for old, new in mappings.items():
                if old in gdf.columns and new not in gdf.columns:
                    gdf = gdf.rename(columns={old: new})
            if "state_fips" not in gdf.columns and "cd_geoid" in gdf.columns:
                gdf["state_fips"] = gdf["cd_geoid"].str[:2]
            if "cd_number" not in gdf.columns:
                if "CDNUM" in gdf.columns:
                    gdf["cd_number"] = gdf["CDNUM"].astype(str)
                elif "DIST" in gdf.columns:
                    gdf["cd_number"] = gdf["DIST"].astype(str)
                elif "cd_geoid" in gdf.columns:
                    gdf["cd_number"] = gdf["cd_geoid"].str[-2:].astype(str)
                else:
                    gdf["cd_number"] = (gdf.index + 1).astype(str)
            if "cd_geoid" not in gdf.columns:
                if "state_fips" in gdf.columns and "cd_number" in gdf.columns:
                    gdf["cd_geoid"] = (
                        gdf["state_fips"] + gdf["cd_number"].astype(str).str.zfill(2)
                    )
                else:
                    gdf["cd_geoid"] = pd.NA
            if "cd_name" not in gdf.columns:
                gdf["cd_name"] = gdf["cd_number"].apply(
                    lambda x: f"Congressional District {x}"
                )
        else:
            mappings = {
                "GEOID": "county_fips", "GEOID20": "county_fips",
                "COUNTYFP": "county_fp", "FIPS": "county_fips",
                "NAME": "county_name", "NAMELSAD": "county_name",
                "STATEFP": "state_fips", "STATEFP20": "state_fips",
                "STATE": "state_fips", "STATE_CODE": "state_fips",
                "STATE_FIPS": "state_fips", "GISJOIN": "county_fips",
                "NHGISCODE": "county_fips", "COUNTY": "county_name",
                "FIPS_CODE": "county_fips", "FULL_NAME": "county_name",
                "FROMCOUNTY": "county_name", "COUNTYNS": "county_name",
                "NAMELSAD10": "county_name", "NAME10": "county_name",
                "STATEFP10": "state_fips", "COUNTYFP10": "county_fp",
                "GEOID10": "county_fips",
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
                    gdf["state_fips"] + gdf["county_fp"].astype(str).str.zfill(3)
                )
            if "state_fips" not in gdf.columns and "county_fips" in gdf.columns:
                gdf["state_fips"] = gdf["county_fips"].str[:2]
        if "state_name" not in gdf.columns and "state_fips" in gdf.columns:
            state_map = self._get_state_mapping()
            gdf["state_name"] = gdf["state_fips"].map(state_map)
        return gdf

    def _get_state_mapping(self) -> Dict[str, str]:
        return {
            "01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas",
            "06": "California", "08": "Colorado", "09": "Connecticut",
            "10": "Delaware", "11": "District of Columbia", "12": "Florida",
            "13": "Georgia", "15": "Hawaii", "16": "Idaho", "17": "Illinois",
            "18": "Indiana", "19": "Iowa", "20": "Kansas", "21": "Kentucky",
            "22": "Louisiana", "23": "Maine", "24": "Maryland",
            "25": "Massachusetts", "26": "Michigan", "27": "Minnesota",
            "28": "Mississippi", "29": "Missouri", "30": "Montana",
            "31": "Nebraska", "32": "Nevada", "33": "New Hampshire",
            "34": "New Jersey", "35": "New Mexico", "36": "New York",
            "37": "North Carolina", "38": "North Dakota", "39": "Ohio",
            "40": "Oklahoma", "41": "Oregon", "42": "Pennsylvania",
            "44": "Rhode Island", "45": "South Carolina", "46": "South Dakota",
            "47": "Tennessee", "48": "Texas", "49": "Utah", "50": "Vermont",
            "51": "Virginia", "53": "Washington", "54": "West Virginia",
            "55": "Wisconsin", "56": "Wyoming",
        }

    def _download_per_state_cd(
        self, year: int, congress_num: str, target_dir: Path
    ) -> bool:
        gdfs = []
        for fips in self.state_fips_to_abbr:
            filename = f"tl_{year}_{fips}_cd{congress_num}.zip"
            url = (
                f"{self.census_cartographic_base}TIGER{year}/CD{congress_num}/"
                f"{filename}"
            )
            filepath = target_dir / filename
            if self._download_and_extract_with_retry(url, filepath):
                shp_files = list(
                    target_dir.rglob(f"tl_{year}_{fips}_cd{congress_num}.shp")
                )
                if shp_files:
                    gdfs.append(gpd.read_file(shp_files[0]))
        if gdfs:
            merged = gpd.GeoDataFrame(
                pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs
            )
            merged.to_file(target_dir / f"tl_{year}_us_cd{congress_num}.shp")
            return True
        return False


# Back-compat alias so existing scripts don't break.
UpdatedMatcher = CDCountyMatcher

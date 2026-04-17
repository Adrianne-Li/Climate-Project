# cd-county-matcher

Compute area-based overlaps between **US Congressional Districts** and **counties** for any year from 1984 through 2025. The tool pulls shapefiles from multiple public sources — TIGER/Line, Census cartographic files, NHGIS, the UCLA Congressional District Boundary Project, and the Newberry Atlas of Historical County Boundaries — and produces a tidy CSV where each row describes what fraction of a CD lies in a given county (and vice versa).

## Quickstart

```bash
# 1. Clone
git clone git@github.com:Adrianne-Li/Climate-Project.git cd-county-matcher
cd cd-county-matcher

# 2. Create the conda environment (recommended — handles GDAL/GEOS/PROJ for you)
conda env create -f environment.yml
conda activate py312

# 3. (Optional) Register as a Jupyter kernel
python -m ipykernel install --user --name py312 --display-name "Python (py312)"

# 4. Fetch the large shapefiles that don't live in this repo (~300 MB total)
python scripts/setup_data.py

# 5. Run the matcher
python scripts/run_matcher.py --start 1984 --end 2025
```

Results land in `data/results/matches.csv`.

## Why there's a setup step

A handful of the shapefiles the matcher uses are too large or too awkwardly licensed to ship inside a git repo — most notably the TIGER 2010 county file (~75 MB) and the Newberry Atlas of Historical County Boundaries (~500 MB). Instead of checking them in, `scripts/setup_data.py` pulls them from their canonical public sources into `data/manual_sources/` on first run. This keeps the repo small, the licensing clean, and the data current.

The downloader is resilient: if a specific URL 404s (they occasionally move), the script prints a fallback note telling you where to grab the file manually and what folder to drop it into.

## NHGIS API key (optional)

NHGIS is used as a fallback when the primary sources (TIGER, UCLA, cartographic boundary files) don't cover a given year. If you want to enable it:

1. Register for a free IPUMS NHGIS account and generate an API key at https://account.ipums.org/api_keys
2. Either pass it on the command line:
   ```bash
   python scripts/run_matcher.py --nhgis-key YOUR_KEY
   ```
   or export it:
   ```bash
   export NHGIS_API_KEY=YOUR_KEY
   python scripts/run_matcher.py
   ```

If no key is supplied, NHGIS sources are simply skipped. The other sources cover every year on their own.

## Running just a subset of years

```bash
python scripts/run_matcher.py --years 2012 2016 2020 2024
python scripts/run_matcher.py --start 2010 --end 2020
```

## Running only the compute step

If you've already downloaded the shapefiles on a previous run and just want to recompute overlaps:

```bash
python scripts/run_matcher.py --skip-download --start 1984 --end 2025
```

## Using from Python

```python
from src.matcher import CDCountyMatcher

matcher = CDCountyMatcher(data_dir="./data", nhgis_api_key=None)
matcher.download_data([2012, 2016, 2020])
matches_df = matcher.compute_matches([2012, 2016, 2020])
```

## Output schema

`data/results/matches.csv` has one row per (CD, county) pair per year where the district has at least 1% of its area in the county:

| column | meaning |
|---|---|
| `year` | election / boundary year |
| `state_name` | human-readable state |
| `cd_number` | district number within the state |
| `cd_geoid` | Census GEOID for the district |
| `cd_name` | e.g. "Congressional District 3" |
| `county_name` | county name |
| `county_fips` | 5-digit FIPS |
| `cd_area_km2` | total district area |
| `county_area_km2` | total county area |
| `intersection_area_km2` | overlap area |
| `pct_cd_in_county` | % of the district that's in this county |
| `pct_county_in_cd` | % of the county that's in this district |
| `data_source`, `processing_date` | provenance |

## Data sources

| Source | Years covered | Notes |
|---|---|---|
| TIGER/Line | 2000-present | Primary source; falls back to per-state downloads when national files are missing |
| Census cartographic (CB) | 2013-present | Smaller, generalized boundaries |
| UCLA (Lewis et al.) | 1984-2012 | Historical congressional districts |
| NHGIS | 1790-present | Optional, requires API key |
| Newberry Atlas | 1790-2000 | Historical county boundaries, fetched by `setup_data.py` |

## Directory layout

```
cd-county-matcher/
├── src/
│   ├── __init__.py
│   └── matcher.py            # CDCountyMatcher class
├── scripts/
│   ├── setup_data.py         # Fetch large shapefiles (run once)
│   └── run_matcher.py        # CLI entry point
├── data/                     # Gitignored — populated at runtime
│   ├── manual_sources/       # Populated by setup_data.py
│   ├── tiger/                # Per-year TIGER downloads
│   ├── ucla_github/          # UCLA historical CDs
│   ├── census_cartographic/  # CB files
│   ├── newberry_historical/  # Derived per-year filtered counties
│   ├── nhgis_api/            # NHGIS extracts (if key provided)
│   └── results/
│       └── matches.csv
├── environment.yml           # Conda env (recommended)
├── requirements.txt          # Pip fallback
└── README.md
```

## Troubleshooting

**`ImportError: No module named 'fiona'` or similar when using pip**
The geospatial stack needs native GDAL/GEOS/PROJ libraries. Use the conda environment (`environment.yml`) — it's the painless path. If you're locked into pip, you'll need to install GDAL/GEOS/PROJ through your OS package manager first.

**A Newberry or Census URL returns 404**
URLs at publications.newberry.org and www2.census.gov do move. Re-run `python scripts/setup_data.py` — if it still fails, follow the fallback note it prints (usually: download the file manually from the linked page and drop it in `data/manual_sources/<source-name>/`).

**Out of memory on the full 1984-2025 run**
Process in chunks: `python scripts/run_matcher.py --start 1984 --end 2000`, then `--start 2001 --end 2025`. The overlay computation is memory-heavy for large multi-year runs.

## License

MIT. Downloaded shapefiles retain their original licenses — see the source organizations for details.

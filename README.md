# cd-county-matcher

Compute area-based overlaps between **US Congressional Districts** and **counties** for any year from 1984 through 2025. The tool pulls shapefiles from multiple public sources — TIGER/Line, Census cartographic files, NHGIS, the UCLA Congressional District Boundary Project, and the Newberry Atlas of Historical County Boundaries — and produces a tidy CSV where each row describes what fraction of a CD lies in a given county (and vice versa).

## Quickstart

```bash
# 1. Clone
git clone https://github.com/Adrianne-Li/Climate-Project.git cd-county-matcher
cd cd-county-matcher

# 2. Create the conda environment (recommended — handles GDAL/GEOS/PROJ for you)
conda env create -f environment.yml
conda activate py312

# 3. (Optional) Register as a Jupyter kernel
python -m ipykernel install --user --name py312 --display-name "Python (py312)"

# 4. Fetch the large shapefiles that don't live in this repo (~300 MB total).
#    By default these come from our project's OSF Storage (fast); see
#    "Hosting the large files on OSF" below to configure the GUIDs.
python scripts/setup_data.py

# 5. Run the matcher
python scripts/run_matcher.py --start 1984 --end 2025

# 6. (Optional) Run the post-processing pipeline (state backfill, uniform CD
#    numbers + year-shift correction, redistricting analysis):
python scripts/run_pipeline.py --skip-matcher --start 1984 --end 2025
```

Results land in `data/results/`. The matcher produces `matches.csv`; the
pipeline adds `matches_state_filled.csv`, `matches_with_uniform_cd_shifted.csv`,
`redistricting_analysis.csv`, and `redistricting_summary.txt`.

To run *everything* (matcher + post-processing) in one go:

```bash
python scripts/run_pipeline.py --start 1984 --end 2025
```

## Why there's a setup step

A handful of the shapefiles the matcher uses are too large or too awkwardly licensed to ship inside a git repo — most notably the TIGER 2010 county file (~75 MB) and the Newberry Atlas of Historical County Boundaries (~500 MB). Instead of checking them in, `scripts/setup_data.py` pulls them into `data/manual_sources/` on first run. This keeps the repo small, the licensing clean, and the data current.

The **primary** source for these files is our project's OSF Storage (see below), which is fast and has stable URLs. This replaced the old behaviour of crawling the original public servers (Census, Newberry), which were slow and whose URLs move around. The original public URLs are kept as automatic fallbacks.

The downloader is resilient: for each file it tries OSF first, then the public fallback URL; if everything fails it prints a fallback note telling you where to grab the file manually and what folder to drop it into.

## Hosting the large files on OSF

The four manual shapefiles live in our OSF project's **OSF Storage** so collaborators don't re-download them from slow public servers. `setup_data.py` only needs the project's GUID — it queries the OSF API, lists the files in the project, matches the ones it needs by name, and downloads them. No per-file GUIDs to copy.

The project GUID is already set in `osf_sources.json`:

```json
{ "_osf_project": "https://osf.io/eqsjw/" }
```

**How the files should be uploaded.** Either style works — `setup_data.py` auto-detects which one is present:

- **Raw files (what we use):** upload each shapefile's components directly into OSF Storage — `*.shp`, `*.shx`, `*.dbf`, `*.prj` (`.cpg` optional). They can sit flat in the storage root or in folders; names just have to match (e.g. `US_HistCounties.shp`, `tl_2010_us_county10.shp`, `cb_2023_us_cd118_5m.shp`, `cb_2024_us_cd119_5m.shp`). Extra sidecar files like `.shp.xml` / `.iso.xml` are ignored. This is the no-zip path — drag the files in and you're done.
- **Zips:** alternatively, one `.zip` per source containing its shapefile set; `setup_data.py` downloads, unzips, and flattens it.

**Private vs public project.** Our project is private, so the OSF API and the downloads need an access token:

1. Create a Personal Access Token at https://osf.io/settings/tokens/ (the `osf.full_read` scope is enough).
2. Export it before running setup (never commit it):

   ```bash
   export OSF_TOKEN=your_token_here
   python scripts/setup_data.py        # add --force to overwrite existing files
   ```

   Or pass it inline: `python scripts/setup_data.py --osf-token your_token_here`.

   Each collaborator needs their own token and must be a contributor on the project. If you'd rather skip tokens entirely, make the project public — then `setup_data.py` works with no token at all.

You should see, per source, `OSF raw files (auto-discovered): ...` and a summary line ending in `(primary: OSF(raw files))`. If a download fails, it falls back to the original public URL automatically.

**Per-file GUID override (optional).** If you ever want to pin a specific file, paste its 5-character GUID (`osf.io/XXXXX/` → `XXXXX`) into the matching field in `osf_sources.json` (`county_2010`, `cd_118th_2023`, `cd_119th_2025`, `newberry_historical`); those take priority over auto-discovery. Other ways to supply config: `--osf-config path.json`, the `OSF_SOURCES_JSON` / `OSF_PROJECT_GUID` env vars.

## Post-processing pipeline

After the matcher writes `matches.csv`, three steps turn it into the redistricting analysis. Each is a standalone script (formerly cells in `Code Patch 0424.ipynb`) and can be run on its own or chained with `run_pipeline.py`:

```bash
# Stage 1 — backfill state_name (derives it from county_fips; recovers the
#           ~94% of 1984–2012 rows that come through without a state).
python scripts/backfill_state.py            # --> matches_state_filled.csv

# Stage 2 — add a uniform cross-year CD number + shift year by -1 to correct
#           the storage glitch (geometry under year Y reflects session Y-1).
python scripts/add_uniform_cd.py            # --> matches_with_uniform_cd_shifted.csv

# Stage 3 — detect redistricting events year-over-year per (state, district).
python scripts/analyze_redistricting.py     # --> redistricting_analysis.csv + _summary.txt
```

All three default to reading/writing inside `<data-dir>/results/` and accept `--data-dir`, `--input`, and `--output` overrides (run any with `-h` for the full list). Useful flags: `backfill_state.py --territory-policy {fill,drop}`, `add_uniform_cd.py --year-shift N`, `analyze_redistricting.py --threshold PCT` (default 20).

`run_pipeline.py` runs the matcher and all three stages in order with consistent paths. Use `--skip-matcher` to re-run only the post-processing on an existing `matches.csv`, and `--skip-download` to forward to the matcher's compute-only mode.

> **Note on identifier columns:** the pipeline reads `cd_geoid`, `county_fips`, etc. as strings. CSV round-tripping otherwise coerces zero-padded codes like `"0601"` to floats (`601.0`) and drops the leading zero the analysis depends on.

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
│   └── matcher.py            # CDCountyMatcher class + OSF source config
├── scripts/
│   ├── setup_data.py         # Fetch large shapefiles (OSF-first; run once)
│   ├── run_matcher.py        # Matcher CLI entry point
│   ├── backfill_state.py     # Pipeline 1/3: fill state_name from county_fips
│   ├── add_uniform_cd.py     # Pipeline 2/3: uniform CD number + year shift
│   ├── analyze_redistricting.py  # Pipeline 3/3: redistricting analysis
│   └── run_pipeline.py       # Orchestrator: matcher + all post-processing
├── osf_sources.json          # OSF GUIDs for the large files (edit after upload)
├── data/                     # Gitignored — populated at runtime
│   ├── manual_sources/       # Populated by setup_data.py (from OSF)
│   ├── tiger/                # Per-year TIGER downloads
│   ├── ucla_github/          # UCLA historical CDs
│   ├── census_cartographic/  # CB files
│   ├── newberry_historical/  # Derived per-year filtered counties
│   ├── nhgis_api/            # NHGIS extracts (if key provided)
│   └── results/
│       ├── matches.csv                          # matcher output
│       ├── matches_state_filled.csv             # after backfill_state.py
│       ├── matches_with_uniform_cd_shifted.csv  # after add_uniform_cd.py
│       ├── redistricting_analysis.csv           # after analyze_redistricting.py
│       └── redistricting_summary.txt
├── environment.yml           # Conda env (recommended)
├── requirements.txt          # Pip fallback
└── README.md
```

## Troubleshooting

**`ImportError: No module named 'fiona'` or similar when using pip**
The geospatial stack needs native GDAL/GEOS/PROJ libraries. Use the conda environment (`environment.yml`) — it's the painless path. If you're locked into pip, you'll need to install GDAL/GEOS/PROJ through your OS package manager first.

**A Newberry or Census URL returns 404**
These files are now served primarily from OSF (see "Hosting the large files on OSF"), so configuring `osf_sources.json` avoids the moving public URLs entirely. If you haven't set up OSF and a public URL at publications.newberry.org or www2.census.gov has moved, re-run `python scripts/setup_data.py` — if it still fails, follow the fallback note it prints (usually: download the file manually from the linked page and drop it in `data/manual_sources/<source-name>/`).

**Out of memory on the full 1984-2025 run**
Process in chunks: `python scripts/run_matcher.py --start 1984 --end 2000`, then `--start 2001 --end 2025`. The overlay computation is memory-heavy for large multi-year runs.

## License

MIT. Downloaded shapefiles retain their original licenses — see the source organizations for details.

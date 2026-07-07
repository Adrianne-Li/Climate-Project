# cd-county-matcher

Compute area-based overlaps between US Congressional Districts and counties for
1984-2025. This package is OSF-first: it discovers the public OSF project,
downloads raw shapefile components or zips from OSF, and only then falls back
to public Census/UCLA sources.

## What This Build Fixes

- Registers annual county sources `county_2011` through `county_2023`, so
  `tl_2012_us_county.*` and neighboring annual county shapefiles are found.
- Downloads missing `.prj` sidecars from OSF before matching, so files such as
  `districts102.shp` do not load as CRS-naive geometries.
- Keeps a final CRS safety net: if a shapefile still lacks CRS metadata, the
  matcher assumes `EPSG:4269` before projecting. Override with
  `MATCHER_SOURCE_CRS` if needed.
- Keeps the post-processing defaults aligned with the UCLA year-to-Congress
  convention, using `--year-shift 0` by default.

## Quickstart on macOS

```bash
# 1. Unzip and enter the package
unzip cd-county-matcher-complete.zip
cd cd-county-matcher-complete

# 2. Create and activate the conda environment
conda env create -f environment.yml
conda activate py312

# 3. Optional: register as a Jupyter kernel
python -m ipykernel install --user --name py312 --display-name "Python (py312)"

# 4. Fetch large/manual shapefiles from OSF
python scripts/setup_data.py

# 5. Run the matcher
python scripts/run_matcher.py --start 1984 --end 2025

# 6. Run post-processing
python scripts/run_pipeline.py --skip-matcher --start 1984 --end 2025
```

## Quickstart on Windows

Run in Anaconda Prompt or PowerShell:

```powershell
# 1. Unzip and enter the package
cd C:\path\to\cd-county-matcher-complete

# 2. Create and activate the conda environment
conda env create -f environment.yml
conda activate py312

# 3. Optional: register as a Jupyter kernel
python -m ipykernel install --user --name py312 --display-name "Python (py312)"

# 4. Fetch large/manual shapefiles from OSF
python scripts/setup_data.py

# 5. Run the matcher
python scripts/run_matcher.py --start 1984 --end 2025

# 6. Run post-processing
python scripts/run_pipeline.py --skip-matcher --start 1984 --end 2025
```

If `conda activate py312` fails in PowerShell, run once:

```powershell
conda init powershell
```

Then close and reopen PowerShell.

## Fast Rerun After Files Are Downloaded

```bash
python scripts/run_matcher.py --start 1984 --end 2025 --skip-download
python scripts/run_pipeline.py --skip-matcher --start 1984 --end 2025
```

## Output

The matcher writes:

- `data/results/matches.csv`

The post-processing pipeline writes:

- `data/results/matches_state_filled.csv`
- `data/results/matches_with_uniform_cd_shifted.csv`
- `data/results/redistricting_analysis.csv`
- `data/results/redistricting_summary.txt`

## OSF Configuration

The package is already configured for the public OSF project:

```json
{ "_osf_project": "https://osf.io/eqsjw/" }
```

No `OSF_TOKEN` is needed for the public project. Tokens are still supported for
private mirrors:

```bash
export OSF_TOKEN=your_token_here
python scripts/setup_data.py
```

## UCLA Year-to-Congress Mapping

The matcher maps calendar years to UCLA files using:

```python
congress_num = (year - 1789) // 2 + 1
filename = f"districts{congress_num:03d}.zip"
```

Examples:

| Year | UCLA file |
|---:|---|
| 1984 | `districts098` |
| 1985-1986 | `districts099` |
| 2013-2014 | `districts113` |
| 2015-2016 | `districts114` |
| 2023-2024 | `districts118` |
| 2025 | `districts119` |

## Directory Layout

```text
cd-county-matcher-complete/
  src/
    __init__.py
    matcher.py
  scripts/
    setup_data.py
    run_matcher.py
    run_pipeline.py
    backfill_state.py
    add_uniform_cd.py
    analyze_redistricting.py
  osf_sources.json
  environment.yml
  requirements.txt
  LICENSE
  README.md
```

## Notes

The `data/` directory is intentionally not included in the zip because the
large shapefiles are fetched from OSF at runtime.

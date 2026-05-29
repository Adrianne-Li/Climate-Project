#!/usr/bin/env python3
"""
Pipeline step 1 of 3: backfill state_name in matches.csv.

The matcher's pre-2013 pipeline reads UCLA Congressional District shapefiles
that don't carry state info in any column `_standardize_columns` looks for. As
a result, ~94% of rows from 1984-2012 end up with an empty `state_name`, which
later gets dropped by the redistricting analysis.

This step derives state from the first two digits of `county_fips` (which IS
fully populated for every row) and writes a fixed copy. No re-running the
matcher required.

    python scripts/backfill_state.py
    python scripts/backfill_state.py --input data/results/matches.csv \
        --output data/results/matches_state_filled.csv --territory-policy drop

Input : <data-dir>/results/matches.csv          (raw matcher output)
Output: <data-dir>/results/matches_state_filled.csv
"""

import argparse
from pathlib import Path

import pandas as pd


# Identifier columns that must stay strings — otherwise pandas coerces
# zero-padded codes like county_fips "01003" / cd_geoid "0601" to floats
# (1003.0 / 601.0) on read, silently dropping the leading zeros the rest of
# the pipeline depends on.
ID_STR_COLS = [
    "cd_geoid", "cd_number", "county_fips", "state_fips", "cd_number_uniform",
]


def read_matches_csv(path):
    """read_csv that keeps identifier columns as strings."""
    return pd.read_csv(
        path, low_memory=False, dtype={c: str for c in ID_STR_COLS}
    )


# 50 states + DC — these are the rows we definitely want filled.
STATE_FIPS_MAP = {
    "01": "Alabama", "02": "Alaska", "04": "Arizona", "05": "Arkansas",
    "06": "California", "08": "Colorado", "09": "Connecticut", "10": "Delaware",
    "11": "District of Columbia", "12": "Florida", "13": "Georgia", "15": "Hawaii",
    "16": "Idaho", "17": "Illinois", "18": "Indiana", "19": "Iowa",
    "20": "Kansas", "21": "Kentucky", "22": "Louisiana", "23": "Maine",
    "24": "Maryland", "25": "Massachusetts", "26": "Michigan", "27": "Minnesota",
    "28": "Mississippi", "29": "Missouri", "30": "Montana", "31": "Nebraska",
    "32": "Nevada", "33": "New Hampshire", "34": "New Jersey", "35": "New Mexico",
    "36": "New York", "37": "North Carolina", "38": "North Dakota", "39": "Ohio",
    "40": "Oklahoma", "41": "Oregon", "42": "Pennsylvania", "44": "Rhode Island",
    "45": "South Carolina", "46": "South Dakota", "47": "Tennessee", "48": "Texas",
    "49": "Utah", "50": "Vermont", "51": "Virginia", "53": "Washington",
    "54": "West Virginia", "55": "Wisconsin", "56": "Wyoming",
}

# US territories.
TERRITORY_FIPS_MAP = {
    "60": "American Samoa",
    "66": "Guam",
    "69": "Northern Mariana Islands",
    "72": "Puerto Rico",
    "78": "US Virgin Islands",
}


def backfill_state_name(
    input_file: str,
    output_file: str,
    territory_policy: str = "fill",
):
    """Fill missing state_name from county_fips.

    territory_policy:
      'fill' -> label territory rows with their name (kept, no blanks).
      'drop' -> leave them NaN so downstream analysis drops them naturally.
    """
    print("=" * 78)
    print("BACKFILL state_name FROM county_fips")
    print("=" * 78)

    print(f"\nLoading {input_file}...")
    df = read_matches_csv(input_file)
    print(f"  {len(df):,} rows loaded")

    # Derive a clean 2-char state FIPS from county_fips. county_fips can come
    # through as float (1003.0), int (1003), or string ('01003' / '1003'),
    # so normalize aggressively.
    fips_str = (
        df["county_fips"]
        .astype(str)
        .str.split(".").str[0]            # strip any '.0' from float coercion
        .str.replace(r"\D", "", regex=True)  # drop any stray non-digits
        .str.zfill(5)                     # zero-pad to 5 digits
    )
    state_fips_2 = fips_str.str[:2]

    # Build the combined FIPS -> state name mapping per policy.
    full_map = dict(STATE_FIPS_MAP)
    if territory_policy == "fill":
        full_map.update(TERRITORY_FIPS_MAP)

    derived_state_name = state_fips_2.map(full_map)

    # Before/after audit.
    missing_before = df["state_name"].isna().sum()
    print(f"\n  Before: {missing_before:,} rows with missing state_name")

    # Only fill where currently missing; don't overwrite real matcher values.
    needs_fill = df["state_name"].isna()
    df.loc[needs_fill, "state_name"] = derived_state_name[needs_fill]

    # Also fill state_fips column if present but blank, since the uniform-CD
    # step downstream sometimes needs it.
    if "state_fips" not in df.columns:
        df["state_fips"] = state_fips_2
    else:
        needs_fips_fill = df["state_fips"].isna() | (
            df["state_fips"].astype(str) == ""
        )
        df.loc[needs_fips_fill, "state_fips"] = state_fips_2[needs_fips_fill]

    missing_after = df["state_name"].isna().sum()
    filled = missing_before - missing_after
    print(f"  Filled:  {filled:,} rows")
    print(f"  After:  {missing_after:,} rows still missing")

    if missing_after:
        leftover_fips = (
            state_fips_2[df["state_name"].isna()]
            .value_counts()
            .head(10)
        )
        print(
            "\n  Unresolved FIPS prefixes (probably territories if "
            "--territory-policy drop):"
        )
        for fips, n in leftover_fips.items():
            name = TERRITORY_FIPS_MAP.get(fips, "unknown")
            print(f"    {fips} ({name}): {n:,} rows")

    # Per-year coverage check (vectorized; works across pandas versions).
    print("\n  Missing state_name by year (after fill):")
    miss_by_year = (
        df["state_name"].isna().groupby(df["year"]).sum().astype(int)
    )
    total_by_year = df.groupby("year").size()
    for year in total_by_year.index:
        miss = int(miss_by_year.get(year, 0))
        total = int(total_by_year[year])
        marker = "" if miss == 0 else " <-- still missing"
        print(f"    {year}: {miss:>4} / {total:>5}{marker}")

    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    print(f"\nSaving to {output_file}...")
    df.to_csv(output_file, index=False)
    print("  Saved.")

    print("\n" + "=" * 78)
    print("DONE")
    print("=" * 78)
    print(f"\nNext: python scripts/add_uniform_cd.py --input {output_file}\n")

    return df


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument(
        "--input",
        default=None,
        help="Input CSV (default: <data-dir>/results/matches.csv)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV (default: <data-dir>/results/matches_state_filled.csv)",
    )
    parser.add_argument(
        "--territory-policy",
        choices=["fill", "drop"],
        default="fill",
        help="'fill' labels US-territory rows with their name; 'drop' leaves "
        "them blank so downstream analysis excludes them (default: fill)",
    )
    args = parser.parse_args()

    results_dir = Path(args.data_dir) / "results"
    input_file = args.input or str(results_dir / "matches.csv")
    output_file = args.output or str(results_dir / "matches_state_filled.csv")

    backfill_state_name(input_file, output_file, args.territory_policy)


if __name__ == "__main__":
    main()

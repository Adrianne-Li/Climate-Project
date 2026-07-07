#!/usr/bin/env python3
"""
Pipeline step 2 of 3: add a uniform CD number + optionally apply a year shift.

Combines two corrections:

1. Derives `cd_number_uniform` from `cd_number` / `cd_geoid` so every year
   uses the same CD identifier scheme (1984-2012 from cd_number, 2013+ from
   the last two digits of cd_geoid). At-large districts stay as '0'.

2. Optionally shifts every row's `year`. With the updated UCLA-based
   year-to-Congress mapping, the default is now 0 because the input year is
   already aligned to the congressional cycle shown in the UCLA table.
   Set `--year-shift -1` only if a downstream analysis intentionally wants
   the old backward-shift convention.

   IMPORTANT: the uniform CD extraction runs on the ORIGINAL year label.
   Any optional shifting happens after.

    python scripts/add_uniform_cd.py
    python scripts/add_uniform_cd.py --input data/results/matches_state_filled.csv \
        --output data/results/matches_with_uniform_cd_shifted.csv --year-shift 0

Input : <data-dir>/results/matches_state_filled.csv  (from backfill_state.py)
Output: <data-dir>/results/matches_with_uniform_cd_shifted.csv
"""

import argparse
from pathlib import Path

import pandas as pd


# Keep zero-padded identifier codes as strings (see backfill_state.py).
ID_STR_COLS = [
    "cd_geoid", "cd_number", "county_fips", "state_fips", "cd_number_uniform",
]


def read_matches_csv(path):
    return pd.read_csv(
        path, low_memory=False, dtype={c: str for c in ID_STR_COLS}
    )


def extract_uniform_cd_number(row):
    """Extract a uniform CD number from cd_number / cd_geoid.

    NOTE: `row['year']` here is the ORIGINAL (unshifted) year. This matters
    because the storage convention — cd_number for 1984-2012, cd_geoid-derived
    for 2013+ — is tied to the original year labels, not the corrected ones.
    """
    year = row["year"]
    cd_number = str(row["cd_number"]) if pd.notna(row["cd_number"]) else ""
    cd_geoid = str(row["cd_geoid"]) if pd.notna(row["cd_geoid"]) else ""

    if year <= 2012:
        # 1984-2012: cd_number already contains the district number.
        # Strip leading zeros; at-large stays as '0'.
        return cd_number.lstrip("0") if cd_number.lstrip("0") else "0"
    else:
        # 2013+: cd_geoid is state FIPS + CD number. Last two digits are CD.
        if len(cd_geoid) >= 2:
            cd_from_geoid = cd_geoid[-2:].lstrip("0")
            return cd_from_geoid if cd_from_geoid else "0"
        # Fallback when cd_geoid is missing.
        return cd_number.lstrip("0") if cd_number.lstrip("0") else "0"


def add_uniform_cd_and_shift(
    input_file: str,
    output_file: str,
    year_shift: int = 0,
):
    print("=" * 80)
    print("UNIFORM CD NUMBER + OPTIONAL YEAR SHIFT")
    print("=" * 80)

    print(f"\nLoading {input_file}...")
    df = read_matches_csv(input_file)
    print(
        f"  {len(df):,} rows, original year range: "
        f"{df['year'].min()} - {df['year'].max()}"
    )

    # --- Step 1: derive uniform CD number from the ORIGINAL year -----------
    print("\nStep 1: deriving cd_number_uniform from original year labels...")
    df["cd_number_uniform"] = df.apply(extract_uniform_cd_number, axis=1)
    print("  Done.")

    # --- Step 2: preserve original year, then shift ------------------------
    print(f"\nStep 2: shifting year by {year_shift} (glitch correction)...")
    df["year_original"] = df["year"]
    df["year"] = df["year"] + year_shift
    print(f"  Corrected year range: {df['year'].min()} - {df['year'].max()}")

    # --- Reorder columns ----------------------------------------------------
    cols = df.columns.tolist()
    cols.remove("cd_number_uniform")
    cols.remove("year_original")

    if "cd_geoid" in cols:
        cols.insert(cols.index("cd_geoid") + 1, "cd_number_uniform")
    else:
        cols.append("cd_number_uniform")

    if "year" in cols:
        cols.insert(cols.index("year") + 1, "year_original")
    else:
        cols.append("year_original")

    df = df[cols]

    # --- Save ---------------------------------------------------------------
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    print(f"\nSaving to {output_file}...")
    df.to_csv(output_file, index=False)
    print("  Saved.")

    # --- Summary ------------------------------------------------------------
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total rows                : {len(df):,}")
    print(
        f"Original years (input)    : "
        f"{df['year_original'].min()} - {df['year_original'].max()}"
    )
    print(
        f"Corrected years (output)  : "
        f"{df['year'].min()} - {df['year'].max()}"
    )
    print(f"Unique uniform CD numbers : {df['cd_number_uniform'].nunique()}")

    print("\nSample rows showing the shift (one per original-year milestone):")
    milestones = [1984, 2000, 2012, 2013, 2020, 2025]
    for y in milestones:
        hit = df[df["year_original"] == y]
        if len(hit) == 0:
            continue
        sample = hit[
            ["year_original", "year", "state_name", "cd_number",
             "cd_geoid", "cd_number_uniform"]
        ].head(1)
        print(f"\nOriginal year {y}:")
        print(sample.to_string(index=False))

    print(
        "\nDone. Next: python scripts/analyze_redistricting.py "
        f"--input {output_file}"
    )

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
        help="Input CSV (default: <data-dir>/results/matches_state_filled.csv)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output CSV (default: "
        "<data-dir>/results/matches_with_uniform_cd_shifted.csv)",
    )
    parser.add_argument(
        "--year-shift",
        type=int,
        default=0,
        help="Integer year shift applied after uniform CD extraction (default: 0)",
    )
    args = parser.parse_args()

    results_dir = Path(args.data_dir) / "results"
    input_file = args.input or str(results_dir / "matches_state_filled.csv")
    output_file = args.output or str(
        results_dir / "matches_with_uniform_cd_shifted.csv"
    )

    add_uniform_cd_and_shift(input_file, output_file, args.year_shift)


if __name__ == "__main__":
    main()

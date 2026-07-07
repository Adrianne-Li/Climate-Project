#!/usr/bin/env python3
"""
Pipeline step 3 of 3: congressional district redistricting analysis.

Analyzes congressional district changes over time to identify redistricting
events, using the uniform-CD matches file produced by add_uniform_cd.py.

Input columns expected:
  - year              (aligned analysis year)
  - year_original     (pre-shift year; optional, carried through)
  - cd_number_uniform (consistent CD identifier across all years)
  - state_name, county_name, county_fips, pct_cd_in_county

Outputs:
  - redistricting_analysis.csv  (State, District, Year, Relevant Counties,
                                 District Same?)
  - redistricting_summary.txt   (human-readable report)

Redistricting detection:
  - For each (state, district), compare county composition year-over-year.
  - total_change = removed-county % + added-county % + |retained shift %|.
  - If total_change > THRESHOLD, flag as redistricting (District Same? = 1).

With the updated UCLA mapping and the default year_shift=0, post-census
redistricting spikes should appear in the years implied by the selected
analysis-year convention.

    python scripts/analyze_redistricting.py
    python scripts/analyze_redistricting.py \
        --input data/results/matches_with_uniform_cd_shifted.csv \
        --output-csv data/results/redistricting_analysis.csv \
        --summary data/results/redistricting_summary.txt --threshold 20
"""

import argparse
from pathlib import Path

import pandas as pd


# Keep zero-padded identifier codes as strings (see backfill_state.py).
ID_STR_COLS = [
    "cd_geoid", "cd_number", "county_fips", "state_fips", "cd_number_uniform",
]


# ---------------------------------------------------------------------------
# Data loading & preparation
# ---------------------------------------------------------------------------
def load_and_prepare_data(matches_file):
    """Load the shifted matches CSV and normalize types."""
    print("Loading matches data...")
    matches = pd.read_csv(
        matches_file, low_memory=False, dtype={c: str for c in ID_STR_COLS}
    )

    if "cd_number_uniform" in matches.columns:
        cd_col = "cd_number_uniform"
        print("Using 'cd_number_uniform' column for analysis")
    else:
        cd_col = "cd_number"
        print(
            "[warn] 'cd_number_uniform' not found — falling back to "
            "'cd_number'. Run add_uniform_cd.py first for consistent "
            "cross-year district identification."
        )

    # Sanity-check that the year shift has already been applied.
    has_year_original = "year_original" in matches.columns
    max_year = matches["year"].max()
    if not has_year_original and max_year >= 2025:
        print(
            "[warn] This file doesn't look year-shifted (max year is "
            f"{max_year} and 'year_original' is missing). If you intended to "
            "use shifted data, run add_uniform_cd.py first."
        )
    elif has_year_original:
        print(
            f"Year shift confirmed: original {matches['year_original'].min()}"
            f"-{matches['year_original'].max()} -> corrected "
            f"{matches['year'].min()}-{matches['year'].max()}"
        )

    # Drop rows missing state or CD.
    initial_rows = len(matches)
    matches = matches[
        matches["state_name"].notna() & matches[cd_col].notna()
    ].copy()
    removed = initial_rows - len(matches)
    if removed:
        print(f"Removed {removed} rows with missing state_name or {cd_col}")

    # Ensure we always have cd_number_uniform, even if we fell back above.
    if "cd_number_uniform" not in matches.columns:
        matches["cd_number_uniform"] = matches[cd_col]

    # Normalize CD number type: string -> strip -> drop trailing ".0" ->
    # numeric. Keeps "1" and "1.0" from being treated as different districts.
    matches["cd_number_uniform"] = (
        matches["cd_number_uniform"].astype(str).str.strip()
    )
    matches["cd_number_uniform"] = matches["cd_number_uniform"].str.replace(
        r"\.0$", "", regex=True
    )
    try:
        matches["cd_number_uniform"] = pd.to_numeric(
            matches["cd_number_uniform"], errors="coerce"
        )
        matches["cd_number_uniform"] = (
            matches["cd_number_uniform"].fillna(0).astype(int)
        )
        print("Converted cd_number_uniform to integer type")
    except Exception as e:
        print(f"Keeping CD numbers as string: {e}")

    print(f"\nData loaded: {len(matches):,} rows")
    print(
        f"Year range (corrected): {matches['year'].min()} - "
        f"{matches['year'].max()}"
    )
    print(f"States: {matches['state_name'].nunique()}")
    print(f"Unique districts: {matches['cd_number_uniform'].nunique()}")
    print(f"CD number dtype: {matches['cd_number_uniform'].dtype}")

    print("\nSample (first 10 unique state-district-year combos):")
    sample = (
        matches[["state_name", "cd_number_uniform", "year"]]
        .drop_duplicates()
        .head(10)
    )
    print(sample.to_string(index=False))

    return matches


# ---------------------------------------------------------------------------
# Aggregation & change detection
# ---------------------------------------------------------------------------
def format_county_with_percentage(county_name, pct):
    return f"{county_name} ({pct:.2f}%)"


def aggregate_by_district_year(matches):
    """Collapse to one row per (state, district, corrected year) with the
    full list of counties and their percentages."""
    print("\nAggregating by state-district-year (using corrected year)...")

    grouped = (
        matches.groupby(["state_name", "cd_number_uniform", "year"])
        .agg(
            {
                "county_name": list,
                "county_fips": list,
                "pct_cd_in_county": list,
            }
        )
        .reset_index()
    )
    grouped = grouped.sort_values(
        ["state_name", "cd_number_uniform", "year"]
    ).reset_index(drop=True)

    print(f"Total state-district-year combinations: {len(grouped):,}")
    print("\nFirst 5 rows of aggregated data:")
    print(grouped[["state_name", "cd_number_uniform", "year"]].head())

    return grouped


def calculate_change_percentage(current_row, previous_row):
    """Sum of removed %, added %, and retained-but-shifted % between years."""
    curr_counties = set(current_row["county_fips"])
    prev_counties = set(previous_row["county_fips"])

    curr_pct = dict(
        zip(current_row["county_fips"], current_row["pct_cd_in_county"])
    )
    prev_pct = dict(
        zip(previous_row["county_fips"], previous_row["pct_cd_in_county"])
    )

    total = 0.0
    # Removed counties contribute their full previous share.
    for c in prev_counties - curr_counties:
        total += prev_pct[c]
    # Added counties contribute their full current share.
    for c in curr_counties - prev_counties:
        total += curr_pct[c]
    # Retained counties contribute their absolute shift.
    for c in curr_counties & prev_counties:
        total += abs(curr_pct[c] - prev_pct[c])

    return total


def detect_redistricting(current_row, previous_row, threshold):
    """Return 1 if redistricting, 0 if stable, None for baseline year."""
    if previous_row is None:
        return None
    return (
        1
        if calculate_change_percentage(current_row, previous_row) > threshold
        else 0
    )


def create_redistricting_analysis(grouped, threshold):
    """Walk each (state, district) timeline and flag changes."""
    print(f"\nDetecting redistricting events (threshold: {threshold}%)...")

    results = []
    for (state, district), group in grouped.groupby(
        ["state_name", "cd_number_uniform"]
    ):
        group = group.sort_values("year").reset_index(drop=True)

        for idx, row in group.iterrows():
            if idx == 0:
                district_same = None  # baseline year
            else:
                prev_row = group.iloc[idx - 1]
                district_same = detect_redistricting(row, prev_row, threshold)

            counties_fmt = [
                format_county_with_percentage(n, p)
                for n, p in zip(row["county_name"], row["pct_cd_in_county"])
            ]
            # Alphabetical sort up front — saves a downstream sort script.
            counties_fmt = sorted(counties_fmt, key=lambda s: s.lower())

            results.append(
                {
                    "State": state,
                    "District": district,
                    "Year": int(row["year"]),
                    "Relevant Counties": ", ".join(counties_fmt),
                    "District Same?": district_same,
                }
            )

    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values(
        ["State", "District", "Year"]
    ).reset_index(drop=True)

    total = len(result_df)
    baseline = result_df["District Same?"].isna().sum()
    stable = (result_df["District Same?"] == 0).sum()
    redistricted = (result_df["District Same?"] == 1).sum()

    print("\nAnalysis complete.")
    print(f"  Total records       : {total:,}")
    print(f"  Baseline years      : {baseline:,}")
    print(f"  Stable years        : {stable:,}")
    print(f"  Redistricting events: {redistricted:,}")

    return result_df


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------
def generate_summary_report(result_df, output_file, threshold):
    print("\nGenerating summary report...")
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        f.write("=" * 80 + "\n")
        f.write("CONGRESSIONAL DISTRICT REDISTRICTING ANALYSIS (YEAR-SHIFTED)\n")
        f.write("=" * 80 + "\n\n")

        f.write("METHODOLOGY:\n")
        f.write("-" * 80 + "\n")
        f.write(
            "- Year labels corrected by the configured shift to fix the\n"
            "  data-storage glitch in the original matches file (geometry\n"
            "  stored under year Y actually reflects the session in year Y-1).\n"
            "- cd_number_uniform gives a consistent district ID across the\n"
            "  entire time series (1984-2012 from cd_number, 2013+ from\n"
            "  the last 2 digits of cd_geoid).\n"
            f"- Threshold: >{threshold}% change in district composition\n"
            "  triggers a redistricting flag.\n"
            "- Change = removed-county % + added-county % + |retained %|.\n"
            "- District Same? codes: 0 stable, 1 redistricted, NaN baseline.\n\n"
        )

        f.write("OVERALL STATISTICS:\n")
        f.write("-" * 80 + "\n")
        f.write(f"Total records: {len(result_df):,}\n")
        f.write(
            f"Time period (corrected): {result_df['Year'].min()} - "
            f"{result_df['Year'].max()}\n"
        )
        f.write(f"States analyzed: {result_df['State'].nunique()}\n")
        f.write(
            "Unique state-district combinations: "
            f"{result_df[['State', 'District']].drop_duplicates().shape[0]}\n\n"
        )

        baseline = result_df["District Same?"].isna().sum()
        stable = (result_df["District Same?"] == 0).sum()
        redistricted = (result_df["District Same?"] == 1).sum()
        total = len(result_df)
        f.write(
            f"Baseline years (no comparison): {baseline:,} "
            f"({baseline/total*100:.1f}%)\n"
        )
        f.write(f"Stable years: {stable:,} ({stable/total*100:.1f}%)\n")
        f.write(
            f"Redistricting events: {redistricted:,} "
            f"({redistricted/total*100:.1f}%)\n\n"
        )

        f.write("REDISTRICTING EVENTS BY YEAR (Top 15):\n")
        f.write("-" * 80 + "\n")
        by_year = (
            result_df[result_df["District Same?"] == 1]
            .groupby("Year")
            .size()
            .sort_values(ascending=False)
        )
        for year, count in by_year.head(15).items():
            f.write(f"  {year}: {count:>4} districts\n")

        f.write(
            "\nNOTE: After the year shift, post-census redistricting spikes\n"
            "should fall on 1991, 2001, 2011, and 2021 — the years\n"
            "immediately following the decennial censuses.\n\n"
        )

        f.write("STATES WITH MOST REDISTRICTING ACTIVITY (Top 20):\n")
        f.write("-" * 80 + "\n")
        by_state = (
            result_df[result_df["District Same?"] == 1]
            .groupby("State")
            .size()
            .sort_values(ascending=False)
        )
        for state, count in by_state.head(20).items():
            f.write(f"  {state:<25} {count:>4} events\n")

    print(f"Summary report saved to: {output_file}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def run_analysis(input_file, output_csv, summary_file, threshold):
    print("=" * 80)
    print("CONGRESSIONAL DISTRICT REDISTRICTING ANALYSIS (YEAR-SHIFTED)")
    print("=" * 80)

    matches = load_and_prepare_data(input_file)
    grouped = aggregate_by_district_year(matches)
    result_df = create_redistricting_analysis(grouped, threshold=threshold)

    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    print(f"\nSaving results to: {output_csv}")
    result_df.to_csv(output_csv, index=False)

    generate_summary_report(result_df, summary_file, threshold)

    print("\n" + "=" * 80)
    print("SAMPLE RESULTS (first 20 rows):")
    print("=" * 80)
    sample = result_df.head(20).copy()
    sample["Relevant Counties"] = sample["Relevant Counties"].apply(
        lambda x: x[:60] + "..." if len(x) > 60 else x
    )
    print(sample.to_string(index=False))

    print("\n" + "=" * 80)
    print("DONE")
    print("=" * 80)
    print("\nOutput files:")
    print(f"  1. {output_csv}")
    print(f"  2. {summary_file}")

    return result_df


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--data-dir", default="./data")
    parser.add_argument(
        "--input",
        default=None,
        help="Input CSV (default: "
        "<data-dir>/results/matches_with_uniform_cd_shifted.csv)",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Output CSV (default: "
        "<data-dir>/results/redistricting_analysis.csv)",
    )
    parser.add_argument(
        "--summary",
        default=None,
        help="Summary text file (default: "
        "<data-dir>/results/redistricting_summary.txt)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=20.0,
        help="Percent-change threshold for flagging redistricting. The "
        "original used 20.0; use 10.0 for a stricter definition (default: 20).",
    )
    args = parser.parse_args()

    results_dir = Path(args.data_dir) / "results"
    input_file = args.input or str(
        results_dir / "matches_with_uniform_cd_shifted.csv"
    )
    output_csv = args.output_csv or str(
        results_dir / "redistricting_analysis.csv"
    )
    summary_file = args.summary or str(
        results_dir / "redistricting_summary.txt"
    )

    run_analysis(input_file, output_csv, summary_file, args.threshold)


if __name__ == "__main__":
    main()

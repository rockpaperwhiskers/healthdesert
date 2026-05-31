"""Census data retrieval and processing for healthcare desert analysis."""

import logging
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from census import Census

import config

logger = logging.getLogger(__name__)

# ── ACS 5-year variable definitions ───────────────────────────────────────────

ELDERLY_MALE_VARS = [
    "B01001_020E", "B01001_021E", "B01001_022E",
    "B01001_023E", "B01001_024E", "B01001_025E",
]
ELDERLY_FEMALE_VARS = [
    "B01001_044E", "B01001_045E", "B01001_046E",
    "B01001_047E", "B01001_048E", "B01001_049E",
]
UNINSURED_MALE_VARS = [
    "B27001_005E", "B27001_008E", "B27001_011E",
    "B27001_014E", "B27001_017E", "B27001_020E",
    "B27001_023E", "B27001_026E", "B27001_029E",
]
UNINSURED_FEMALE_VARS = [
    "B27001_033E", "B27001_036E", "B27001_039E",
    "B27001_042E", "B27001_045E", "B27001_048E",
    "B27001_051E", "B27001_054E", "B27001_057E",
]
BASE_VARS = [
    "B01003_001E",  # Total population
    "B17001_002E",  # Population below poverty line
    "B08201_002E",  # Households with no vehicle
    "B08201_001E",  # Total households
]

ALL_VARS = (
    BASE_VARS
    + ELDERLY_MALE_VARS
    + ELDERLY_FEMALE_VARS
    + UNINSURED_MALE_VARS
    + UNINSURED_FEMALE_VARS
)


def _replace_missing(df: pd.DataFrame) -> pd.DataFrame:
    """Replace the Census missing-data sentinel (-666666666) with NaN.

    Args:
        df: DataFrame potentially containing sentinel values.

    Returns:
        DataFrame with sentinels replaced by NaN.
    """
    return df.replace(config.CENSUS_MISSING, np.nan)


def fetch_census_data(
    state_fips: str = config.STATE_FIPS,
    county_fips: str = config.COUNTY_FIPS,
    year: int = config.ACS_YEAR,
    use_cache: bool = True,
    refresh_cache: bool = False,
) -> pd.DataFrame:
    """Fetch ACS 5-year estimates at the census tract level.

    Args:
        state_fips: Two-digit state FIPS code (e.g., "17" for Illinois).
        county_fips: Three-digit county FIPS code, or "*" for all counties.
        year: ACS end year to query (e.g., 2022 = 2018–2022 5-year estimates).
        use_cache: Load from disk cache when available.
        refresh_cache: Force re-download even when cached data exists.

    Returns:
        DataFrame of raw ACS variables with string-typed FIPS columns.

    Raises:
        ValueError: If CENSUS_API_KEY is not set in the environment.
        RuntimeError: If the Census API returns an error or empty response.
    """
    raw_path = config.RAW_DIR / f"census_raw_{state_fips}_{county_fips}_{year}.csv"

    if use_cache and not refresh_cache and raw_path.exists():
        logger.info("Loading cached raw Census data from %s", raw_path)
        return pd.read_csv(
            raw_path,
            dtype={"state": str, "county": str, "tract": str},
        )

    if not config.CENSUS_API_KEY:
        raise ValueError(
            "CENSUS_API_KEY is not set. "
            "Get a free key at https://api.census.gov/data/key_signup.html "
            "then add it to your .env file."
        )

    logger.info(
        "Fetching ACS %d 5-year data for state=%s county=%s",
        year, state_fips, county_fips,
    )

    try:
        c = Census(config.CENSUS_API_KEY, year=year)
        county_arg = Census.ALL if county_fips == "*" else county_fips
        data = c.acs5.state_county_tract(
            fields=ALL_VARS,
            state_fips=state_fips,
            county_fips=county_arg,
            tract=Census.ALL,
        )
    except Exception as exc:
        raise RuntimeError(f"Census API request failed: {exc}") from exc

    if not data:
        raise RuntimeError(
            f"Census API returned an empty response for "
            f"state={state_fips}, county={county_fips}"
        )

    df = pd.DataFrame(data)
    df = _replace_missing(df)

    # Zero-pad FIPS codes to standard widths
    df["state"] = df["state"].astype(str).str.zfill(2)
    df["county"] = df["county"].astype(str).str.zfill(3)
    df["tract"] = df["tract"].astype(str).str.zfill(6)

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(raw_path, index=False)
    logger.info("Saved raw Census data to %s (%d tracts)", raw_path, len(df))

    return df


def compute_vulnerability_vars(df: pd.DataFrame) -> pd.DataFrame:
    """Derive vulnerability percentage columns from raw ACS data.

    Computes pct_elderly, pct_uninsured, pct_poverty, and pct_no_vehicle,
    clipping results to [0, 1] and dropping zero-population tracts.

    Args:
        df: Raw ACS DataFrame produced by fetch_census_data.

    Returns:
        DataFrame with added GEOID and pct_* columns.
    """
    df = df.copy()

    # 11-digit GEOID: state(2) + county(3) + tract(6)
    df["GEOID"] = (
        df["state"].astype(str).str.zfill(2)
        + df["county"].astype(str).str.zfill(3)
        + df["tract"].astype(str).str.zfill(6)
    )

    # Coerce all ACS columns to numeric and re-apply sentinel replacement
    num_cols = [c for c in ALL_VARS if c in df.columns]
    df[num_cols] = df[num_cols].apply(pd.to_numeric, errors="coerce")
    df = _replace_missing(df)

    total_pop = df["B01003_001E"]
    total_hh = df["B08201_001E"]

    # Elderly 65+ (male + female age buckets)
    elderly_cols = [c for c in ELDERLY_MALE_VARS + ELDERLY_FEMALE_VARS if c in df.columns]
    df["elderly_pop"] = df[elderly_cols].sum(axis=1)
    df["pct_elderly"] = df["elderly_pop"] / total_pop

    # Uninsured (male + female age buckets)
    uninsured_cols = [c for c in UNINSURED_MALE_VARS + UNINSURED_FEMALE_VARS if c in df.columns]
    df["uninsured_pop"] = df[uninsured_cols].sum(axis=1)
    df["pct_uninsured"] = df["uninsured_pop"] / total_pop

    # Below poverty line
    df["poverty_pop"] = pd.to_numeric(df.get("B17001_002E"), errors="coerce")
    df["pct_poverty"] = df["poverty_pop"] / total_pop

    # No-vehicle households
    df["no_vehicle_hh"] = pd.to_numeric(df.get("B08201_002E"), errors="coerce")
    df["pct_no_vehicle"] = df["no_vehicle_hh"] / total_hh

    # Clip to valid percentage range
    pct_cols = ["pct_elderly", "pct_uninsured", "pct_poverty", "pct_no_vehicle"]
    for col in pct_cols:
        df[col] = df[col].clip(0.0, 1.0)

    # Drop tracts with no population (they produce meaningless percentages)
    n_before = len(df)
    df = df[total_pop.fillna(0) > 0].copy()
    dropped = n_before - len(df)
    if dropped:
        logger.warning("Dropped %d zero-population tracts", dropped)

    return df


def save_processed_census(
    df: pd.DataFrame,
    state_fips: str,
    county_fips: str,
) -> Path:
    """Write processed Census DataFrame to the processed data directory.

    Args:
        df: DataFrame with vulnerability columns and GEOID.
        state_fips: State FIPS code (used in filename).
        county_fips: County FIPS code (used in filename).

    Returns:
        Path to the saved CSV file.
    """
    path = config.PROCESSED_DIR / f"census_processed_{state_fips}_{county_fips}.csv"
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    logger.info("Saved processed Census data to %s", path)
    return path


def load_census_data(
    state_fips: str = config.STATE_FIPS,
    county_fips: str = config.COUNTY_FIPS,
    refresh_cache: bool = False,
) -> pd.DataFrame:
    """Top-level entry point: load Census data, processing if not cached.

    On the first call this fetches from the API, derives vulnerability
    variables, and writes both raw and processed CSVs. Subsequent calls
    load the processed CSV from disk.

    Args:
        state_fips: State FIPS code.
        county_fips: County FIPS code or "*" for all counties.
        refresh_cache: Force full re-download and reprocessing.

    Returns:
        Processed Census DataFrame with GEOID and pct_* columns.
    """
    processed_path = (
        config.PROCESSED_DIR / f"census_processed_{state_fips}_{county_fips}.csv"
    )

    if not refresh_cache and processed_path.exists():
        logger.info("Loading processed Census data from %s", processed_path)
        return pd.read_csv(
            processed_path,
            dtype={"state": str, "county": str, "tract": str, "GEOID": str},
        )

    raw_df = fetch_census_data(
        state_fips=state_fips,
        county_fips=county_fips,
        refresh_cache=refresh_cache,
    )
    processed_df = compute_vulnerability_vars(raw_df)
    save_processed_census(processed_df, state_fips, county_fips)

    return processed_df

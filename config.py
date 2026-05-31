"""Project-wide configuration for healthcare desert analysis."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Project layout ────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
MAPS_DIR = OUTPUTS_DIR / "maps"
REPORTS_DIR = OUTPUTS_DIR / "reports"

# ── Study area ────────────────────────────────────────────────────────────────
STATE_FIPS = "32"      # Nevada
STATE_NAME = "Nevada"
COUNTY_FIPS = "*"    # Clark County; use "*" for all counties in the state

# ── API keys ──────────────────────────────────────────────────────────────────
CENSUS_API_KEY: str = os.environ.get("CENSUS_API_KEY", "")
ORS_API_KEY: str = os.environ.get("ORS_API_KEY", "")

# ── ACS settings ──────────────────────────────────────────────────────────────
ACS_YEAR = 2022

# ── Drive-time thresholds (minutes) ───────────────────────────────────────────
DRIVE_TIMES_MINUTES = [15, 30, 60]

# ── Coordinate reference systems ──────────────────────────────────────────────
GEO_CRS = "EPSG:4326"        # WGS 84 geographic — for storage / API calls
PROJECTED_CRS = "EPSG:3857"  # Web Mercator — for distance / buffer operations

# ── Vulnerability index weights (must sum to 1.0) ─────────────────────────────
VULNERABILITY_WEIGHTS: dict[str, float] = {
    "pct_elderly": 0.25,
    "pct_uninsured": 0.30,
    "pct_poverty": 0.25,
    "pct_no_vehicle": 0.20,
}

# ── Desert classification threshold ───────────────────────────────────────────
# Q4 vulnerability AND outside this isochrone → "Desert"
DESERT_ISOCHRONE_MINUTES = 30

# ── Euclidean buffer fallback distances (meters) ─────────────────────────────
EUCLIDEAN_BUFFERS: dict[int, int] = {
    15: 12_000,
    30: 25_000,
    60: 50_000,
}

# ── Census missing-data sentinel ──────────────────────────────────────────────
CENSUS_MISSING = -666_666_666

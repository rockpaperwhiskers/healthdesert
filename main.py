"""Healthcare desert analysis pipeline — top-level orchestration."""

import argparse
import logging
import sys
import time
from typing import Any, Tuple

from dotenv import load_dotenv

load_dotenv()

import config
from src.census_data import load_census_data
from src.facilities import load_facilities
from src.isochrones import (
    generate_isochrones,
    _build_euclidean_buffers,
    _filter_facilities_to_study_area,
)
from src.vulnerability_index import load_vulnerability_index
from src.visualization import (
    create_interactive_map,
    generate_summary_report,
    plot_vulnerability_choropleth,
)


def _configure_logging() -> None:
    """Set up root logger with console and file handlers."""
    fmt = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("pipeline.log"),
        ],
    )


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for pipeline configuration.

    Returns:
        Namespace with county, skip_isochrones, and refresh_cache attributes.
    """
    parser = argparse.ArgumentParser(
        description="Healthcare Desert Analysis Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--county",
        type=str,
        default=config.COUNTY_FIPS,
        help='County FIPS code (e.g. "031") or "*" for all counties in the state',
    )
    parser.add_argument(
        "--skip-isochrones",
        action="store_true",
        help="Force Euclidean buffer fallback — useful when no ORS API key is available",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Re-download all source data, ignoring any cached files",
    )
    return parser.parse_args()


def run_step(name: str, fn, *args, **kwargs) -> Tuple[Any, bool]:
    """Execute a pipeline step, logging timing and catching exceptions.

    Args:
        name: Human-readable step label for log output.
        fn: Callable to execute.
        *args: Positional arguments forwarded to fn.
        **kwargs: Keyword arguments forwarded to fn.

    Returns:
        Tuple of (return_value, success_flag). return_value is None on failure.
    """
    logger = logging.getLogger(__name__)
    logger.info("=== Starting: %s ===", name)
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        logger.info("=== Completed: %s (%.1fs) ===", name, time.perf_counter() - t0)
        return result, True
    except Exception as exc:
        logger.error(
            "=== FAILED: %s — %s ===", name, exc, exc_info=True
        )
        return None, False


def main() -> int:
    """Run the full analysis pipeline.

    Returns:
        0 on full success, 1 if any step encountered an error.
    """
    _configure_logging()
    logger = logging.getLogger(__name__)

    args = _parse_args()
    county = args.county
    refresh = args.refresh_cache

    logger.info(
        "Pipeline start — state=%s (%s)  county=%s  skip_isochrones=%s  refresh=%s",
        config.STATE_NAME, config.STATE_FIPS, county,
        args.skip_isochrones, refresh,
    )

    any_failed = False

    # ── Step 1: Census data ───────────────────────────────────────────────────
    census_df, ok = run_step(
        "Census Data",
        load_census_data,
        state_fips=config.STATE_FIPS,
        county_fips=county,
        refresh_cache=refresh,
    )
    if not ok:
        logger.critical("Cannot continue without census data")
        return 1

    # ── Step 2: Healthcare facilities ─────────────────────────────────────────
    facilities, ok = run_step(
        "Healthcare Facilities",
        load_facilities,
        refresh_cache=refresh,
    )
    if not ok:
        logger.critical("Cannot continue without facility data")
        return 1

    # ── Step 3: Drive-time isochrones ─────────────────────────────────────────
    isochrones, ok = run_step(
        "Drive-Time Isochrones",
        generate_isochrones,
        facilities=facilities,
        drive_times=config.DRIVE_TIMES_MINUTES,
        state_fips=config.STATE_FIPS,
        county_fips=county,
        refresh_cache=refresh,
        use_euclidean_fallback=args.skip_isochrones,
    )
    if not ok:
        logger.warning("Isochrone step failed — falling back to local Euclidean buffers")
        local_fac = _filter_facilities_to_study_area(
            facilities, config.STATE_FIPS, county, max(config.DRIVE_TIMES_MINUTES)
        )
        isochrones = _build_euclidean_buffers(local_fac, config.DRIVE_TIMES_MINUTES)

    # ── Step 4: Vulnerability index ───────────────────────────────────────────
    final_tracts, ok = run_step(
        "Vulnerability Index",
        load_vulnerability_index,
        census_df=census_df,
        facilities=facilities,
        isochrones=isochrones,
        state_fips=config.STATE_FIPS,
        county_fips=county,
        refresh_cache=refresh,
    )
    if not ok:
        logger.critical("Cannot produce outputs without a vulnerability index")
        return 1

    # ── Step 5: Visualisations ────────────────────────────────────────────────
    _, v1_ok = run_step(
        "Vulnerability Choropleth Map",
        plot_vulnerability_choropleth,
        tracts=final_tracts,
        facilities=facilities,
        isochrones=isochrones,
    )
    if not v1_ok:
        any_failed = True

    _, v2_ok = run_step(
        "Interactive Folium Map",
        create_interactive_map,
        tracts=final_tracts,
        facilities=facilities,
        isochrones=isochrones,
    )
    if not v2_ok:
        any_failed = True

    _, v3_ok = run_step(
        "Summary Report",
        generate_summary_report,
        tracts=final_tracts,
    )
    if not v3_ok:
        any_failed = True

    if any_failed:
        logger.warning("Pipeline completed with errors — check pipeline.log for details")
        return 1

    logger.info("Pipeline completed successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Drive-time isochrone generation via OpenRouteService with buffer fallback."""

import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import geopandas as gpd
from shapely.geometry import shape
from shapely.ops import unary_union

import config

logger = logging.getLogger(__name__)

_ORS_BATCH_SIZE = 5      # ORS free tier max locations per request
_BATCH_DELAY_SEC = 1.0   # polite delay between batches
_MAX_RETRIES = 4
_BACKOFF_BASE = 2.0      # seconds; wait = base^attempt


# ── Cache path helpers ────────────────────────────────────────────────────────

def _cache_path(minutes: int, state_fips: str, county_fips: str) -> Path:
    """Return the county-specific GeoJSON path for one isochrone layer."""
    return (
        config.PROCESSED_DIR
        / f"isochrone_{state_fips}_{county_fips}_{minutes}min.geojson"
    )


def _load_isochrone_cache(
    drive_times: List[int],
    state_fips: str = config.STATE_FIPS,
    county_fips: str = config.COUNTY_FIPS,
) -> Dict[int, gpd.GeoDataFrame]:
    """Load dissolved isochrone layers from disk if all expected files exist.

    Args:
        drive_times: Drive time values (minutes) expected on disk.
        state_fips: State FIPS code used in the filename.
        county_fips: County FIPS code used in the filename.

    Returns:
        Dict mapping minutes → GeoDataFrame, or empty dict if any file is missing.
    """
    result: Dict[int, gpd.GeoDataFrame] = {}
    for minutes in drive_times:
        path = _cache_path(minutes, state_fips, county_fips)
        if not path.exists():
            return {}
        result[minutes] = gpd.read_file(path)
        logger.info("Loaded cached %d-min isochrone from %s", minutes, path)
    return result


def _save_isochrones(
    isochrones: Dict[int, gpd.GeoDataFrame],
    state_fips: str = config.STATE_FIPS,
    county_fips: str = config.COUNTY_FIPS,
) -> None:
    """Persist isochrone GeoDataFrames to county-specific files.

    Args:
        isochrones: Dict mapping minutes → GeoDataFrame (any CRS).
        state_fips: State FIPS code used in the filename.
        county_fips: County FIPS code used in the filename.
    """
    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    for minutes, gdf in isochrones.items():
        path = _cache_path(minutes, state_fips, county_fips)
        out = gdf.to_crs(config.GEO_CRS) if gdf.crs.to_epsg() != 4326 else gdf
        out.to_file(path, driver="GeoJSON")
        logger.info("Saved %d-min isochrone to %s", minutes, path)


# ── Facility filter ───────────────────────────────────────────────────────────

def _filter_facilities_to_study_area(
    facilities: gpd.GeoDataFrame,
    state_fips: str,
    county_fips: str,
    max_drive_minutes: int,
) -> gpd.GeoDataFrame:
    """Return only the facilities that could plausibly serve the study county.

    Downloads the county TIGER tract boundary (cached by vulnerability_index),
    buffers by the Euclidean approximation of ``max_drive_minutes``, and clips
    the nationwide facility list to that area.  This keeps ORS API request
    counts manageable (tens to low hundreds vs. tens of thousands).

    Falls back to the unfiltered list if the county boundary cannot be loaded
    or if county_fips is ``"*"`` (statewide analysis).

    Args:
        facilities: Nationwide GeoDataFrame of facility points.
        state_fips: State FIPS code of the study area.
        county_fips: County FIPS code, or ``"*"`` to skip filtering.
        max_drive_minutes: Largest drive-time threshold; sets the buffer radius.

    Returns:
        Filtered GeoDataFrame containing only locally relevant facilities.
    """
    if county_fips == "*":
        logger.info(
            "county_fips='*' — skipping facility filter (statewide analysis)"
        )
        return facilities

    try:
        # vulnerability_index does NOT import isochrones, so this is safe
        from src.vulnerability_index import download_tiger_tracts

        tracts = download_tiger_tracts(state_fips, county_fips)
        buffer_m = config.EUCLIDEAN_BUFFERS.get(max_drive_minutes, 50_000)

        study_union = tracts.to_crs(config.PROJECTED_CRS).geometry.unary_union
        study_buffered = (
            gpd.GeoDataFrame(
                geometry=[study_union.buffer(buffer_m)],
                crs=config.PROJECTED_CRS,
            )
            .to_crs(config.GEO_CRS)
            .geometry[0]
        )

        fac_geo = facilities.to_crs(config.GEO_CRS)
        filtered = facilities[fac_geo.within(study_buffered)].copy()
        logger.info(
            "Filtered %d → %d facilities within %d km of study area",
            len(facilities), len(filtered), buffer_m // 1000,
        )
        return filtered

    except Exception as exc:
        logger.warning(
            "Could not filter facilities to study area (%s) — using all %d",
            exc, len(facilities),
        )
        return facilities


# ── Euclidean buffer fallback ─────────────────────────────────────────────────

def _build_euclidean_buffers(
    facilities: gpd.GeoDataFrame,
    drive_times: List[int],
) -> Dict[int, gpd.GeoDataFrame]:
    """Build straight-line distance buffers as a fallback for ORS isochrones.

    Dissolves all per-facility buffer circles into a single coverage polygon
    for each drive-time threshold.  Accuracy depends entirely on the spatial
    density of ``facilities`` — pass a pre-filtered local set, not a nationwide
    dataset, or the dissolved polygon will be meaningless.

    Args:
        facilities: GeoDataFrame of facility points (should be pre-filtered to
            the study area — see _filter_facilities_to_study_area).
        drive_times: Drive time values (minutes) to generate buffers for.

    Returns:
        Dict mapping minutes → single-row dissolved GeoDataFrame in GEO_CRS.
    """
    logger.warning(
        "Using Euclidean buffer fallback (straight-line distances). "
        "Results are approximate — shapes are circles, not road-network polygons. "
        "Set ORS_API_KEY in .env and omit --skip-isochrones for accurate results."
    )
    fac_proj = facilities.to_crs(config.PROJECTED_CRS)
    result: Dict[int, gpd.GeoDataFrame] = {}

    for minutes in drive_times:
        radius_m = config.EUCLIDEAN_BUFFERS[minutes]
        dissolved = unary_union(fac_proj.geometry.buffer(radius_m))
        gdf = gpd.GeoDataFrame(
            {"minutes": [minutes], "geometry": [dissolved]},
            crs=config.PROJECTED_CRS,
        ).to_crs(config.GEO_CRS)
        result[minutes] = gdf
        logger.info("Euclidean buffer: %d min → %d m radius", minutes, radius_m)

    return result


# ── ORS helpers ───────────────────────────────────────────────────────────────

def _call_ors_with_retry(client, locations: list, range_seconds: list) -> list:
    """Call ORS isochrones endpoint with exponential back-off on rate limits.

    Args:
        client: Configured openrouteservice.Client instance.
        locations: List of [lon, lat] coordinate pairs.
        range_seconds: Range values in seconds to pass to ORS.

    Returns:
        List of GeoJSON feature dicts from the ORS response.

    Raises:
        RuntimeError: If all retries are exhausted.
    """
    from openrouteservice.exceptions import ApiError

    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.isochrones(
                locations=locations,
                range=range_seconds,
                range_type="time",
            )
            return resp.get("features", [])
        except ApiError as exc:
            msg = str(exc)
            if "429" in msg or "rate" in msg.lower() or "quota" in msg.lower():
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "ORS rate limit (attempt %d/%d) — waiting %.0fs",
                    attempt + 1, _MAX_RETRIES, wait,
                )
                time.sleep(wait)
            else:
                raise

    raise RuntimeError(f"ORS API failed after {_MAX_RETRIES} retries")


# ── Public entry point ────────────────────────────────────────────────────────

def generate_isochrones(
    facilities: gpd.GeoDataFrame,
    drive_times: Optional[List[int]] = None,
    state_fips: str = config.STATE_FIPS,
    county_fips: str = config.COUNTY_FIPS,
    refresh_cache: bool = False,
    use_euclidean_fallback: bool = False,
) -> Dict[int, gpd.GeoDataFrame]:
    """Generate dissolved drive-time isochrones for the study area.

    Pipeline:
    1. Return county-specific cached files if they exist and refresh is False.
    2. Filter the nationwide facility list to those within the max-drive-time
       buffer of the study county (avoids wasting ORS quota on irrelevant sites).
    3. Call ORS in batches of 5 with rate-limit back-off, or build Euclidean
       buffers when ORS is unavailable / ``use_euclidean_fallback`` is True.
    4. Dissolve all per-facility polygons for each drive time into a single
       coverage polygon and cache to disk.

    Args:
        facilities: Nationwide GeoDataFrame of facility points.
        drive_times: Drive time thresholds in minutes. Defaults to
            config.DRIVE_TIMES_MINUTES.
        state_fips: State FIPS code for cache filename and spatial filter.
        county_fips: County FIPS code for cache filename and spatial filter.
        refresh_cache: Regenerate isochrones even if cached files exist.
        use_euclidean_fallback: Skip ORS entirely and use straight-line buffers.

    Returns:
        Dict mapping drive_time_minutes → dissolved single-polygon GeoDataFrame.
    """
    if drive_times is None:
        drive_times = config.DRIVE_TIMES_MINUTES

    if not refresh_cache:
        cached = _load_isochrone_cache(drive_times, state_fips, county_fips)
        if cached:
            return cached

    # Always filter to local facilities first — this is what makes ORS practical
    # and prevents Euclidean buffers from merging into a continent-wide blob
    local_facilities = _filter_facilities_to_study_area(
        facilities, state_fips, county_fips, max(drive_times)
    )

    if use_euclidean_fallback or not config.ORS_API_KEY:
        if not use_euclidean_fallback:
            logger.warning("ORS_API_KEY not configured — using Euclidean fallback")
        result = _build_euclidean_buffers(local_facilities, drive_times)
        _save_isochrones(result, state_fips, county_fips)
        return result

    # ── ORS path ──────────────────────────────────────────────────────────────
    fac_geo = local_facilities.to_crs(config.GEO_CRS)
    locations = [
        [row.geometry.x, row.geometry.y]
        for _, row in fac_geo.iterrows()
        if row.geometry is not None
    ]
    range_seconds = [m * 60 for m in drive_times]
    polys: Dict[int, list] = {m: [] for m in drive_times}

    logger.info(
        "Calling ORS for %d local facilities, %d drive-time thresholds",
        len(locations), len(drive_times),
    )

    try:
        import openrouteservice

        client = openrouteservice.Client(key=config.ORS_API_KEY)
        batches = [
            locations[i: i + _ORS_BATCH_SIZE]
            for i in range(0, len(locations), _ORS_BATCH_SIZE)
        ]

        for idx, batch in enumerate(batches):
            logger.info(
                "ORS batch %d/%d (%d facilities)", idx + 1, len(batches), len(batch)
            )
            try:
                features = _call_ors_with_retry(client, batch, range_seconds)
            except Exception as exc:
                logger.error(
                    "ORS batch %d failed (%s) — switching to Euclidean fallback",
                    idx + 1, exc,
                )
                result = _build_euclidean_buffers(local_facilities, drive_times)
                _save_isochrones(result, state_fips, county_fips)
                return result

            for feat in features:
                props = feat.get("properties", {})
                value_min = int(props.get("value", 0)) // 60
                if value_min in polys:
                    polys[value_min].append(shape(feat["geometry"]))

            if idx < len(batches) - 1:
                time.sleep(_BATCH_DELAY_SEC)

    except Exception as exc:
        logger.error(
            "ORS client error (%s) — switching to Euclidean fallback", exc
        )
        result = _build_euclidean_buffers(local_facilities, drive_times)
        _save_isochrones(result, state_fips, county_fips)
        return result

    # Dissolve per-drive-time polygon lists
    result: Dict[int, gpd.GeoDataFrame] = {}
    for minutes, polygon_list in polys.items():
        if not polygon_list:
            logger.warning("No ORS polygons for %d min — using buffer fallback", minutes)
            fb = _build_euclidean_buffers(local_facilities, [minutes])
            result[minutes] = fb[minutes]
            continue
        dissolved = unary_union(polygon_list)
        result[minutes] = gpd.GeoDataFrame(
            {"minutes": [minutes], "geometry": [dissolved]},
            crs=config.GEO_CRS,
        )
        logger.info(
            "Dissolved %d polygons → %d-min isochrone", len(polygon_list), minutes
        )

    _save_isochrones(result, state_fips, county_fips)
    return result

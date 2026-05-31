"""Healthcare facility data retrieval and processing."""

import logging
import time
from typing import Optional

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

import config

logger = logging.getLogger(__name__)

HRSA_URL = (
    "https://data.hrsa.gov/DataDownload/DD_Files/"
    "Health_Center_Service_Delivery_and_LookAlike_Sites.csv"
)

# CMS Hospital General Information — stable government API, no key required
# HIFLD ArcGIS service URLs have proven unstable; CMS is the authoritative source
CMS_HOSPITALS_URL = (
    "https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0"
)

# Hospital types to retain — excludes psychiatric, children's, VA, DoD
CMS_KEEP_TYPES = {"Acute Care Hospitals", "Critical Access Hospitals"}


def download_hrsa_facilities(refresh_cache: bool = False) -> pd.DataFrame:
    """Download HRSA Health Center Service Delivery Sites as a DataFrame.

    Args:
        refresh_cache: Force re-download even when a local copy exists.

    Returns:
        Raw DataFrame of all HRSA delivery sites.

    Raises:
        RuntimeError: If the HTTP request fails.
    """
    cache_path = config.RAW_DIR / "hrsa_facilities.csv"

    if not refresh_cache and cache_path.exists():
        logger.info("Loading cached HRSA data from %s", cache_path)
        return pd.read_csv(cache_path, low_memory=False)

    logger.info("Downloading HRSA facility data")
    try:
        resp = requests.get(HRSA_URL, timeout=180)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Failed to download HRSA data: {exc}") from exc

    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(resp.content)
    logger.info("Saved HRSA data to %s", cache_path)

    return pd.read_csv(cache_path, low_memory=False)


def _find_column(df: pd.DataFrame, candidates: list[str]) -> Optional[str]:
    """Return the first candidate column name (case-insensitive) found in df.

    Args:
        df: DataFrame to search.
        candidates: Ordered list of preferred column names.

    Returns:
        Matching column name, or None if none are found.
    """
    lower_map = {c.lower(): c for c in df.columns}
    for name in candidates:
        if name in df.columns:
            return name
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    return None


def _geocode_by_zip(df: pd.DataFrame) -> tuple[pd.DataFrame, str, str]:
    """Add lat/lon columns to df using ZIP code centroids via pgeocode.

    This is a fallback for HRSA data that ships without coordinate columns.
    Precision is ZIP code centroid (~1–5 km from the actual site), which is
    sufficient for 15/30/60-minute isochrone analysis.

    Args:
        df: DataFrame containing a ZIP / postal code column.

    Returns:
        Tuple of (updated_df, lat_col_name, lon_col_name).

    Raises:
        RuntimeError: If pgeocode is not installed or no ZIP column is found.
    """
    try:
        import pgeocode
    except ImportError as exc:
        raise RuntimeError(
            "pgeocode is required for ZIP-code geocoding fallback. "
            "Install it with: pip install pgeocode"
        ) from exc

    zip_col = _find_column(
        df,
        ["Site Postal Code", "Postal Code", "zip_code", "ZIP", "ZipCode", "Zip Code", "ZIPCODE"],
    )
    if zip_col is None:
        raise RuntimeError(
            "HRSA data has no lat/lon columns and no ZIP/postal code column "
            "for geocoding fallback."
        )

    logger.warning(
        "HRSA CSV contains no lat/lon columns — falling back to ZIP code "
        "centroid geocoding via pgeocode (approximate precision). "
        "Coordinates reflect ZIP centroid, not exact site address."
    )

    nomi = pgeocode.Nominatim("us")
    # Ensure clean 5-digit ZIPs; pgeocode handles leading-zero ZIPs fine
    zips = df[zip_col].astype(str).str.strip().str[:5]
    coords = nomi.query_postal_code(zips.tolist())

    df = df.copy()
    df["_geocoded_lat"] = coords["latitude"].values
    df["_geocoded_lon"] = coords["longitude"].values

    return df, "_geocoded_lat", "_geocoded_lon"


def process_hrsa_facilities(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Filter active HRSA sites and convert to a standardized GeoDataFrame.

    Attempts to use native lat/lon columns; falls back to ZIP code centroid
    geocoding via pgeocode when coordinates are absent (as is the case with
    the current HRSA CSV download format).

    Args:
        df: Raw HRSA DataFrame from download_hrsa_facilities.

    Returns:
        GeoDataFrame with columns: facility_id, name, type, latitude,
        longitude, geometry. CRS is set to GEO_CRS.

    Raises:
        RuntimeError: If coordinates cannot be determined by any method.
    """
    # The current HRSA download uses "Site Status Description" with values
    # like "Active" / "Inactive". Include both the new and legacy column names.
    status_col = _find_column(
        df,
        [
            "Site Status Description",
            "Health Center Status",
            "Site Status",
            "Status",
            "Active Status",
        ],
    )
    if status_col:
        active_vals = {"ACTIVE", "1", "TRUE", "YES", "A"}
        mask = df[status_col].astype(str).str.strip().str.upper().isin(active_vals)
        df = df[mask].copy()
        logger.info("Filtered HRSA to %d active facilities", len(df))
    else:
        logger.warning(
            "No status column found in HRSA data; retaining all %d rows", len(df)
        )

    lat_col = _find_column(df, ["Latitude", "latitude", "LAT", "lat", "Y_Lat"])
    lon_col = _find_column(
        df, ["Longitude", "longitude", "LON", "lon", "Long", "X_Long"]
    )

    if lat_col is None or lon_col is None:
        # Current HRSA CSV has no coordinate columns — geocode by ZIP centroid
        df, lat_col, lon_col = _geocode_by_zip(df)

    df = df.copy()
    df[lat_col] = pd.to_numeric(df[lat_col], errors="coerce")
    df[lon_col] = pd.to_numeric(df[lon_col], errors="coerce")
    df = df.dropna(subset=[lat_col, lon_col])

    name_col = _find_column(
        df, ["Site Name", "Health Center Name", "Center Name", "Name", "SITE_NAME"]
    ) or df.columns[0]

    geometry = [Point(lon, lat) for lon, lat in zip(df[lon_col], df[lat_col])]
    gdf = gpd.GeoDataFrame(
        {
            "facility_id": [f"HRSA_{i}" for i in range(len(df))],
            "name": df[name_col].values,
            "type": "HRSA Health Center",
            "latitude": df[lat_col].values,
            "longitude": df[lon_col].values,
        },
        geometry=geometry,
        crs=config.GEO_CRS,
    )
    return gdf


def download_cms_hospitals(refresh_cache: bool = False) -> pd.DataFrame:
    """Download CMS Hospital General Information via the provider-data API.

    Paginates through all ~5,400 hospital records. No API key required.

    Args:
        refresh_cache: Force re-download even when a local copy exists.

    Returns:
        Raw DataFrame of all CMS hospital records.

    Raises:
        RuntimeError: If any page request fails.
    """
    cache_path = config.RAW_DIR / "cms_hospitals.csv"

    if not refresh_cache and cache_path.exists():
        logger.info("Loading cached CMS hospital data from %s", cache_path)
        return pd.read_csv(cache_path, dtype={"zip_code": str})

    logger.info("Downloading CMS Hospital General Information")
    page_size = 500
    offset = 0
    all_records: list[dict] = []

    while True:
        try:
            resp = requests.get(
                CMS_HOSPITALS_URL,
                params={"limit": page_size, "offset": offset},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(
                f"CMS hospital API request failed at offset {offset}: {exc}"
            ) from exc

        page = data.get("results", [])
        all_records.extend(page)
        logger.debug("CMS: fetched %d records (offset=%d)", len(page), offset)

        if len(page) < page_size:
            break
        offset += len(page)
        time.sleep(0.1)

    df = pd.DataFrame(all_records)
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)
    logger.info("Saved %d CMS hospital records to %s", len(df), cache_path)
    return df


def process_cms_hospitals(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Filter CMS hospitals to acute/critical-access types and geocode by ZIP.

    Args:
        df: Raw CMS DataFrame from download_cms_hospitals.

    Returns:
        GeoDataFrame with columns: facility_id, name, type, latitude,
        longitude, geometry. CRS is set to GEO_CRS.
    """
    mask = df["hospital_type"].isin(CMS_KEEP_TYPES)
    df = df[mask].copy()
    logger.info("Filtered CMS to %d acute/critical-access hospitals", len(df))

    # CMS does not include lat/lon — geocode by ZIP centroid
    df, lat_col, lon_col = _geocode_by_zip(df)
    df[lat_col] = pd.to_numeric(df[lat_col], errors="coerce")
    df[lon_col] = pd.to_numeric(df[lon_col], errors="coerce")
    df = df.dropna(subset=[lat_col, lon_col])

    geometry = [Point(lon, lat) for lon, lat in zip(df[lon_col], df[lat_col])]
    gdf = gpd.GeoDataFrame(
        {
            "facility_id": [f"CMS_{i}" for i in range(len(df))],
            "name": df["facility_name"].values,
            "type": "Hospital",
            "latitude": df[lat_col].values,
            "longitude": df[lon_col].values,
        },
        geometry=geometry,
        crs=config.GEO_CRS,
    )
    return gdf


def load_facilities(refresh_cache: bool = False) -> gpd.GeoDataFrame:
    """Top-level entry point: combine HRSA and HIFLD into one facility layer.

    Loads from the processed GeoJSON cache when available; otherwise downloads,
    filters, and merges both datasets before saving.

    Args:
        refresh_cache: Force re-download of all source data.

    Returns:
        Combined GeoDataFrame with a uniform schema:
        facility_id, name, type, latitude, longitude, geometry.
    """
    processed_path = config.PROCESSED_DIR / "facilities.geojson"

    if not refresh_cache and processed_path.exists():
        logger.info("Loading cached facilities from %s", processed_path)
        return gpd.read_file(processed_path)

    logger.info("Processing healthcare facility data")

    hrsa_raw = download_hrsa_facilities(refresh_cache=refresh_cache)
    hrsa_gdf = process_hrsa_facilities(hrsa_raw)
    logger.info("HRSA: %d active facilities", len(hrsa_gdf))

    cms_raw = download_cms_hospitals(refresh_cache=refresh_cache)
    cms_gdf = process_cms_hospitals(cms_raw)
    logger.info("CMS: %d hospitals", len(cms_gdf))

    combined = gpd.GeoDataFrame(
        pd.concat([hrsa_gdf, cms_gdf], ignore_index=True),
        crs=config.GEO_CRS,
    )
    combined = combined.dropna(subset=["geometry"])
    logger.info("Combined facility dataset: %d total facilities", len(combined))

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_file(processed_path, driver="GeoJSON")
    logger.info("Saved facilities to %s", processed_path)

    return combined

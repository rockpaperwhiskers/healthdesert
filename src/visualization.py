"""Visualization module: static choropleth, interactive map, and summary report."""

import logging
from pathlib import Path
from typing import Dict, Optional

import folium
import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

try:
    import contextily as ctx
    _HAS_CONTEXTILY = True
except ImportError:
    _HAS_CONTEXTILY = False

import config

logger = logging.getLogger(__name__)

ACCESS_COLORS = {
    "Desert": "#d73027",
    "At Risk": "#fc8d59",
    "Underserved": "#fee090",
    "Adequate": "#74add1",
}

_COLS_FOR_MAP = [
    "GEOID", "access_category", "vulnerability_score", "vulnerability_quartile",
    "healthcare_desert", "pct_elderly", "pct_uninsured", "pct_poverty",
    "pct_no_vehicle", "B01003_001E", "geometry",
]


def _add_north_arrow(ax: plt.Axes, x: float = 0.05, y: float = 0.15) -> None:
    """Draw a minimal north-arrow annotation on a matplotlib axis.

    Args:
        ax: Target matplotlib Axes.
        x: Axes-fraction x position of the arrow base.
        y: Axes-fraction y position of the arrow base.
    """
    ax.annotate(
        "N",
        xy=(x, y + 0.05),
        xytext=(x, y),
        xycoords="axes fraction",
        textcoords="axes fraction",
        fontsize=13,
        fontweight="bold",
        ha="center",
        va="bottom",
        arrowprops=dict(arrowstyle="-|>", color="black", lw=2),
    )


def _add_scale_bar(
    ax: plt.Axes, gdf: gpd.GeoDataFrame, length_m: int = 10_000
) -> None:
    """Draw a scale bar on a projected matplotlib axis.

    Args:
        ax: Target matplotlib Axes (must use a projected CRS).
        gdf: GeoDataFrame whose total bounds define the map extent.
        length_m: Scale bar length in metres (default 10 km).
    """
    xmin, ymin, xmax, ymax = gdf.total_bounds
    bar_x = xmin + (xmax - xmin) * 0.05
    bar_y = ymin + (ymax - ymin) * 0.03
    tick_h = (ymax - ymin) * 0.008

    ax.plot([bar_x, bar_x + length_m], [bar_y, bar_y], "k-", lw=3, transform=ax.transData)
    for tick_x in (bar_x, bar_x + length_m):
        ax.plot([tick_x, tick_x], [bar_y, bar_y + tick_h], "k-", lw=2, transform=ax.transData)
    ax.text(
        bar_x + length_m / 2,
        bar_y + tick_h * 2,
        "10 km",
        ha="center",
        fontsize=8,
        transform=ax.transData,
    )


def plot_vulnerability_choropleth(
    tracts: gpd.GeoDataFrame,
    facilities: gpd.GeoDataFrame,
    isochrones: Dict[int, gpd.GeoDataFrame],
    output_path: Optional[Path] = None,
) -> None:
    """Produce a static choropleth of vulnerability score with overlays.

    Layers (bottom → top):
      1. Tract polygons coloured by vulnerability_score (YlOrRd)
      2. Hatched red outlines on healthcare_desert tracts
      3. 30-minute isochrone boundary in navy
      4. Facility point markers (HRSA = circle, Hospital = triangle)
      5. Contextily basemap if contextily is installed

    Args:
        tracts: Final classified tract GeoDataFrame.
        facilities: Healthcare facility GeoDataFrame.
        isochrones: Dict mapping minutes → dissolved isochrone GeoDataFrame.
        output_path: PNG save path; defaults to outputs/maps/vulnerability_choropleth.png.
    """
    if output_path is None:
        output_path = config.MAPS_DIR / "vulnerability_choropleth.png"
    config.MAPS_DIR.mkdir(parents=True, exist_ok=True)

    tracts_proj = tracts.to_crs(config.PROJECTED_CRS)
    fac_proj = facilities.to_crs(config.PROJECTED_CRS)

    fig, ax = plt.subplots(figsize=(14, 10))

    # Base choropleth
    tracts_proj.plot(
        column="vulnerability_score",
        ax=ax,
        cmap="YlOrRd",
        legend=True,
        legend_kwds={"label": "Vulnerability Score", "shrink": 0.55, "pad": 0.01},
        missing_kwds={"color": "#cccccc", "label": "No data"},
        alpha=0.85,
        edgecolor="#888888",
        linewidth=0.2,
    )

    # Desert hatch overlay
    desert = tracts_proj[tracts_proj.get("healthcare_desert", False) == True]
    if len(desert):
        desert.plot(
            ax=ax,
            facecolor="none",
            edgecolor="#8b0000",
            hatch="///",
            linewidth=0.4,
            zorder=3,
        )

    # 30-min isochrone boundary
    if 30 in isochrones:
        isochrones[30].to_crs(config.PROJECTED_CRS).boundary.plot(
            ax=ax, color="navy", linewidth=2.5, zorder=4
        )

    # Facility markers
    hrsa = fac_proj[fac_proj["type"] == "HRSA Health Center"]
    hosp = fac_proj[fac_proj["type"] == "Hospital"]
    if len(hrsa):
        hrsa.plot(ax=ax, color="#1f78b4", marker="o", markersize=3, alpha=0.75, zorder=5)
    if len(hosp):
        hosp.plot(ax=ax, color="#33a02c", marker="^", markersize=5, alpha=0.80, zorder=5)

    # Optional basemap
    if _HAS_CONTEXTILY:
        try:
            ctx.add_basemap(ax, source=ctx.providers.CartoDB.Positron, zoom="auto")
        except Exception as exc:
            logger.warning("Basemap unavailable: %s", exc)

    _add_scale_bar(ax, tracts_proj)
    _add_north_arrow(ax)

    legend_handles = [
        mpatches.Patch(facecolor="none", edgecolor="#8b0000", hatch="///", label="Healthcare Desert"),
        mpatches.Patch(facecolor="none", edgecolor="navy", linewidth=2.5, label="30-min Drive Boundary"),
        plt.Line2D([0], [0], marker="o", color="w", markerfacecolor="#1f78b4",
                   markersize=7, label="HRSA Health Center"),
        plt.Line2D([0], [0], marker="^", color="w", markerfacecolor="#33a02c",
                   markersize=8, label="Hospital"),
    ]
    ax.legend(
        handles=legend_handles, loc="lower right",
        fontsize=8, framealpha=0.9, edgecolor="#cccccc",
    )
    ax.set_title(
        f"Healthcare Vulnerability Index — {config.STATE_NAME}\n"
        f"(Census Tracts, ACS {config.ACS_YEAR})",
        fontsize=14,
        fontweight="bold",
        pad=12,
    )
    ax.set_axis_off()
    plt.tight_layout()
    plt.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    logger.info("Saved choropleth map to %s", output_path)


def _folium_legend_html() -> str:
    """Return an HTML legend block for the access category colour scheme."""
    swatches = "".join(
        f'<div style="margin:3px 0">'
        f'<span style="background:{color};display:inline-block;'
        f'width:14px;height:14px;margin-right:7px;border:1px solid #666;'
        f'vertical-align:middle"></span>{label}</div>'
        for label, color in ACCESS_COLORS.items()
    )
    return (
        '<div style="position:fixed;bottom:30px;left:30px;z-index:1000;'
        'background:white;padding:12px 16px;border-radius:6px;'
        'box-shadow:0 2px 8px rgba(0,0,0,.3);font:12px Arial,sans-serif">'
        f"<b>Access Category</b><br>{swatches}</div>"
    )


def create_interactive_map(
    tracts: gpd.GeoDataFrame,
    facilities: gpd.GeoDataFrame,
    isochrones: Dict[int, gpd.GeoDataFrame],
    output_path: Optional[Path] = None,
) -> None:
    """Build a toggleable Folium map with four overlay layers.

    Layer 1 (default on):  Tract choropleth by access_category.
    Layer 2 (default on):  Drive-time isochrone rings at 15 / 30 / 60 min.
    Layer 3 (default on):  Facility point markers with name / type popups.
    Layer 4 (default off): Continuous vulnerability score choropleth.

    Args:
        tracts: Final classified tract GeoDataFrame.
        facilities: Healthcare facility GeoDataFrame.
        isochrones: Dict mapping minutes → dissolved isochrone GeoDataFrame.
        output_path: HTML save path; defaults to outputs/maps/interactive_map.html.
    """
    if output_path is None:
        output_path = config.MAPS_DIR / "interactive_map.html"
    config.MAPS_DIR.mkdir(parents=True, exist_ok=True)

    # Slim down GeoDataFrame before converting to GeoJSON
    keep = [c for c in _COLS_FOR_MAP if c in tracts.columns]
    tracts_geo = tracts[keep].to_crs(config.GEO_CRS).copy()

    bounds = tracts_geo.total_bounds  # [minx, miny, maxx, maxy] in lon/lat
    centroid = tracts_geo.geometry.unary_union.centroid
    m = folium.Map(
        location=[centroid.y, centroid.x],
        zoom_start=10,
        tiles="CartoDB positron",
        attr="© OpenStreetMap contributors © CARTO",
    )

    # ── Layer 1: Access category choropleth ──────────────────────────────────
    access_layer = folium.FeatureGroup(name="Access Category", show=True)

    def _access_style(feature: dict) -> dict:
        cat = feature["properties"].get("access_category", "Adequate")
        return {
            "fillColor": ACCESS_COLORS.get(str(cat), "#74add1"),
            "color": "#444444",
            "weight": 0.5,
            "fillOpacity": 0.65,
        }

    tooltip_fields = [c for c in ["GEOID", "access_category", "vulnerability_score",
                                   "pct_elderly", "pct_uninsured", "pct_poverty",
                                   "pct_no_vehicle"] if c in tracts_geo.columns]
    folium.GeoJson(
        tracts_geo.__geo_interface__,
        style_function=_access_style,
        tooltip=folium.GeoJsonTooltip(
            fields=tooltip_fields,
            aliases=[f.replace("_", " ").title() for f in tooltip_fields],
            localize=True,
        ),
        name="Access Category",
    ).add_to(access_layer)
    access_layer.add_to(m)

    # ── Layer 2: Isochrone rings ──────────────────────────────────────────────
    iso_palette = {15: "#2c7bb6", 30: "#1a9641", 60: "#d7191c"}
    for minutes in sorted(isochrones):
        iso_geo = isochrones[minutes].to_crs(config.GEO_CRS)
        iso_layer = folium.FeatureGroup(
            name=f"{minutes}-min Drive Time", show=True
        )
        color = iso_palette.get(minutes, "#888888")
        folium.GeoJson(
            iso_geo.__geo_interface__,
            style_function=lambda _, c=color: {
                "fillColor": c,
                "color": c,
                "weight": 2,
                "fillOpacity": 0.12,
            },
            tooltip=f"{minutes}-minute drive time",
        ).add_to(iso_layer)
        iso_layer.add_to(m)

    # ── Layer 3: Facility markers ─────────────────────────────────────────────
    fac_layer = folium.FeatureGroup(name="Healthcare Facilities", show=True)
    fac_geo = facilities.to_crs(config.GEO_CRS)
    for _, row in fac_geo.iterrows():
        if row.geometry is None:
            continue
        is_hrsa = row.get("type") == "HRSA Health Center"
        try:
            folium.Marker(
                location=[row.geometry.y, row.geometry.x],
                popup=folium.Popup(
                    f"<b>{row.get('name', 'Unknown')}</b><br>"
                    f"Type: {row.get('type', 'N/A')}<br>"
                    f"ID: {row.get('facility_id', 'N/A')}",
                    max_width=260,
                ),
                icon=folium.Icon(
                    color="blue" if is_hrsa else "darkgreen",
                    icon="heart" if is_hrsa else "plus-sign",
                    prefix="glyphicon",
                ),
            ).add_to(fac_layer)
        except Exception:
            pass  # skip malformed geometry rows
    fac_layer.add_to(m)

    # ── Layer 4: Continuous vulnerability score (toggle off by default) ──────
    vuln_layer = folium.FeatureGroup(name="Vulnerability Score (Continuous)", show=False)
    if "vulnerability_score" in tracts_geo.columns:
        scores = tracts_geo["vulnerability_score"].dropna()
        vmin, vmax = scores.quantile(0.05), scores.quantile(0.95)
        colormap = folium.LinearColormap(
            ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"],
            vmin=vmin, vmax=vmax,
            caption="Vulnerability Score",
        )
        for _, row in tracts_geo.iterrows():
            if row.geometry is None or pd.isna(row.get("vulnerability_score")):
                continue
            score = float(row["vulnerability_score"])
            try:
                folium.GeoJson(
                    row.geometry.__geo_interface__,
                    style_function=lambda _, s=score: {
                        "fillColor": colormap(min(max(s, vmin), vmax)),
                        "color": "none",
                        "fillOpacity": 0.70,
                    },
                ).add_to(vuln_layer)
            except Exception:
                pass
        colormap.add_to(m)
    vuln_layer.add_to(m)

    m.get_root().html.add_child(folium.Element(_folium_legend_html()))
    folium.LayerControl(collapsed=False).add_to(m)

    # Fit the initial view to the study area regardless of county size
    m.fit_bounds([[bounds[1], bounds[0]], [bounds[3], bounds[2]]])

    m.save(str(output_path))
    logger.info("Saved interactive map to %s", output_path)


def generate_summary_report(
    tracts: gpd.GeoDataFrame,
    output_path: Optional[Path] = None,
) -> pd.DataFrame:
    """Compute summary statistics and print a formatted console report.

    Outputs:
      - outputs/reports/summary_stats.csv   (category-level statistics)
      - outputs/reports/top10_desert_tracts.csv

    Args:
        tracts: Final classified tract GeoDataFrame.
        output_path: CSV save path; defaults to outputs/reports/summary_stats.csv.

    Returns:
        Category-level summary statistics DataFrame.
    """
    if output_path is None:
        output_path = config.REPORTS_DIR / "summary_stats.csv"
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    df = tracts.copy()
    total_tracts = len(df)

    pop_col = "B01003_001E" if "B01003_001E" in df.columns else None
    agg_spec: dict = {"tract_count": ("GEOID", "count")}
    if pop_col:
        agg_spec["total_population"] = (pop_col, "sum")
    agg_spec["mean_vulnerability"] = ("vulnerability_score", "mean")

    cat_stats = (
        df.groupby("access_category")
        .agg(**agg_spec)
        .reset_index()
    )
    cat_stats["pct_tracts"] = (
        cat_stats["tract_count"] / total_tracts * 100
    ).round(1)

    desert_pop = 0
    if pop_col:
        desert_pop = df.loc[df["access_category"] == "Desert", pop_col].sum()

    # Top 10 most vulnerable desert tracts
    desert_df = df[df["access_category"] == "Desert"].copy()
    top10_base = ["GEOID", "vulnerability_score", "pct_elderly",
                  "pct_uninsured", "pct_poverty", "pct_no_vehicle"]
    if "NAME" in df.columns:
        top10_base = ["NAME"] + top10_base
    top10_cols = [c for c in top10_base if c in desert_df.columns]
    top10 = desert_df.nlargest(10, "vulnerability_score")[top10_cols]

    # ── Console output ────────────────────────────────────────────────────────
    sep = "=" * 72
    print(f"\n{sep}")
    print("  HEALTHCARE DESERT ANALYSIS — SUMMARY REPORT")
    print(f"  {config.STATE_NAME}  |  ACS {config.ACS_YEAR}")
    print(sep)
    print(f"\n  Total tracts analysed : {total_tracts:,}")
    if pop_col:
        print(f"  Population in deserts : {int(desert_pop):,}\n")

    display_cols = {
        "access_category": "Category",
        "tract_count": "Tracts",
        "pct_tracts": "% of Tracts",
        "mean_vulnerability": "Mean Score",
    }
    if "total_population" in cat_stats.columns:
        display_cols["total_population"] = "Population"

    print(
        cat_stats[list(display_cols)]
        .rename(columns=display_cols)
        .to_string(index=False)
    )

    if len(top10):
        print("\n  Top 10 Most Vulnerable Desert Tracts:")
        print(top10.to_string(index=False))

    print(f"\n{sep}\n")

    cat_stats.to_csv(output_path, index=False)
    top10.to_csv(config.REPORTS_DIR / "top10_desert_tracts.csv", index=False)
    logger.info("Saved summary report to %s", output_path)

    return cat_stats

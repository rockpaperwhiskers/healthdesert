# Healthcare Desert Analysis

A spatial analysis pipeline that identifies US census tracts that are **healthcare deserts** — areas combining high healthcare vulnerability (elderly, uninsured, impoverished, or car-free populations) with poor physical access to healthcare facilities.

## Project Motivation

Tens of millions of Americans live in communities where reaching a doctor, clinic, or hospital requires more than a 30-minute drive — and where the populations most likely to need care are least equipped to make that trip. This project operationalises the concept of a "healthcare desert" using real ACS 5-year estimates, real facility locations from HRSA and HIFLD, and drive-time isochrones from OpenRouteService.

---

## Project Structure

```
healthcare_deserts/
├── data/
│   ├── raw/              ← Downloaded source files (gitignored)
│   └── processed/        ← Cleaned GeoJSONs and CSVs
├── notebooks/
│   └── exploration.ipynb ← EDA and ad-hoc analysis
├── src/
│   ├── census_data.py    ← ACS 5-year retrieval and vulnerability vars
│   ├── facilities.py     ← HRSA + HIFLD download and unification
│   ├── isochrones.py     ← ORS drive-time isochrones with buffer fallback
│   ├── vulnerability_index.py ← Index computation and desert classification
│   └── visualization.py  ← Static map, interactive map, summary report
├── outputs/
│   ├── maps/             ← PNG and HTML map outputs
│   └── reports/          ← CSV summary statistics
├── main.py               ← Pipeline orchestration
├── config.py             ← All configuration constants
└── requirements.txt
```

---

## Setup

### 1. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Obtain API keys

**Census API key** (free, instant)  
Sign up at: https://api.census.gov/data/key_signup.html

**OpenRouteService API key** (free tier: 2,000 isochrone requests/day)  
Sign up at: https://openrouteservice.org/dev/#/signup

### 3. Create a `.env` file

Copy the template below into a file named `.env` at the project root:

```env
CENSUS_API_KEY=your_census_api_key_here
ORS_API_KEY=your_openrouteservice_api_key_here
```

---

## Running the Pipeline

### Full run (default: Cook County, Illinois)

```bash
python main.py
```

### Different county

```bash
python main.py --county 043       # DuPage County, IL
python main.py --county "*"       # All counties in Illinois
```

### Skip ORS isochrones (use straight-line buffers instead)

Useful when you haven't set up an ORS key yet or want a fast test run:

```bash
python main.py --skip-isochrones
```

### Force re-download of all data

```bash
python main.py --refresh-cache
```

### Combining flags

```bash
python main.py --county 031 --skip-isochrones --refresh-cache
```

---

## Outputs

| File | Description |
|------|-------------|
| `outputs/maps/vulnerability_choropleth.png` | Static 300-DPI map of vulnerability scores, desert hatch, isochrone boundary, and facility markers |
| `outputs/maps/interactive_map.html` | Folium map with toggleable layers: access categories, isochrone rings, facilities, and continuous vulnerability score |
| `outputs/reports/summary_stats.csv` | Tract counts, populations, and mean vulnerability scores per access category |
| `outputs/reports/top10_desert_tracts.csv` | The 10 highest-vulnerability desert tracts with their component scores |

---

## Desert Classification Logic

A tract is classified along two dimensions:

1. **Vulnerability quartile** (Q1–Q4) — weighted composite of:
   - % elderly (65+): weight 0.25
   - % uninsured: weight 0.30
   - % below poverty line: weight 0.25
   - % households with no vehicle: weight 0.20

2. **Drive-time access** — whether the tract centroid falls within the 15-min, 30-min, or 60-min isochrone from any facility

| Category | Condition |
|----------|-----------|
| **Desert** | Q4 vulnerability AND outside 30-min isochrone |
| **At Risk** | Q4 vulnerability AND inside 30-min but outside 15-min |
| **Underserved** | Q3 vulnerability AND outside 30-min isochrone |
| **Adequate** | All other tracts |

---

## Portfolio Notes

### Technical skills demonstrated

- **Census API** — authenticated requests via the `census` Python package; handling ACS variable hierarchies (age/sex × insurance status) and the `-666666666` missing-data sentinel
- **GeoPandas & Shapely** — spatial joins (centroid-in-polygon), CRS management (EPSG:4326 ↔ EPSG:3857), `unary_union` dissolves
- **OpenRouteService** — batch isochrone generation with rate-limit back-off; graceful degradation to Euclidean buffers
- **Vulnerability indexing** — min-max normalisation, weighted composite scores, quartile classification (`pd.qcut`)
- **Static cartography** — matplotlib choropleth, hatch overlays, contextily basemaps, north arrow, scale bar
- **Interactive cartography** — Folium `FeatureGroup` layers, `GeoJsonTooltip`, `LinearColormap`, `LayerControl`
- **Pipeline engineering** — argparse CLI, `pathlib`-based file I/O, disk caching at every step, per-step error isolation, structured logging

---

## Known Limitations & Future Work

- **Facility coverage**: HRSA and HIFLD are national datasets; no filtering to the study area is applied before isochrone generation, so ORS API quota usage scales with the number of nationwide facilities.
- **Isochrone accuracy**: The Euclidean buffer fallback ignores road networks and topography. Use ORS for publishable results.
- **Temporal mismatch**: ACS 2022 (2018–2022 5-year estimates) and facility datasets may not be contemporaneous.
- **Access definition**: Centroid-in-polygon coverage ignores tract shape; large rural tracts may be marked "covered" even though most residents are far from a facility.
- **Potential improvements**: filter facilities to a bounding box around the study area before calling ORS; weight isochrone coverage by population-weighted tract centroid; incorporate transit or walking isochrones for car-free populations; add Medicaid/Medicare acceptance as a facility attribute.

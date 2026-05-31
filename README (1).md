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

## Results: Clark County, Nevada (FIPS 32003)

> **Config used:** `STATE_FIPS = "32"` (Nevada), `COUNTY_FIPS = "003"` (Clark County), `ACS_YEAR = 2022`

This run analyses all 535 census tracts in Clark County — home to Las Vegas and one of the fastest-growing metro areas in the US — using ACS 2022 5-year estimates and national HRSA/HIFLD facility data.

### Summary Statistics

| Access Category | Tracts | Population | Mean Vulnerability Score | Share of Tracts |
|-----------------|-------:|-----------:|-------------------------:|----------------:|
| Adequate        | 530    | 2,254,253  | 0.204                    | 99.1%           |
| At Risk         | 2      | 7,792      | 0.291                    | 0.4%            |
| Desert          | 1      | 949        | 0.357                    | 0.2%            |
| Underserved     | 2      | 2,932      | 0.236                    | 0.4%            |

Of the 535 tracts analysed, **530 (99.1%)** fall into the Adequate category. The remaining 5 tracts — covering an estimated **11,673 people** — face some combination of high vulnerability and inadequate geographic access to care.

### The Desert Tract: Census Tract 57.03

The single confirmed healthcare desert is **Census Tract 57.03** (GEOID `32003005703`), with a composite vulnerability score of **0.357** — comfortably in the top quartile. Its component scores:

| Variable | Value |
|----------|------:|
| % Elderly (65+) | 38.0% |
| % Uninsured | 11.9% |
| % Below poverty line | 24.7% |
| % Households with no vehicle | 21.7% |
| **Composite vulnerability score** | **0.357** |
| **Estimated population** | **949** |

This tract's centroid lies beyond the 30-minute drive-time isochrone from any HRSA health center or HIFLD hospital — qualifying it as a healthcare desert under the classification logic above.

### Key Findings

- **Facility clustering:** Healthcare facilities are almost entirely concentrated within the Las Vegas–Henderson urban core. Outlying areas — particularly the southeastern portion of Clark County and communities like Moapa Valley — are facility-sparse and fall outside the 30-minute isochrone.

- **Isochrone shape:** Road-network isochrones extend access along major highway corridors but leave large off-highway areas unreachable within 30 minutes. The gap between urban coverage and rural isolation is stark.

- **Correlated burdens:** The vulnerability variables are not independent. Uninsured rate and poverty rate show a strong positive correlation (r = 0.65); poverty and no-vehicle households are tightly coupled (r = 0.69). Elderly share correlates *negatively* with uninsured rate (r = −0.32), likely reflecting Medicare coverage. Desert tracts are simultaneously poor, car-free, and uninsured.

- **Vulnerability distributions:** Median elderly rate across county tracts is 13.4%; median uninsured rate is 10.4%; median poverty rate is 11.0%; median no-vehicle household rate is 4.6%. All distributions are right-skewed, meaning a small tail of tracts carries disproportionately high burdens.

### Maps and Charts

All outputs are in `outputs/`:

| File | What it shows |
|------|---------------|
| `maps/vulnerability_choropleth.png` | County-wide vulnerability score choropleth with facility markers and 30-min isochrone boundary |
| `maps/isochrones_clark_county.png` | Drive-time isochrones (15/30/60 min) over the full county extent |
| `charts/01_vulnerability_distributions.png` | Histograms of all four vulnerability variables with medians |
| `charts/02_facility_map.png` | Spatial distribution of HRSA health centers and HIFLD hospitals |
| `charts/03_isochrone_coverage.png` | Isochrone polygons overlaid on census tract boundaries |
| `charts/04_access_category_map.png` | Final classification map (Adequate / At Risk / Desert / Underserved) |
| `charts/05_score_distributions_and_correlations.png` | Vulnerability score histogram by category + correlation heatmap |

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

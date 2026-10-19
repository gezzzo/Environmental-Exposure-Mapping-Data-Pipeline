# Data Dictionary

CSV files use UTF-8 encoding. An empty field denotes a missing value, not zero. Zone identifiers are strings; dates use ISO format. Parquet exports preserve column types.

| Field | Definition / unit |
|---|---|
| `zone_id` | Stable zone identifier for data linkage; input boundaries define the administrative level |
| `date` | UTC calendar day, YYYY-MM-DD |
| `year` | Calendar year; summaries cover only the requested dates within that year |
| `tmean_c` / `tmax_c` / `tmin_c` | Area-weighted zone mean of daily cell temperatures, °C; for ERA5, daily cell values are the mean, maximum, or minimum of hourly samples |
| `*_spatial_coverage` | Valid temperature raster area divided by total zone area, 0–1 |
| `zone_area_m2` | Total zone area, m² |
| `green_area_m2` | Observed area of the union of selected green-space classes within the zone, m² |
| `green_fraction` | Green-space area divided by total zone area, 0–1; missing when land-use coverage is insufficient |
| `land_coverage` / `land_coverage_ok` | Proportion of zone area covered by the land-use map, 0–1 / whether it meets the coverage threshold |
| `expected_days` | Number of requested days within the year, including both start and end dates |
| `tmean_valid_days` / `tmax_valid_days` / `tmin_valid_days` | Number of days with an accepted spatial mean for each temperature variable |
| `*_temporal_coverage` | Valid days divided by expected days, 0–1 |
| `tmean_mean_c` / `tmax_mean_c` / `tmin_mean_c` | Mean of accepted daily zone values, °C; missing when temporal coverage is insufficient |
| `hot_days_observed` | Number of valid days with zone-average Tmax ≥30°C; not the spatial mean of cell-level exceedance counts |
| `warm_minimum_days_observed` | Number of valid days with zone-average Tmin ≥20°C |
| `hot_days_fraction_valid` / `warm_minimum_days_fraction_valid` | Observed exceedance days divided by valid days for the relevant variable; missing when temporal coverage is insufficient |
| `ndvi_mean` | Area-weighted mean NDVI of accepted clear pixels within the zone for an image date, −1 to 1 |
| `ndvi_clear_coverage` | Valid NDVI area divided by total zone area, 0–1 |
| `ndvi_scene_mean` | Equal-weight mean of accepted scene-level zone NDVI means within the requested period and year |
| `ndvi_valid_scenes` / `ndvi_scenes_available` | Number of accepted scenes / number of available scenes within the requested period |
| `synthetic` | `true` for synthetic demonstration data; `false` for research inputs |
| `landuse_reference_year` | Reference year of the land-use map, fixed for a run |
| `climate_source` | Description of the climate source, time basis, and temperature derivation |

Applying a threshold to zone-average Tmax differs from calculating exceedances in each cell and then averaging spatially. This pipeline implements the former. Do not report `*_observed` fields as complete annual counts when coverage is below 100%.

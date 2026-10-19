"""Daily gridded air temperatures and vector land use -> zone/day and zone/year."""
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import from_bounds, Window
from shapely.geometry import Polygon
from shapely.ops import unary_union


def polygons(path, id_col=None):
    frame = gpd.read_file(path)
    if frame.crs is None:
        raise ValueError(f'Missing CRS: {path}')
    if frame.empty or frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise ValueError(f'Empty geometry: {path}')
    if not frame.geometry.is_valid.all():
        raise ValueError(f'Invalid geometry; repair and document before running: {path}')
    if not frame.geom_type.isin(['Polygon', 'MultiPolygon']).all():
        raise ValueError('Only polygons are supported')
    if id_col and (frame[id_col].isna().any() or frame[id_col].astype(str).duplicated().any()):
        raise ValueError('Zone IDs must be non-null and unique')
    return frame


def land_metrics(zones, land, cfg):
    field = cfg['class_column']
    codes = land[field].astype(str).str.replace(r'\.0$', '', regex=True)
    selected = land.loc[codes.isin([str(x) for x in cfg['green_codes']])]
    # Union prevents double counting when source polygons overlap.
    green = unary_union(selected.geometry)
    covered = unary_union(land.geometry)
    result = []
    for _, zone in zones.iterrows():
        area = zone.geometry.area
        coverage = zone.geometry.intersection(covered).area / area
        green_area = zone.geometry.intersection(green).area
        result.append(dict(zone_id=str(zone[cfg['zone_id_column']]),
                           zone_area_m2=area, green_area_m2=green_area,
                           green_fraction=green_area / area if coverage >= cfg['minimum_coverage'] else np.nan,
                           land_coverage=coverage,
                           land_coverage_ok=coverage >= cfg['minimum_coverage']))
    return pd.DataFrame(result)


def raster_mean(path, zones, unit, minimum, ndvi=False):
    """Exact intersection area weights; no centroid or all-touched approximation."""
    if not ndvi and unit not in ['C', 'K']:
        raise ValueError('Temperature unit must be C or K')
    values = []
    with rasterio.open(path) as src:
        if src.crs is None or src.count != 1:
            raise ValueError('Temperature raster requires CRS and exactly one band')
        if src.transform.b != 0 or src.transform.d != 0:
            raise ValueError('Rotated rasters require preprocessing')
        local = zones.to_crs(src.crs)
        for i, shape in enumerate(local.geometry):
            weights, temps = [], []
            raw_window = from_bounds(*shape.bounds, transform=src.transform)
            c0, r0 = max(0, int(np.floor(raw_window.col_off))), max(0, int(np.floor(raw_window.row_off)))
            c1 = min(src.width, int(np.ceil(raw_window.col_off + raw_window.width)))
            r1 = min(src.height, int(np.ceil(raw_window.row_off + raw_window.height)))
            if c1 > c0 and r1 > r0:
                data = src.read(1, window=Window(c0, r0, c1-c0, r1-r0), masked=True)
                mask = np.ma.getmaskarray(data)
                for r, c in np.ndindex(data.shape):
                    if mask[r, c]:
                        continue
                    t = float(data[r, c]) * src.scales[0] + src.offsets[0]
                    if unit == 'K':
                        t -= 273.15
                    if not np.isfinite(t):
                        continue
                    if not ((-1 <= t <= 1) if ndvi else (-90 <= t <= 65)):
                        raise ValueError(f'Implausible air temperature in {path}: {t}')
                    corners = [src.transform * (c+c0+dc, r+r0+dr) for dc, dr in [(0,0),(1,0),(1,1),(0,1)]]
                    piece = shape.intersection(Polygon(corners))
                    if piece.is_empty or piece.area == 0:
                        continue
                    area = gpd.GeoSeries([piece], crs=src.crs).to_crs(zones.crs).area.iloc[0]
                    weights.append(area)
                    temps.append(t)
            coverage = min(1.0, sum(weights) / zones.geometry.iloc[i].area)
            mean = np.average(temps, weights=weights) if weights and coverage >= minimum else np.nan
            values.append((float(mean), coverage))
    return values


def summarize(daily, start, end, minimum):
    rows = []
    for (zone, year), group in daily.groupby(['zone_id', daily.date.dt.year]):
        expected = len(pd.date_range(max(start, pd.Timestamp(year,1,1)), min(end, pd.Timestamp(year,12,31))))
        row = dict(zone_id=zone, year=int(year), expected_days=expected)
        for variable in ['tmean', 'tmax', 'tmin']:
            series = group[f'{variable}_c']
            n = int(series.count())
            ok = n / expected >= minimum
            row[f'{variable}_valid_days'] = n
            row[f'{variable}_temporal_coverage'] = n / expected
            row[f'{variable}_mean_c'] = float(series.mean()) if ok else np.nan
            if variable in ['tmax', 'tmin']:
                label, threshold = ('hot_days', 30) if variable == 'tmax' else ('warm_minimum_days', 20)
                row[f'{label}_observed'] = int((series.dropna() >= threshold).sum()) if n else np.nan
                row[f'{label}_fraction_valid'] = float((series.dropna() >= threshold).mean()) if ok and n else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def run(config_path):
    config_path = Path(config_path).resolve()
    cfg = json.loads(config_path.read_text())
    base = config_path.parent
    resolve = lambda p: (base / p).resolve()
    minimum = cfg.get('minimum_coverage', .9)
    if not 0 < minimum <= 1:
        raise ValueError('minimum_coverage must be in (0,1]')
    cfg['minimum_coverage'] = minimum
    start, end = pd.Timestamp(cfg['start']), pd.Timestamp(cfg['end'])
    if start != start.normalize() or end != end.normalize() or start > end:
        raise ValueError('Invalid date interval')
    zones = polygons(resolve(cfg['zones']), cfg['zone_id_column']).to_crs(3035)
    if any(zones.geometry.area <= 0):
        raise ValueError('Zones must have positive area')
    zones[cfg['zone_id_column']] = zones[cfg['zone_id_column']].astype(str)
    land = polygons(resolve(cfg['landuse'])).to_crs(3035)
    green = land_metrics(zones, land, cfg)
    manifest_path = resolve(cfg['climate_manifest'])
    manifest = pd.read_csv(manifest_path, dtype={'variable':str, 'unit':str})
    manifest['date'] = pd.to_datetime(manifest['date'], errors='raise')
    if manifest.empty or manifest[['date','variable','path','unit']].isna().any().any():
        raise ValueError('Manifest cannot be empty or contain missing fields')
    if not manifest.date.eq(manifest.date.dt.normalize()).all():
        raise ValueError('Manifest dates must be calendar dates')
    if manifest.duplicated(['date','variable']).any():
        raise ValueError('Duplicate date/variable rasters')
    if not manifest.variable.isin(['tmean','tmax','tmin']).all():
        raise ValueError('Unknown temperature variable')
    dates = pd.date_range(start,end)
    daily = pd.MultiIndex.from_product([green.zone_id,dates], names=['zone_id','date']).to_frame(index=False)
    for var in ['tmean','tmax','tmin']:
        daily[f'{var}_c'] = np.nan
        daily[f'{var}_spatial_coverage'] = 0.0
    inputs = [config_path,resolve(cfg['zones']),resolve(cfg['landuse']),manifest_path]
    for entry in manifest.itertuples():
        if not start <= entry.date <= end:
            continue
        path = (manifest_path.parent / entry.path).resolve()
        inputs.append(path)
        stats = raster_mean(path,zones,entry.unit,minimum)
        selection = daily.date.eq(entry.date)
        daily.loc[selection, f'{entry.variable}_c'] = [s[0] for s in stats]
        daily.loc[selection, f'{entry.variable}_spatial_coverage'] = [s[1] for s in stats]
    for lower, upper in [('tmin','tmean'),('tmean','tmax'),('tmin','tmax')]:
        if (daily[f'{lower}_c'] > daily[f'{upper}_c'] + .01).any():
            raise ValueError(f'Inconsistent daily temperatures: {lower} > {upper}')
    ndvi_rows = []
    if cfg.get('ndvi_manifest'):
        ndvi_manifest = resolve(cfg['ndvi_manifest'])
        inputs.append(ndvi_manifest)
        scenes = pd.read_csv(ndvi_manifest)
        scenes['date'] = pd.to_datetime(scenes['date'], errors='raise')
        if scenes.empty or scenes[['date','path']].isna().any().any() or scenes.date.duplicated().any():
            raise ValueError('NDVI manifest needs unique dates and non-null paths; mosaic same-day tiles first')
        for scene in scenes.itertuples():
            if not start <= scene.date <= end:
                continue
            path = (ndvi_manifest.parent / scene.path).resolve()
            inputs.append(path)
            for zone_id, (mean, coverage) in zip(green.zone_id, raster_mean(path,zones,'NDVI',minimum,ndvi=True)):
                ndvi_rows.append(dict(zone_id=zone_id,date=scene.date,ndvi_mean=mean,ndvi_clear_coverage=coverage))
    annual = summarize(daily,start,end,minimum).merge(green,on='zone_id',validate='many_to_one')
    ndvi_table = pd.DataFrame(ndvi_rows,columns=['zone_id','date','ndvi_mean','ndvi_clear_coverage'])
    if len(ndvi_table):
        ndvi_table['year'] = ndvi_table.date.dt.year
        summary = ndvi_table.groupby(['zone_id','year']).agg(ndvi_scene_mean=('ndvi_mean','mean'),
                    ndvi_valid_scenes=('ndvi_mean','count'),ndvi_scenes_available=('date','count')).reset_index()
        annual = annual.merge(summary,on=['zone_id','year'],how='left',validate='one_to_one')
    daily = daily.merge(green,on='zone_id',validate='many_to_one')
    for table in [daily,annual]:
        table['synthetic'] = bool(cfg.get('synthetic',False))
        table['landuse_reference_year'] = cfg['landuse_reference_year']
        table['climate_source'] = cfg['climate_source']
    out = resolve(cfg['output'])
    out.mkdir(parents=True,exist_ok=True)
    ndvi_table['synthetic'] = bool(cfg.get('synthetic', False))
    ndvi_table.to_csv(out/'ndvi_zone_scene.csv',index=False)
    for name, table in [('exposure_daily',daily),('exposure_annual',annual)]:
        table.to_csv(out / f'{name}.csv',index=False)
        table.to_parquet(out / f'{name}.parquet',index=False)
    mapped = zones.rename(columns={cfg['zone_id_column']:'zone_id'})[['zone_id','geometry']].merge(annual,on='zone_id')
    mapped.to_file(out / 'exposure.gpkg',layer='zone_year',driver='GPKG')
    preparations = [resolve(p) for p in cfg.get('preparation_records', [])]
    inputs.extend(preparations)
    provenance = dict(preparation_records=[json.loads(p.read_text()) for p in preparations], created_utc=datetime.now(timezone.utc).isoformat(),config=cfg,
                      inputs=[dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in dict.fromkeys(inputs)],
                      versions={m.__name__:m.__version__ for m in [gpd,np,pd,rasterio]})
    (out/'provenance.json').write_text(json.dumps(provenance,indent=2,ensure_ascii=False))
    print(f'Wrote {len(daily)} zone-days and {len(annual)} zone-years to {out}')
    return daily, annual


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config',type=Path)
    run(parser.parse_args().config)

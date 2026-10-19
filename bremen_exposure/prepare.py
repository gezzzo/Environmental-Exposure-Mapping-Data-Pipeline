"""Adapters for ERA5 NetCDF and Sentinel-2 L2A bands (no account required)."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling
import xarray as xr


def provenance(out, paths, parameters):
    Path(out).write_text(json.dumps(dict(parameters=parameters, inputs=[
        dict(path=str(Path(p).resolve()),sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
        for p in paths]),indent=2))


def write_raster(path, array, profile):
    profile.update(driver='GTiff',count=1,dtype='float32',nodata=-9999,compress='deflate')
    with rasterio.open(path,'w',**profile) as dst:
        dst.write(np.where(np.isfinite(array),array,-9999).astype('float32'),1)


def era5(source, output, variable='t2m'):
    """UTC days; mean/max/min of 24 hourly instantaneous values, not true extrema."""
    output = Path(output)
    output.mkdir(parents=True,exist_ok=True)
    with xr.open_dataset(source) as ds:
        field = ds[variable]
        if 'valid_time' in field.dims:
            field = field.rename(valid_time='time')
        if set(field.dims) != {'time','latitude','longitude'}:
            raise ValueError('Expected time/valid_time, latitude, longitude; select expver/member first')
        if field.attrs.get('units') not in ['K','kelvin']:
            raise ValueError('ERA5 input must declare Kelvin units')
        field = field.sortby('latitude',ascending=False).sortby('longitude')
        lon, lat = field.longitude.values, field.latitude.values
        if len(lon)<2 or len(lat)<2 or lon.max()>180:
            raise ValueError('Supply a regional regular grid with >=2 rows/columns and -180..180 longitude')
        dx, dy = np.diff(lon), -np.diff(lat)
        if not (np.all(dx>0) and np.all(dy>0) and np.allclose(dx,dx[0]) and np.allclose(dy,dy[0])):
            raise ValueError('Nonregular or duplicate spatial coordinates')
        field = field.sortby('time')
        times = pd.DatetimeIndex(field.time.values)
        if times.empty or times.has_duplicates or not times.equals(times.floor('h')):
            raise ValueError('Unique exact hourly timestamps required')
        # Incomplete days/cells stay missing. No filling or extrapolation.
        transform = from_origin(lon[0]-dx[0]/2,lat[0]+dy[0]/2,dx[0],dy[0])
        profile = dict(height=len(lat),width=len(lon),crs='EPSG:4326',transform=transform)
        rows = []
        for day, sample in field.resample(time='1D'):
            day = pd.Timestamp(day)
            expected = pd.date_range(day,periods=24,freq='h')
            actual = pd.DatetimeIndex(sample.time.values)
            data = sample.transpose('time','latitude','longitude').values - 273.15
            if not actual.equals(expected):
                data = np.full((24,len(lat),len(lon)),np.nan)
            for name, func in [('tmean',np.mean),('tmax',np.max),('tmin',np.min)]:
                path = output/f'{day:%Y%m%d}_{name}.tif'
                write_raster(path,func(data,axis=0),profile.copy())
                rows.append(dict(date=f'{day:%Y-%m-%d}',variable=name,path=path.name,unit='C'))
        pd.DataFrame(rows).to_csv(output/'climate.csv',index=False)
    provenance(output/'preparation.json',[source],dict(method='ERA5 hourly statistics',variable=variable,day_basis='UTC',required_hours=24))


def sentinel(red_path,nir_path,scl_path,output,scale,red_offset,nir_offset):
    """B04/B08 aligned reflectance; align SCL with nearest-neighbour sampling."""
    if scale <= 0:
        raise ValueError('Scale must be positive')
    with rasterio.open(red_path) as red, rasterio.open(nir_path) as nir, rasterio.open(scl_path) as scl:
        if not red.crs or not nir.crs or not scl.crs:
            raise ValueError('All bands require a CRS')
        if (red.crs,red.transform,red.shape)!=(nir.crs,nir.transform,nir.shape):
            raise ValueError('B04 and B08 grids must match; resample before running')
        r = red.read(1,masked=True).filled(np.nan).astype(float) if red.dtypes[0].startswith('float') else red.read(1).astype(float)
        n = nir.read(1).astype(float)
        valid = (red.read_masks(1)>0)&(nir.read_masks(1)>0)&(r!=0)&(n!=0)
        classes = np.zeros(red.shape,dtype='uint8')
        reproject(source=rasterio.band(scl,1),destination=classes,src_transform=scl.transform,
                  src_crs=scl.crs,dst_transform=red.transform,dst_crs=red.crs,
                  src_nodata=scl.nodata,dst_nodata=0,resampling=Resampling.nearest)
        # Conservative clear vegetation, bare land, water only; excludes cloud/shadow/snow/unknown.
        valid &= np.isin(classes,[4,5,6])
        r, n = (r+red_offset)*scale, (n+nir_offset)*scale
        valid &= np.isfinite(r)&np.isfinite(n)&(r>=0)&(n>=0)&((r+n)>0)
        ndvi = np.full(red.shape,np.nan)
        np.divide(n-r,n+r,out=ndvi,where=valid)
        output = Path(output)
        output.parent.mkdir(parents=True,exist_ok=True)
        write_raster(output,ndvi,red.profile.copy())
    provenance(str(output)+'.json',[red_path,nir_path,scl_path],dict(scale=scale,red_offset=red_offset,nir_offset=nir_offset,clear_scl=[4,5,6]))


if __name__=='__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command',required=True)
    e = sub.add_parser('era5')
    e.add_argument('source'); e.add_argument('output'); e.add_argument('--variable',default='t2m')
    s = sub.add_parser('sentinel')
    for name in ['red_path','nir_path','scl_path','output']:
        s.add_argument(name)
    s.add_argument('--scale',type=float,required=True)
    s.add_argument('--red-offset',type=float,required=True)
    s.add_argument('--nir-offset',type=float,required=True)
    args = vars(p.parse_args()); command=args.pop('command')
    (era5 if command=='era5' else sentinel)(**args)

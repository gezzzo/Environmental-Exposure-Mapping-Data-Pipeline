"""Generate a reproducible SYNTHETIC example near Bremen, not official boundaries."""
import json
from pathlib import Path
import numpy as np
import pandas as pd
import geopandas as gpd
import xarray as xr
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box
from .prepare import era5, sentinel
from .pipeline import run


def main(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    data = root/'data/demo'
    data.mkdir(parents=True,exist_ok=True)
    zones = gpd.GeoDataFrame({'zone_id':['DEMO_WEST','DEMO_EAST']},geometry=[box(8.7,53.05,8.8,53.15),box(8.8,53.05,8.9,53.15)],crs=4326)
    zones.to_file(data/'zones.geojson',driver='GeoJSON')
    land = gpd.GeoDataFrame({'code':['14100','11100','31000','11100']},geometry=[box(8.7,53.05,8.75,53.15),box(8.75,53.05,8.8,53.15),box(8.8,53.05,8.88,53.15),box(8.88,53.05,8.9,53.15)],crs=4326)
    land.to_file(data/'land.geojson',driver='GeoJSON')
    times = pd.date_range('2023-07-01',periods=72,freq='h')
    temp = 273.15 + 25 + 7*np.sin((np.arange(72)%24-8)*2*np.pi/24)
    values = temp[:,None,None] + np.array([[0.,1.],[0.,1.]])[None,:,:]
    xr.Dataset({'t2m':(('time','latitude','longitude'),values,{'units':'K'})},coords={'time':times,'latitude':[53.125,53.075],'longitude':[8.75,8.85]}).to_netcdf(data/'era5_synthetic.nc')
    era5(data/'era5_synthetic.nc',data/'climate')
    # Coarse pixels keep this exact-intersection example quick; not Sentinel spatial resolution.
    profile=dict(driver='GTiff',width=4,height=2,count=1,crs=4326,transform=from_origin(8.7,53.15,.05,.05),dtype='uint16',nodata=0)
    for name, arr in [('B04',np.full((2,4),2000)),('B08',np.tile([3000,3000,6000,6000],(2,1))),('SCL',np.full((2,4),4))]:
        with rasterio.open(data/f'{name}.tif','w',**profile) as dst:
            dst.write(arr.astype('uint16'),1)
    sentinel(data/'B04.tif',data/'B08.tif',data/'SCL.tif',data/'ndvi.tif',.0001,0,0)
    pd.DataFrame([dict(date='2023-07-02',path='ndvi.tif')]).to_csv(data/'ndvi.csv',index=False)
    cfg=dict(zones='zones.geojson',zone_id_column='zone_id',landuse='land.geojson',class_column='code',green_codes=['14100','31000'],landuse_reference_year=2023,climate_manifest='climate/climate.csv',ndvi_manifest='ndvi.csv',preparation_records=['climate/preparation.json','ndvi.tif.json'],climate_source='SYNTHETIC ERA5-like example; not observations',start='2023-07-01',end='2023-07-03',minimum_coverage=.9,output='../../outputs/demo',synthetic=True)
    (data/'config.json').write_text(json.dumps(cfg,indent=2))
    run(data/'config.json')

if __name__=='__main__':
    main()

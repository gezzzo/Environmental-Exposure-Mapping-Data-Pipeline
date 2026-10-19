import numpy as np
import pandas as pd
import geopandas as gpd
import pytest
import rasterio
import xarray as xr
from rasterio.transform import from_origin
from shapely.geometry import box
from bremen_exposure.pipeline import land_metrics, raster_mean, summarize
from bremen_exposure.prepare import era5, sentinel


def raster(path,data,nodata=-9999):
    with rasterio.open(path,'w',driver='GTiff',width=data.shape[1],height=data.shape[0],count=1,dtype='float32',crs=3035,transform=from_origin(0,2,1,1),nodata=nodata) as dst:
        dst.write(data.astype('float32'),1)


def test_exact_area_and_nodata(tmp_path):
    path=tmp_path/'temp.tif'
    raster(path,np.array([[10,30],[10,-9999]]))
    zones=gpd.GeoDataFrame(geometry=[box(.5,1,2,2),box(0,0,2,2)],crs=3035)
    result=raster_mean(path,zones,'C',.8)
    assert result[0][0]==pytest.approx(70/3)
    assert result[1][1]==pytest.approx(.75)
    assert np.isnan(result[1][0])


def test_land_union_and_incomplete():
    zones=gpd.GeoDataFrame({'id':['001']},geometry=[box(0,0,2,2)],crs=3035)
    land=gpd.GeoDataFrame({'code':['14100','14100']},geometry=[box(0,0,1,2)]*2,crs=3035)
    result=land_metrics(zones,land,dict(class_column='code',green_codes=['14100'],zone_id_column='id',minimum_coverage=.9))
    assert result.green_area_m2.iloc[0]==2
    assert result.land_coverage.iloc[0]==.5
    assert np.isnan(result.green_fraction.iloc[0])


def test_missing_days_and_threshold():
    daily=pd.DataFrame({'zone_id':['a']*3,'date':pd.date_range('2023-01-01',periods=3),'tmean_c':[25,np.nan,22],'tmax_c':[30,np.nan,29],'tmin_c':[20,np.nan,15]})
    out=summarize(daily,pd.Timestamp('2023-01-01'),pd.Timestamp('2023-01-03'),.9).iloc[0]
    assert out.hot_days_observed==1
    assert out.warm_minimum_days_observed==1
    assert np.isnan(out.hot_days_fraction_valid)
    assert out.tmax_temporal_coverage==pytest.approx(2/3)


def test_era5_incomplete_day(tmp_path):
    source=tmp_path/'era.nc'
    times=pd.date_range('2023-01-01',periods=23,freq='h')
    xr.Dataset({'t2m':(('time','latitude','longitude'),np.full((23,2,2),300.),{'units':'K'})},coords={'time':times,'latitude':[54,53],'longitude':[8,9]}).to_netcdf(source)
    era5(source,tmp_path/'out')
    with rasterio.open(tmp_path/'out/20230101_tmax.tif') as src:
        assert src.read(1,masked=True).mask.all()


def test_sentinel_cloud_and_offset(tmp_path):
    for name,data in [('red',[[2000,2000]]),('nir',[[4000,4000]]),('scl',[[4,9]])]:
        raster(tmp_path/f'{name}.tif',np.array(data))
    sentinel(tmp_path/'red.tif',tmp_path/'nir.tif',tmp_path/'scl.tif',tmp_path/'ndvi.tif',.0001,-1000,-1000)
    with rasterio.open(tmp_path/'ndvi.tif') as src:
        data=src.read(1,masked=True)
        assert data[0,0]==pytest.approx(.5)
        assert data.mask[0,1]


def test_end_to_end_demo(tmp_path):
    from bremen_exposure.demo import main
    main(tmp_path)
    out=tmp_path/'outputs/demo'
    daily=pd.read_csv(out/'exposure_daily.csv')
    annual=pd.read_parquet(out/'exposure_annual.parquet').set_index('zone_id')
    assert len(daily)==6 and len(annual)==2
    assert not daily.duplicated(['zone_id','date']).any()
    assert daily.synthetic.all()
    assert annual.loc['DEMO_WEST','green_fraction']==pytest.approx(.5,abs=.001)
    assert annual.loc['DEMO_EAST','green_fraction']==pytest.approx(.8,abs=.001)
    assert annual.loc['DEMO_WEST','ndvi_scene_mean']==pytest.approx(.2)
    assert annual.loc['DEMO_EAST','ndvi_scene_mean']==pytest.approx(.5)
    assert annual.hot_days_observed.tolist()==[3,3]
    assert annual.tmax_valid_days.tolist()==[3,3]
    assert len(gpd.read_file(out/'exposure.gpkg'))==2

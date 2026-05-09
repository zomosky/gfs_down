"""Wind speed visualization from sliced GRIB2 data."""

import glob
import logging
from pathlib import Path

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import numpy as np
import xarray as xr

logger = logging.getLogger(__name__)


def clean_cfgrib_index(path: Path):
    """Remove stale cfgrib index cache files for a given GRIB file."""
    for f in glob.glob(str(path) + ".*.idx"):
        Path(f).unlink(missing_ok=True)


def read_grib_variable(path: Path, short_name: str):
    """Read a specific variable from a GRIB2 file using cfgrib."""
    clean_cfgrib_index(path)
    return xr.open_dataset(
        path,
        engine="cfgrib",
        backend_kwargs={"filter_by_keys": {"shortName": short_name}},
    )


def compute_wind_speed(u_path: Path, v_path: Path, var_name: str = "u10"):
    """Compute wind speed from UGRD and VGRD GRIB2 files.

    Handles the case where both variables are in the same file or separate files.
    """
    if u_path == v_path:
        # Both variables in same file — cfgrib maps UGRD/10m -> u10, VGRD/10m -> v10
        clean_cfgrib_index(u_path)
        ds = xr.open_dataset(u_path, engine="cfgrib")
        u_key = None
        v_key = None
        for name in ds.data_vars:
            if name.startswith("u") and "10" in name.lower():
                u_key = name
            elif name.startswith("v") and "10" in name.lower():
                v_key = name

        if u_key is None or v_key is None:
            # Fallback: just take the first two variables
            vars_list = list(ds.data_vars.keys())
            if len(vars_list) >= 2:
                u_key, v_key = vars_list[0], vars_list[1]
            else:
                raise ValueError(f"Could not find UGRD/VGRD variables in {u_path}")

        u = ds[u_key]
        v = ds[v_key]
    else:
        ds_u = xr.open_dataset(u_path, engine="cfgrib")
        ds_v = xr.open_dataset(v_path, engine="cfgrib")
        u = list(ds_u.data_vars.values())[0]
        v = list(ds_v.data_vars.values())[0]

    wind_speed = np.sqrt(u**2 + v**2)
    return wind_speed


def plot_wind_speed(
    wind_data,
    date: str,
    cycle: int,
    forecast_hour: int,
    output_path: Path,
    region=None,
    colormap: str = "viridis",
    dpi: int = 300,
    output_format: str = "png",
):
    """Plot wind speed on a map with coastlines."""
    data = wind_data

    # Apply region subset if specified
    if region:
        lat_slice = slice(region.lat_max, region.lat_min)
        lon_slice = slice(region.lon_min, region.lon_max)
        data = data.sel(latitude=lat_slice, longitude=lon_slice)

    fig = plt.figure(figsize=(12, 8))
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())

    # Add geographic features
    ax.add_feature(cfeature.COASTLINE, linewidth=0.8)
    ax.add_feature(cfeature.BORDERS, linewidth=0.5)
    ax.add_feature(cfeature.OCEAN, zorder=0)
    ax.add_feature(cfeature.LAND, zorder=0, color="lightgray")

    # Plot wind speed
    levels = np.linspace(0, 30, 31)
    cf = ax.contourf(
        data.longitude, data.latitude, data.values,
        levels=levels,
        cmap=colormap,
        transform=ccrs.PlateCarree(),
        zorder=1,
    )

    plt.colorbar(cf, ax=ax, label="Wind Speed (m/s)", shrink=0.8)

    ax.set_title(
        f"GFS Wind Speed (10m) | {date} {cycle:02d}Z | "
        f"Forecast Hour +{forecast_hour:03d}",
        fontsize=14,
        pad=15,
    )

    ax.set_extent(
        [data.longitude.min(), data.longitude.max(),
         data.latitude.min(), data.latitude.max()],
        crs=ccrs.PlateCarree(),
    )

    ax.gridlines(draw_labels=True, linewidth=0.5, color="gray", alpha=0.5)

    out_file = output_path.with_suffix(f".{output_format}")
    fig.savefig(out_file, dpi=dpi, bbox_inches="tight")
    plt.close(fig)

    logger.info(f"Saved wind speed plot: {out_file}")
    return out_file

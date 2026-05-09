"""Configuration loading and validation for GFS downloader."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class RegionConfig:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


@dataclass
class VariableConfig:
    name: str
    level: str


@dataclass
class ForecastRange:
    start: int
    end: int
    step: int

    @property
    def hours(self):
        return list(range(self.start, self.end + 1, self.step))


@dataclass
class PlotConfig:
    enabled: bool = True
    plot_type: str = "wind_speed"
    output_format: str = "png"
    dpi: int = 300
    colormap: str = "viridis"


@dataclass
class GFSConfig:
    date: str
    cycle: int
    forecast_hours: ForecastRange
    variables: list[VariableConfig]
    region: Optional[RegionConfig]
    output_dir: Path
    plot: PlotConfig


VALID_CYCLES = (0, 6, 12, 18)


def load_config(path: str | Path) -> GFSConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    date = raw["date"]
    cycle = raw["cycle"]
    if cycle not in VALID_CYCLES:
        raise ValueError(f"Cycle must be one of {VALID_CYCLES}, got {cycle}")

    fh = raw["forecast_hours"]
    forecast_range = ForecastRange(
        start=int(fh["start"]),
        end=int(fh["end"]),
        step=int(fh["step"]),
    )

    variables = [
        VariableConfig(name=v["name"], level=v["level"])
        for v in raw["variables"]
    ]

    region = None
    if "region" in raw and raw["region"] is not None:
        r = raw["region"]
        region = RegionConfig(
            lat_min=float(r["lat_min"]),
            lat_max=float(r["lat_max"]),
            lon_min=float(r["lon_min"]),
            lon_max=float(r["lon_max"]),
        )

    output_dir = Path(raw.get("output_dir", "./output"))

    plot_raw = raw.get("plot", {})
    plot = PlotConfig(
        enabled=plot_raw.get("enabled", True),
        plot_type=plot_raw.get("type", "wind_speed"),
        output_format=plot_raw.get("output_format", "png"),
        dpi=plot_raw.get("dpi", 300),
        colormap=plot_raw.get("colormap", "viridis"),
    )

    return GFSConfig(
        date=date,
        cycle=cycle,
        forecast_hours=forecast_range,
        variables=variables,
        region=region,
        output_dir=output_dir,
        plot=plot,
    )

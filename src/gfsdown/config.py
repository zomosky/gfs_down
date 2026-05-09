"""Configuration loading and validation for GFS downloader."""

from dataclasses import dataclass, field
from datetime import date as _date, datetime, timedelta
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
class DateRange:
    start: str  # "YYYY-MM-DD"
    end: str    # "YYYY-MM-DD" inclusive
    step_days: int = 1

    @property
    def dates(self) -> list[str]:
        d0 = datetime.strptime(self.start, "%Y-%m-%d").date()
        d1 = datetime.strptime(self.end, "%Y-%m-%d").date()
        if d1 < d0:
            raise ValueError(f"date_range.end ({self.end}) is before start ({self.start})")
        if self.step_days <= 0:
            raise ValueError(f"date_range.step_days must be > 0, got {self.step_days}")
        out = []
        d = d0
        while d <= d1:
            out.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=self.step_days)
        return out


@dataclass
class PlotConfig:
    enabled: bool = True
    plot_type: str = "wind_speed"
    output_format: str = "png"
    dpi: int = 300
    colormap: str = "viridis"


@dataclass
class GFSConfig:
    date: str  # primary / first date — kept for backward compat (e.g. plot titles)
    cycle: int  # primary / first cycle — kept for list-vars / list-hours single-shot use
    forecast_hours: Optional[ForecastRange]  # None when all_hours=True
    variables: list[VariableConfig]
    region: Optional[RegionConfig]
    output_dir: Path
    plot: PlotConfig
    date_range: Optional[DateRange] = None
    dates: list[str] = field(default_factory=list)
    cycles: list[int] = field(default_factory=list)
    all_hours: bool = False  # when True, slicer auto-discovers every f-hour per init


VALID_CYCLES = (0, 6, 12, 18)


def _normalize_cycles(raw_value) -> list[int]:
    """Accept an int or list of ints; validate each is in VALID_CYCLES; dedupe + sort."""
    if isinstance(raw_value, (list, tuple)):
        items = [int(c) for c in raw_value]
    else:
        items = [int(raw_value)]
    if not items:
        raise ValueError("'cycle'/'cycles' must contain at least one value")
    for c in items:
        if c not in VALID_CYCLES:
            raise ValueError(f"Cycle must be one of {VALID_CYCLES}, got {c}")
    # dedupe while preserving sorted order for determinism
    return sorted(set(items))


def load_config(path: str | Path) -> GFSConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f)

    # Resolve cycles: prefer plural `cycles` list, else fall back to singular `cycle`.
    if "cycles" in raw and raw["cycles"] is not None:
        cycles = _normalize_cycles(raw["cycles"])
    elif "cycle" in raw and raw["cycle"] is not None:
        cycles = _normalize_cycles(raw["cycle"])
    else:
        raise ValueError("Config must define either 'cycle' or 'cycles'")
    cycle = cycles[0]

    # Resolve dates: prefer date_range when present, otherwise fall back to single date.
    date_range = None
    if "date_range" in raw and raw["date_range"] is not None:
        dr = raw["date_range"]
        date_range = DateRange(
            start=str(dr["start"]),
            end=str(dr["end"]),
            step_days=int(dr.get("step_days", 1)),
        )
        dates = date_range.dates
    elif "date" in raw and raw["date"] is not None:
        dates = [str(raw["date"])]
    else:
        raise ValueError("Config must define either 'date' or 'date_range'")
    date = dates[0]

    # forecast_hours: either a dict {start, end, step} or the string "all"
    # (when "all", the slicer discovers every available hour per init via S3 LIST).
    fh = raw.get("forecast_hours")
    all_hours = False
    forecast_range: Optional[ForecastRange] = None
    if isinstance(fh, str) and fh.strip().lower() == "all":
        all_hours = True
    elif isinstance(fh, dict):
        forecast_range = ForecastRange(
            start=int(fh["start"]),
            end=int(fh["end"]),
            step=int(fh["step"]),
        )
    elif fh is None:
        raise ValueError("Config must define 'forecast_hours' (a {start,end,step} block or 'all')")
    else:
        raise ValueError(
            f"'forecast_hours' must be a {{start,end,step}} mapping or the string 'all', got {fh!r}"
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
        date_range=date_range,
        dates=dates,
        cycles=cycles,
        all_hours=all_hours,
    )

"""Orchestration: iterate forecast hours, parse idx, filter variables, download sliced GRIB2."""

import logging
from pathlib import Path

from gfsdown.config import GFSConfig
from gfsdown.downloader import (
    build_grib_url,
    build_idx_url,
    download_sliced_grib,
    download_text,
    get_file_size,
    list_available_forecast_hours,
)
from gfsdown.index_parser import filter_entries, parse_idx

logger = logging.getLogger(__name__)


def date_output_dir(base_dir: Path, date: str, cycle: int) -> Path:
    """Per-date / per-cycle output directory: <base>/<YYYYMMDD>/<CC>z/."""
    return base_dir / date.replace("-", "") / f"{cycle:02d}z"


def download_forecast_hour(
    config: GFSConfig,
    date: str,
    cycle: int,
    forecast_hour: int,
    output_dir: Path,
) -> Path | None:
    """Download sliced GRIB2 for one (date, cycle, forecast_hour).

    Returns the output file path, or None if no matching variables found.
    """
    idx_url = build_idx_url(date, cycle, forecast_hour)
    grib_url = build_grib_url(date, cycle, forecast_hour)

    logger.info(f"Fetching index for {date} {cycle:02d}Z f{forecast_hour:03d}: {Path(idx_url).name}")

    try:
        idx_text = download_text(idx_url)
    except Exception as e:
        logger.error(f"Failed to download idx for {date} {cycle:02d}Z f{forecast_hour:03d}: {e}")
        return None

    entries = parse_idx(idx_text)
    if not entries:
        logger.warning(f"No entries parsed from idx for {date} {cycle:02d}Z f{forecast_hour:03d}")
        return None

    # Collect all byte ranges needed for this forecast hour
    all_ranges = []
    for var_cfg in config.variables:
        matched = filter_entries(entries, var_cfg.name, var_cfg.level)
        if matched:
            logger.info(f"  Found {var_cfg.name} at '{var_cfg.level}': "
                       f"{len(matched)} record(s)")
            for entry in matched:
                all_ranges.append((entry.byte_offset, entry.byte_end - 1))
        else:
            logger.warning(f"  Variable {var_cfg.name} at '{var_cfg.level}' "
                         f"not found in {date} {cycle:02d}Z f{forecast_hour:03d}")

    if not all_ranges:
        return None

    output_path = output_dir / f"gfs_f{forecast_hour:03d}.grib2"
    download_sliced_grib(all_ranges, grib_url, output_path)

    file_size = output_path.stat().st_size
    logger.info(f"  Saved {output_path.relative_to(output_path.parents[2]) if len(output_path.parents) >= 3 else output_path.name} ({file_size:,} bytes)")

    return output_path


def download_all(config: GFSConfig) -> list[tuple[str, int, Path]]:
    """Download all (date, cycle, forecast_hour) combinations specified in config.

    Files are organised under <output_dir>/<YYYYMMDD>/<CC>z/gfs_fXXX.grib2,
    so different cycles for the same date never collide.

    Returns list of (date, cycle, path) tuples for successfully downloaded files.
    """
    base_dir = Path(config.output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    dates = config.dates or [config.date]
    cycles = config.cycles or [config.cycle]

    if config.all_hours:
        logger.info(
            f"Downloading {len(dates)} date(s) × {len(cycles)} cycle(s) × ALL forecast hours "
            f"(cycles={','.join(f'{c:02d}Z' for c in cycles)}; hours discovered per init)"
        )
    else:
        hours = config.forecast_hours.hours
        total = len(dates) * len(cycles) * len(hours)
        logger.info(
            f"Downloading {total} file(s): {len(dates)} date(s) × {len(cycles)} cycle(s) "
            f"× {len(hours)} forecast hour(s) "
            f"(cycles={','.join(f'{c:02d}Z' for c in cycles)}, "
            f"f{hours[0]:03d}-f{hours[-1]:03d})"
        )

    downloaded: list[tuple[str, int, Path]] = []
    for date in dates:
        for cycle in cycles:
            out_dir = date_output_dir(base_dir, date, cycle)
            out_dir.mkdir(parents=True, exist_ok=True)

            if config.all_hours:
                try:
                    hours = list_available_forecast_hours(date, cycle)
                except Exception as e:
                    logger.error(f"Failed S3 LIST for {date} {cycle:02d}Z, skipping: {e}")
                    continue
                if not hours:
                    logger.warning(f"No forecast files found on S3 for {date} {cycle:02d}Z, skipping")
                    continue
                logger.info(
                    f"=== {date} {cycle:02d}Z -> {out_dir}  "
                    f"(all-hours: {len(hours)} files, f{hours[0]:03d}-f{hours[-1]:03d}) ==="
                )
            else:
                hours = config.forecast_hours.hours
                logger.info(f"=== {date} {cycle:02d}Z -> {out_dir} ===")

            for hour in hours:
                result = download_forecast_hour(config, date, cycle, hour, out_dir)
                if result:
                    downloaded.append((date, cycle, result))

    logger.info(f"Downloaded {len(downloaded)} file(s)")
    return downloaded

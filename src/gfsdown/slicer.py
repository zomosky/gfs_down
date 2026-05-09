"""Orchestration: iterate forecast hours, parse idx, filter variables, download sliced GRIB2."""

import logging
from pathlib import Path

from gfsdown.config import GFSConfig
from gfsdown.downloader import build_grib_url, build_idx_url, download_text, download_sliced_grib, get_file_size
from gfsdown.index_parser import filter_entries, parse_idx

logger = logging.getLogger(__name__)


def download_forecast_hour(
    config: GFSConfig,
    forecast_hour: int,
    output_dir: Path,
) -> Path | None:
    """Download sliced GRIB2 for one forecast hour.

    Returns the output file path, or None if no matching variables found.
    """
    idx_url = build_idx_url(config.date, config.cycle, forecast_hour)
    grib_url = build_grib_url(config.date, config.cycle, forecast_hour)

    logger.info(f"Fetching index for f{forecast_hour:03d}: {Path(idx_url).name}")

    try:
        idx_text = download_text(idx_url)
    except Exception as e:
        logger.error(f"Failed to download idx for f{forecast_hour:03d}: {e}")
        return None

    entries = parse_idx(idx_text)
    if not entries:
        logger.warning(f"No entries parsed from idx for f{forecast_hour:03d}")
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
                         f"not found in f{forecast_hour:03d}")

    if not all_ranges:
        return None

    output_path = output_dir / f"gfs_f{forecast_hour:03d}.grib2"
    download_sliced_grib(all_ranges, grib_url, output_path)

    file_size = output_path.stat().st_size
    logger.info(f"  Saved {output_path.name} ({file_size:,} bytes)")

    return output_path


def download_all(config: GFSConfig) -> list[Path]:
    """Download all forecast hours specified in config.

    Returns list of successfully downloaded file paths.
    """
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    hours = config.forecast_hours.hours
    logger.info(f"Downloading {len(hours)} forecast hours: f{hours[0]:03d}-f{hours[-1]:03d}")

    downloaded = []
    for hour in hours:
        result = download_forecast_hour(config, hour, output_dir)
        if result:
            downloaded.append(result)

    logger.info(f"Downloaded {len(downloaded)}/{len(hours)} forecast hours")
    return downloaded

"""Orchestration: iterate forecast hours, parse idx, filter variables, download sliced GRIB2."""

import glob
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm

from gfsdown import downloader as _downloader
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

# Files smaller than this are treated as partial/empty and re-downloaded.
MIN_VALID_GRIB_BYTES = 1024


class DownloadError(Exception):
    """Raised when a per-(date,cycle,hour) download exhausts retries."""


def _clean_cfgrib_cache(path: Path) -> None:
    """Remove cfgrib's on-disk .grib2.*.idx cache for a given file."""
    for f in glob.glob(str(path) + ".*.idx"):
        Path(f).unlink(missing_ok=True)


def _validate_existing_grib(path: Path) -> bool:
    """Lightweight metadata-only validation of an existing sliced GRIB2 file.

    Opens the file via cfgrib (which scans the GRIB message headers but does
    not load array data) and confirms at least one data variable was decoded.
    Returns False on any read error so the caller can re-download.
    """
    try:
        import cfgrib  # local import: keeps slicer import-time light
    except ImportError as e:
        logger.warning(f"cfgrib unavailable, skipping validation of {path}: {e}")
        return True  # fall back to size-only check upstream

    _clean_cfgrib_cache(path)
    try:
        # indexpath="" prevents cfgrib from writing a .grib2.*.idx cache during validation.
        # open_datasets() (plural) returns one xr.Dataset per hypercube, so it tolerates
        # mixed-level slices (surface + heightAboveGround + isobaricInhPa, ...).
        datasets = cfgrib.open_datasets(
            str(path),
            backend_kwargs={"indexpath": ""},
        )
    except Exception as e:
        logger.warning(f"GRIB metadata read failed for {path}: {e}")
        return False

    try:
        total_vars = sum(len(ds.data_vars) for ds in datasets)
        return total_vars > 0
    finally:
        for ds in datasets:
            try:
                ds.close()
            except Exception:
                pass


def date_output_dir(base_dir: Path, date: str, cycle: int) -> Path:
    """Per-date / per-cycle output directory: <base>/<YYYYMMDD>/<CC>z/."""
    return base_dir / date.replace("-", "") / f"{cycle:02d}z"


def download_forecast_hour(
    config: GFSConfig,
    date: str,
    cycle: int,
    forecast_hour: int,
    output_dir: Path,
    progress_label: str = "",
) -> Path | None:
    """Download sliced GRIB2 for one (date, cycle, forecast_hour).

    Returns the output file path on success or when the file already exists
    (resume), or None when no requested variable is present in the idx.
    Raises DownloadError if any underlying HTTP step fails after retries.

    ``progress_label`` is an optional ``"[N/M] "`` prefix injected into the
    main log lines so background runs (nohup / piped to file) can tail the
    log and see overall progress without needing a tqdm TTY.
    """
    output_path = output_dir / f"gfs_f{forecast_hour:03d}.grib2"
    pfx = f"{progress_label} " if progress_label else ""

    # Resume: skip files already on disk that pass a lightweight metadata check.
    if output_path.exists() and output_path.stat().st_size >= MIN_VALID_GRIB_BYTES:
        if _validate_existing_grib(output_path):
            logger.info(
                f"{pfx}Skipping {date} {cycle:02d}Z f{forecast_hour:03d}: already downloaded "
                f"({output_path.stat().st_size:,} bytes at {output_path})"
            )
            return output_path
        logger.warning(
            f"{pfx}Existing file {output_path} failed metadata validation; will re-download"
        )
        try:
            output_path.unlink()
            _clean_cfgrib_cache(output_path)
        except OSError as e:
            logger.warning(f"Could not remove invalid file {output_path}: {e}")

    idx_url = build_idx_url(date, cycle, forecast_hour)
    grib_url = build_grib_url(date, cycle, forecast_hour)

    logger.info(f"{pfx}Fetching index for {date} {cycle:02d}Z f{forecast_hour:03d}: {Path(idx_url).name}")

    try:
        idx_text = download_text(idx_url)
    except Exception as e:
        raise DownloadError(f"idx fetch failed: {e}") from e

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

    try:
        download_sliced_grib(all_ranges, grib_url, output_path)
    except Exception as e:
        # Remove partial file so the next run can resume cleanly.
        if output_path.exists():
            try:
                output_path.unlink()
                logger.info(f"  Removed partial file {output_path}")
            except OSError as cleanup_err:
                logger.warning(f"  Could not remove partial file {output_path}: {cleanup_err}")
        raise DownloadError(f"byte-range fetch failed: {e}") from e

    file_size = output_path.stat().st_size
    rel = (
        output_path.relative_to(output_path.parents[2])
        if len(output_path.parents) >= 3
        else output_path.name
    )
    logger.info(f"{pfx}Saved {rel} ({file_size:,} bytes)")

    return output_path


def download_all(
    config: GFSConfig,
) -> tuple[list[tuple[str, int, Path]], list[tuple[str, int, int, str]]]:
    """Download all (date, cycle, forecast_hour) combinations specified in config.

    Files are organised under <output_dir>/<YYYYMMDD>/<CC>z/gfs_fXXX.grib2,
    so different cycles for the same date never collide. Existing files are
    skipped (resume), letting interrupted runs continue without re-downloading.
    Per-init S3 LIST failures and per-hour download failures are logged and
    collected, but never abort the whole batch.

    Returns:
        (succeeded, failed) where
            succeeded = list of (date, cycle, path) for files now on disk;
            failed = list of (date, cycle, forecast_hour, reason) for hours that
                     exhausted retries (S3 LIST failures are recorded with
                     forecast_hour=-1).
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

    succeeded: list[tuple[str, int, Path]] = []
    failed: list[tuple[str, int, int, str]] = []

    # Outer "files" progress bar: ticks once per (date, cycle, hour) processed
    # (success, skip, or fail all count). For all-hours mode the total is grown
    # lazily as each init's hour list is discovered.
    if config.all_hours:
        outer_total: int | None = None  # discovered per-init
    else:
        outer_total = len(dates) * len(cycles) * len(config.forecast_hours.hours)

    progress_on = _downloader.PROGRESS_ENABLED and sys.stderr.isatty()
    file_bar = tqdm(
        total=outer_total,
        desc="Files",
        unit="file",
        position=0,
        dynamic_ncols=True,
        disable=not progress_on,
    )

    # Counter for "[N/M]" log prefix so background runs (nohup, redirected
    # to file) can tail the log and see overall progress without a TTY bar.
    # In all-hours mode total starts at 0 and grows as each init is discovered.
    done = 0
    total = outer_total or 0

    # Route logging through tqdm so log lines don't interleave with the bars.
    with logging_redirect_tqdm():
        for date in dates:
            for cycle in cycles:
                out_dir = date_output_dir(base_dir, date, cycle)
                out_dir.mkdir(parents=True, exist_ok=True)

                if config.all_hours:
                    try:
                        hours = list_available_forecast_hours(date, cycle)
                    except Exception as e:
                        logger.error(f"Failed S3 LIST for {date} {cycle:02d}Z, skipping: {e}")
                        failed.append((date, cycle, -1, f"S3 LIST failed: {e}"))
                        continue
                    if not hours:
                        logger.warning(f"No forecast files found on S3 for {date} {cycle:02d}Z, skipping")
                        continue
                    # Grow the outer bar's total as each init's hour list is discovered.
                    file_bar.total = (file_bar.total or 0) + len(hours)
                    file_bar.refresh()
                    total += len(hours)
                    logger.info(
                        f"=== {date} {cycle:02d}Z -> {out_dir}  "
                        f"(all-hours: {len(hours)} files, f{hours[0]:03d}-f{hours[-1]:03d}) ==="
                    )
                else:
                    hours = config.forecast_hours.hours
                    logger.info(f"=== {date} {cycle:02d}Z -> {out_dir} ===")

                for hour in hours:
                    done += 1
                    label = f"[{done}/{total}]"
                    try:
                        result = download_forecast_hour(
                            config, date, cycle, hour, out_dir, progress_label=label
                        )
                    except DownloadError as e:
                        logger.error(f"{label} Failed {date} {cycle:02d}Z f{hour:03d}: {e}")
                        failed.append((date, cycle, hour, str(e)))
                        file_bar.update(1)
                        continue
                    if result:
                        succeeded.append((date, cycle, result))
                    file_bar.update(1)

    file_bar.close()

    if failed:
        logger.error(
            f"Download summary: {len(succeeded)} succeeded, {len(failed)} failed"
        )
        for date, cycle, hour, reason in failed:
            label = "S3-LIST" if hour < 0 else f"f{hour:03d}"
            logger.error(f"  FAILED  {date} {cycle:02d}Z {label}: {reason}")
    else:
        logger.info(f"Download summary: {len(succeeded)} succeeded, 0 failed")

    write_run_report(base_dir, succeeded, failed)

    return succeeded, failed


def write_run_report(
    output_dir: Path,
    succeeded: list[tuple[str, int, Path]],
    failed: list[tuple[str, int, int, str]],
) -> Path:
    """Write a JSON report of the latest run to <output_dir>/logs/last_run.json.

    The file is overwritten on every run so callers can drive a retry workflow
    by inspecting `failed`. Returns the report path.
    """
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    report_path = log_dir / "last_run.json"

    payload = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "succeeded_count": len(succeeded),
        "failed_count": len(failed),
        "failed": [
            {
                "date": date,
                "cycle": cycle,
                "forecast_hour": hour,
                "reason": reason,
            }
            for date, cycle, hour, reason in failed
        ],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    logger.info(f"Run report written to {report_path}")
    return report_path

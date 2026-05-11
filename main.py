#!/usr/bin/env python3
"""GFS GRIB2 downloader CLI entry point."""

import argparse
import logging
import sys
import warnings
from datetime import datetime
from pathlib import Path

# cfgrib >= 0.9.15 still calls xr.merge with the legacy default `compat`,
# emitting one FutureWarning per open_datasets() call. Filter it before any
# cfgrib import so resume validation doesn't spam the log.
warnings.filterwarnings(
    "ignore",
    message="In a future version of xarray the default value for compat",
    category=FutureWarning,
)

from gfsdown.config import VALID_CYCLES, DateRange, ForecastRange, load_config
from gfsdown.downloader import build_idx_url, download_text, list_available_forecast_hours
from gfsdown.index_parser import classify_variables, list_all_variables, parse_idx
from gfsdown.plotter import compute_wind_speed, plot_wind_speed
from gfsdown.slicer import download_all


def parse_date_range_arg(value: str) -> DateRange:
    """Parse --date-range argument: 'START:END' or 'START:END:STEP'."""
    parts = value.split(":")
    if len(parts) == 2:
        start, end = parts
        step = 1
    elif len(parts) == 3:
        start, end, step = parts[0], parts[1], int(parts[2])
    else:
        raise argparse.ArgumentTypeError(
            f"--date-range must be START:END or START:END:STEP (got {value!r})"
        )
    return DateRange(start=start, end=end, step_days=int(step))


def parse_fhours_arg(value: str) -> ForecastRange:
    """Parse --fhours argument: 'N' (single hour) or 'START:END' or 'START:END:STEP'."""
    parts = value.split(":")
    try:
        if len(parts) == 1:
            n = int(parts[0])
            return ForecastRange(start=n, end=n, step=1)
        elif len(parts) == 2:
            return ForecastRange(start=int(parts[0]), end=int(parts[1]), step=1)
        elif len(parts) == 3:
            return ForecastRange(start=int(parts[0]), end=int(parts[1]), step=int(parts[2]))
    except ValueError:
        pass
    raise argparse.ArgumentTypeError(
        f"--fhours must be N, START:END, or START:END:STEP (got {value!r})"
    )


def parse_cycles_arg(value: str) -> list[int]:
    """Parse --cycles argument: comma-separated cycle hours, e.g. '0,6,12,18'."""
    try:
        items = [int(x.strip()) for x in value.split(",") if x.strip()]
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--cycles must be comma-separated integers (got {value!r})"
        )
    if not items:
        raise argparse.ArgumentTypeError("--cycles must contain at least one value")
    for c in items:
        if c not in VALID_CYCLES:
            raise argparse.ArgumentTypeError(
                f"--cycles values must be in {VALID_CYCLES}, got {c}"
            )
    return sorted(set(items))


def apply_cli_overrides(config, args):
    """Apply CLI flag overrides on top of the loaded config (in-place)."""
    if args.cycles is not None:
        config.cycles = args.cycles
        config.cycle = config.cycles[0]

    if args.date_range is not None:
        config.date_range = args.date_range
        config.dates = args.date_range.dates
        config.date = config.dates[0]
    elif args.date is not None:
        config.date_range = None
        config.dates = [args.date]
        config.date = args.date

    # forecast hours: --fhours and --all-hours are mutually exclusive (validated in main()).
    if args.all_hours:
        config.all_hours = True
        config.forecast_hours = None
    elif args.fhours is not None:
        config.all_hours = False
        config.forecast_hours = args.fhours

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def setup_file_logging(output_dir: Path) -> Path:
    """Mirror the console log stream to <output_dir>/logs/gfsdown_<timestamp>.log.

    Adds a FileHandler to the root logger so every module's INFO+ messages are
    captured. Returns the path of the new log file. Safe to call multiple times
    in one process — duplicate handlers are not added.
    """
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"gfsdown_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, logging.FileHandler) and Path(h.baseFilename) == log_path:
            return log_path

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(fh)
    # Route warnings.warn(...) through logging so they land in the log file.
    logging.captureWarnings(True)
    logger.info(f"Logging to {log_path}")
    return log_path


def cmd_download(args, config):
    """Download GRIB2 data based on config."""
    downloaded, failed = download_all(config)

    if not downloaded:
        if failed:
            logger.error(
                f"No data downloaded. {len(failed)} file(s) failed after retries; "
                f"re-run the same command to resume."
            )
        else:
            logger.error("No data downloaded. Check config variables and levels.")
        sys.exit(1)

    if failed:
        logger.warning(
            f"Download complete with errors: {len(downloaded)} succeeded, "
            f"{len(failed)} failed. Re-run the same command to retry the failed hours."
        )
    else:
        logger.info(f"Download complete: {len(downloaded)} file(s)")

    # Plot if enabled
    if config.plot.enabled and config.plot.plot_type == "wind_speed":
        plot_results(config, downloaded)


def plot_results(config, downloaded_files):
    """Generate wind speed plots from downloaded files.

    `downloaded_files` is a list of (date, cycle, path) tuples produced by
    `slicer.download_all`.
    """
    for date, cycle, fpath in downloaded_files:
        try:
            wind = compute_wind_speed(fpath, fpath)
            stem = fpath.stem  # gfs_f006
            hour = int(stem.rsplit("f", 1)[1])

            # Plots live alongside the GRIB files in <date>/<cycle>z/plots/
            out_dir = fpath.parent / "plots"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"wind_f{hour:03d}"

            plot_wind_speed(
                wind,
                date,
                cycle,
                hour,
                out_path,
                region=config.region,
                colormap=config.plot.colormap,
                dpi=config.plot.dpi,
                output_format=config.plot.output_format,
            )
        except Exception as e:
            logger.error(f"Failed to plot {fpath.name}: {e}")


def cmd_list_vars(args, config):
    """List all available variables for a given forecast hour."""
    if args.hour is not None:
        hour = args.hour
    elif config.forecast_hours is not None:
        hour = config.forecast_hours.start
    else:
        hour = 0  # all-hours mode has no explicit start; probe f000 by default

    idx_url = build_idx_url(config.date, config.cycle, hour)
    logger.info(f"Fetching index for {config.date} {config.cycle:02d}Z f{hour:03d}")

    idx_text = download_text(idx_url)
    entries = parse_idx(idx_text)
    classes = classify_variables(entries)

    # Split into multi-level and single-level
    multi = {k: v for k, v in classes.items() if len(v) > 1}
    single = {k: v for k, v in classes.items() if len(v) == 1}

    # Filter by variable name if requested
    if args.var:
        target = args.var.upper()
        if target in classes:
            levels = classes[target]
            print(f"\n{target} [{len(levels)} level{'s' if len(levels) > 1 else ''}]:")
            for lv in levels:
                print(f'    - {{name: "{target}", level: "{lv}"}}')
            print()
        else:
            print(f"\nVariable '{target}' not found in f{hour:03d}")
            print(f"Available: {', '.join(sorted(classes))}")
        return

    print(f"\nAvailable variables in f{hour:03d} "
          f"({len(classes)} total, {len(multi)} multi-level, {len(single)} single-level)")
    print("=" * 65)

    if multi:
        print("\n── Multi-Level Variables (can pick one or more layers) ──\n")
        for var, levels in multi.items():
            level_str = ", ".join(levels)
            if len(level_str) > 120:
                level_str = level_str[:117] + "..."
            print(f"  {var:<8} [{len(levels)} levels]")
            print(f"          {level_str}")
            print()

    if single:
        print("── Single-Level Variables (only one layer) ──\n")
        for var, levels in single.items():
            lv = levels[0]
            print(f"  {var:<8} {lv}")

    print(f"\nTip: use --var <NAME> to see full level list for a variable\n")


def _group_by_step(hours):
    """Split a sorted hour list into groups of constant step.

    Returns list of (start, end, step, count). For a single-element group, step is 0.
    """
    if not hours:
        return []
    groups = []
    g_start = hours[0]
    g_step = None
    prev = hours[0]
    for h in hours[1:]:
        diff = h - prev
        if g_step is None:
            g_step = diff
        elif diff != g_step:
            count = (prev - g_start) // g_step + 1
            groups.append((g_start, prev, g_step, count))
            g_start = h
            g_step = None
        prev = h
    if g_step is None:
        groups.append((g_start, prev, 0, 1))
    else:
        count = (prev - g_start) // g_step + 1
        groups.append((g_start, prev, g_step, count))
    return groups


def cmd_list_hours(args, config):
    """List every forecast hour file actually available on S3 for date+cycle."""
    logger.info(f"Querying S3 listing for {config.date} {config.cycle:02d}Z ...")
    hours = list_available_forecast_hours(config.date, config.cycle)

    if not hours:
        print(f"\nNo forecast files found for {config.date} {config.cycle:02d}Z.")
        print("Check date/cycle (NOAA retains GFS data ~10 days for current archive).")
        return

    groups = _group_by_step(hours)
    max_lead_h = hours[-1]
    days, hh = divmod(max_lead_h, 24)

    print(f"\nAvailable forecast hours for {config.date} {config.cycle:02d}Z "
          f"({len(hours)} total)")
    print("=" * 65)
    for start, end, step, count in groups:
        if step == 0:
            print(f"  f{start:03d}                     (1 file)")
        else:
            print(f"  f{start:03d} - f{end:03d}  step {step}h     ({count} files)")
    print(f"\nMax lead time: f{max_lead_h:03d}  ({days} day{'s' if days != 1 else ''} "
          f"{hh}h ahead)")
    print(f"\nTip: set 'forecast_hours: {{start: 0, end: {max_lead_h}, step: <N>}}' "
          f"in config.yaml,\n     or override with --fhours later.\n")



def main():
    parser = argparse.ArgumentParser(
        description="Download and visualize GFS GRIB2 data from NOAA S3",
    )
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--list-vars", "-l",
        action="store_true",
        help="List available variables for a forecast hour",
    )
    parser.add_argument(
        "--var",
        type=str,
        default=None,
        help="Show all levels for a specific variable (e.g. --var UGRD)",
    )
    parser.add_argument(
        "--hour", "-H",
        type=int,
        default=None,
        help="Forecast hour for --list-vars (default: first hour from config)",
    )
    parser.add_argument(
        "--plot",
        action="store_true",
        help="Generate wind-speed plots after download (off by default).",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Force-disable plotting even if config.yaml has plot.enabled: true. "
             "Conflicts with --plot.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run demo with the project's config.yaml and force plotting on.",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable tqdm progress bars (auto-disabled when stderr is not a TTY).",
    )
    # ── Lightweight overrides for config.yaml ─────────────────────────
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Override single date (YYYY-MM-DD). Conflicts with --date-range.",
    )
    parser.add_argument(
        "--date-range",
        type=parse_date_range_arg,
        default=None,
        metavar="START:END[:STEP]",
        help="Override date range, e.g. 2026-01-01:2026-02-01 or "
             "2026-01-01:2026-02-01:7 (step in days, default 1).",
    )
    parser.add_argument(
        "--cycles",
        type=parse_cycles_arg,
        default=None,
        metavar="C[,C,...]",
        help="Override forecast cycle(s), one or more from {0,6,12,18} comma-separated. "
             "Examples: --cycles 12  or  --cycles 0,6,12,18.",
    )
    parser.add_argument(
        "--fhours",
        type=parse_fhours_arg,
        default=None,
        metavar="N | START:END | START:END:STEP",
        help="Override forecast hours: single hour (e.g. 1), range (0:24), or "
             "range with step (0:120:3). Conflicts with --all-hours.",
    )
    parser.add_argument(
        "--all-hours",
        action="store_true",
        help="Download every available forecast hour on S3 for each (date, cycle); "
             "overrides forecast_hours from config.yaml. Conflicts with --fhours.",
    )
    # ── Discovery commands ────────────────────────────────────────────
    parser.add_argument(
        "--list-hours",
        action="store_true",
        help="List every available forecast hour on S3 for date+cycle "
             "(uses first cycle when multiple are configured).",
    )

    args = parser.parse_args()

    if args.date is not None and args.date_range is not None:
        parser.error("--date and --date-range are mutually exclusive")
    if args.fhours is not None and args.all_hours:
        parser.error("--fhours and --all-hours are mutually exclusive")
    if args.plot and args.no_plot:
        parser.error("--plot and --no-plot are mutually exclusive")

    config_path = Path(__file__).parent / "config.yaml" if args.demo else args.config
    config = load_config(config_path)
    apply_cli_overrides(config, args)

    # Plot resolution priority: --no-plot > --plot / --demo > config.yaml
    if args.no_plot:
        config.plot.enabled = False
    elif args.plot or args.demo:
        config.plot.enabled = True

    # Progress-bar toggle: explicit --no-progress wins; otherwise tqdm itself
    # also auto-disables when stderr is not a TTY.
    if args.no_progress:
        from gfsdown import downloader as _dl
        _dl.PROGRESS_ENABLED = False

    if args.list_hours:
        cmd_list_hours(args, config)
        return

    if args.list_vars or args.var:
        cmd_list_vars(args, config)
        return

    setup_file_logging(Path(config.output_dir))
    cmd_download(args, config)


if __name__ == "__main__":
    main()

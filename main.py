#!/usr/bin/env python3
"""GFS GRIB2 downloader CLI entry point."""

import argparse
import logging
import sys
from pathlib import Path

from gfsdown.config import load_config
from gfsdown.downloader import build_idx_url, download_text
from gfsdown.index_parser import classify_variables, list_all_variables, parse_idx
from gfsdown.plotter import compute_wind_speed, plot_wind_speed
from gfsdown.slicer import download_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def cmd_download(args, config):
    """Download GRIB2 data based on config."""
    downloaded = download_all(config)

    if not downloaded:
        logger.error("No data downloaded. Check config variables and levels.")
        sys.exit(1)

    logger.info(f"Download complete: {len(downloaded)} file(s)")

    # Plot if enabled
    if config.plot.enabled and config.plot.plot_type == "wind_speed":
        plot_results(config, downloaded)


def plot_results(config, downloaded_files):
    """Generate wind speed plots from downloaded files."""
    for fpath in downloaded_files:
        try:
            wind = compute_wind_speed(fpath, fpath)
            stem = fpath.stem  # gfs_f006
            hour = int(stem.rsplit("f", 1)[1])

            out_dir = config.output_dir / "plots"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"wind_f{hour:03d}"

            plot_wind_speed(
                wind,
                config.date,
                config.cycle,
                hour,
                out_path,
                region=config.region,
                colormap=config.plot.colormap,
                dpi=config.plot.dpi,
                output_format=config.plot.output_format,
            )
        except Exception as e:
            logger.error(f"Failed to plot {fpath.name}: {e}")


def cmd_list_vars(args):
    """List all available variables for a given forecast hour."""
    config_path = Path(args.config) if args.config else Path(__file__).parent / "config.yaml"
    if not config_path.exists():
        logger.error(f"Config not found: {config_path}")
        sys.exit(1)

    config = load_config(config_path)
    hour = args.hour if args.hour is not None else config.forecast_hours.start

    idx_url = build_idx_url(config.date, config.cycle, hour)
    logger.info(f"Fetching index: f{hour:03d}")

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
        "--no-plot",
        action="store_true",
        help="Skip plotting after download",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run demo: download wind speed for config date and plot",
    )

    args = parser.parse_args()

    if args.list_vars or args.var:
        cmd_list_vars(args)
        return

    if args.demo:
        config = load_config(Path(__file__).parent / "config.yaml")
        if args.no_plot:
            config.plot.enabled = False
        cmd_download(args, config)
        return

    config = load_config(args.config)
    if args.no_plot:
        config.plot.enabled = False

    cmd_download(args, config)


if __name__ == "__main__":
    main()

"""Generate climate_restore-compatible manifest files for downloaded GFS data.

This module creates manifest.json files that can be consumed by climate_restore's
batch_restore tool for post-processing GRIB2 data into NetCDF/Zarr formats.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def compute_sha256(filepath: Path, chunk_size: int = 8192) -> str:
    """Compute SHA-256 hash of a file.

    Args:
        filepath: Path to the file
        chunk_size: Read chunk size (default 8KB)

    Returns:
        Hex digest string
    """
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def extract_step_hours(filename: str) -> int | None:
    """Extract forecast step hours from filename.

    Matches patterns like:
    - gfs_f026.grib2 -> 26
    - f006.subset.grib2 -> 6

    Args:
        filename: File name (not full path)

    Returns:
        Step hours as integer, or None if not found
    """
    match = re.search(r'[fg](\d{3})', filename)
    if match:
        return int(match.group(1))
    return None


def scan_grib_files(data_dir: Path) -> list[dict]:
    """Scan directory for grib2 files and extract metadata.

    Args:
        data_dir: Directory containing grib2 files

    Returns:
        List of file info dictionaries
    """
    files = []
    for grib_file in sorted(data_dir.glob("*.grib2")):
        step_hours = extract_step_hours(grib_file.name)
        if step_hours is None:
            logger.warning(f"Could not extract step hours from {grib_file.name}, skipping")
            continue

        file_info = {
            "step_hours": step_hours,
            "path": str(grib_file),
            "size_bytes": grib_file.stat().st_size,
        }
        files.append(file_info)

    return files


def generate_manifest(
    output_dir: Path,
    date_str: str,
    cycle: int,
    source_name: str = "gfs-0p25",
    compute_hash: bool = False,
    variables: list[dict] | None = None,
) -> dict:
    """Generate manifest dictionary for a specific (date, cycle).

    Args:
        output_dir: Output directory containing the data (e.g., ./output)
        date_str: Date string in YYYYMMDD format
        cycle: Forecast cycle hour (0, 6, 12, or 18)
        source_name: Source identifier (default: "gfs-0p25")
        compute_hash: Whether to compute SHA-256 hashes (slow)
        variables: List of variable definitions (if None, uses default)

    Returns:
        Manifest dictionary
    """
    # gfsdown output structure: <output_dir>/<YYYYMMDD>/<CC>z/
    # (no source_name subdirectory)
    cycle_dir = output_dir / date_str / f"{cycle:02d}z"

    if not cycle_dir.exists():
        raise FileNotFoundError(f"Directory not found: {cycle_dir}")

    # Scan grib files
    grib_files = scan_grib_files(cycle_dir)
    if not grib_files:
        raise ValueError(f"No grib2 files found in: {cycle_dir}")

    # Compute SHA-256 if enabled
    if compute_hash:
        logger.info(f"Computing SHA-256 for {len(grib_files)} files...")
        for file_info in grib_files:
            filepath = Path(file_info["path"])
            file_info["sha256"] = compute_sha256(filepath)

    # Parse date and init time
    date_obj = datetime.strptime(date_str, "%Y%m%d")
    init_time = datetime(date_obj.year, date_obj.month, date_obj.day, cycle, 0, 0)

    # Default variables (can be overridden)
    if variables is None:
        variables = [
            {"name": "wind_100m", "levtype": "hag", "params": ["UGRD", "VGRD"], "levels": ["100"]},
            {"name": "wind_80m", "levtype": "hag", "params": ["UGRD", "VGRD"], "levels": ["80"]},
            {"name": "wind_10m", "levtype": "hag", "params": ["UGRD", "VGRD"], "levels": ["10"]},
            {"name": "pl_850", "levtype": "pl", "params": ["UGRD", "VGRD", "TMP", "RH", "HGT"], "levels": ["850"]},
            {"name": "temp_hag", "levtype": "hag", "params": ["TMP"], "levels": ["2", "80", "100"]},
            {"name": "temp_surface", "levtype": "sfc", "params": ["TMP"], "levels": None},
            {"name": "humidity_hag", "levtype": "hag", "params": ["RH", "SPFH", "DPT"], "levels": ["2", "80"]},
            {"name": "pressure_sfc", "levtype": "sfc", "params": ["PRES"], "levels": None},
            {"name": "pressure_hag", "levtype": "hag", "params": ["PRES"], "levels": ["80"]},
            {"name": "pressure_msl", "levtype": "atm", "params": ["PRMSL"], "levels": None},
            {"name": "radiation_surface", "levtype": "sfc", "params": ["DSWRF", "USWRF", "DLWRF", "ULWRF"], "levels": None},
            {"name": "cloud_column", "levtype": "atm", "params": ["TCDC"], "levels": None},
            {"name": "cloud_layers", "levtype": "other", "params": ["LCDC", "MCDC", "HCDC"], "levels": None},
            {"name": "precip_pwat", "levtype": "sfc", "params": ["APCP", "PRATE"], "levels": None},
            {"name": "pwat_column", "levtype": "atm", "params": ["PWAT"], "levels": None},
        ]

    # Build manifest
    manifest = {
        "schema_version": 1,
        "source": {
            "name": source_name,
            "description": "NOAA GFS 0.25° atmos forecast (NCEP), downloaded by gfsdown"
        },
        "init_time": init_time.isoformat() + "+00:00",
        "date": date_str,
        "cycle": cycle,
        "completed_at": datetime.now().isoformat() + "+00:00",
        "variables": variables,
        "download": {
            "output_dir": "output",
            "workers": 8,
            "gap_tolerance": 0,
            "timeout_seconds": 120.0,
            "init_concurrency": 2
        },
        "files": []
    }

    # Add file entries
    for file_info in grib_files:
        # Convert to relative path format expected by climate_restore
        # Format: output/gfs-0p25/YYYYMMDD/CCz/gfs_fXXX.grib2
        filename = Path(file_info["path"]).name
        rel_path = f"output/{source_name}/{date_str}/{cycle:02d}z/{filename}"

        file_entry = {
            "step_hours": file_info["step_hours"],
            "path": rel_path,
            "size_bytes": file_info["size_bytes"],
            "sha256": file_info.get("sha256", ""),
            "records_selected": 0,  # gfsdown doesn't track this
            "records_total": 0,
            "http_requests": 0,
            "savings_pct": 0.0,
            "selected_breakdown": {}
        }
        manifest["files"].append(file_entry)

    return manifest


def write_manifest(manifest: dict, output_path: Path) -> None:
    """Write manifest dictionary to JSON file.

    Args:
        manifest: Manifest dictionary
        output_path: Output file path
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    logger.info(f"Manifest written to {output_path}")


def discover_dates(data_root: Path, source_name: str = "gfs-0p25") -> list[tuple[str, int]]:
    """Discover all available (date, cycle) combinations in data directory.

    Args:
        data_root: Root directory containing the data
        source_name: Source identifier

    Returns:
        List of (date_str, cycle) tuples, sorted
    """
    dates = []
    source_dir = data_root / source_name

    if not source_dir.exists():
        return dates

    for date_dir in sorted(source_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        date_str = date_dir.name
        if not re.match(r'^\d{8}$', date_str):
            continue

        for cycle_dir in date_dir.iterdir():
            if not cycle_dir.is_dir():
                continue
            match = re.match(r'^(\d{2})z$', cycle_dir.name)
            if match:
                cycle = int(match.group(1))
                dates.append((date_str, cycle))

    return dates

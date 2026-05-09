"""HTTP download engine with range-request support for slicing GRIB2 files from S3."""

import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

S3_BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"

# Retry settings
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds, doubles each retry


def build_grib_url(date: str, cycle: int, forecast_hour: int) -> str:
    date_stripped = date.replace("-", "")
    cycle_str = f"{cycle:02d}"
    hour_str = f"{forecast_hour:03d}"
    return (
        f"{S3_BASE}/gfs.{date_stripped}/{cycle_str}/atmos/"
        f"gfs.t{cycle_str}z.pgrb2.0p25.f{hour_str}"
    )


def build_idx_url(date: str, cycle: int, forecast_hour: int) -> str:
    return build_grib_url(date, cycle, forecast_hour) + ".idx"


def get_file_size(url: str) -> int:
    """Get file size via HEAD request."""
    resp = requests.head(url, timeout=30)
    resp.raise_for_status()
    return int(resp.headers.get("Content-Length", 0))


def download_text(url: str) -> str:
    """Download a text file (e.g. .idx) with retries."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            delay = RETRY_DELAY * (2 ** attempt)
            logger.warning(f"Download failed (attempt {attempt+1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(delay)
            else:
                raise


def download_byte_range(url: str, start: int, end: int) -> bytes:
    """Download a byte range from the file using HTTP Range header."""
    range_header = f"bytes={start}-{end}"
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                url,
                headers={"Range": range_header},
                timeout=120,
            )
            if resp.status_code == 206:
                return resp.content
            elif resp.status_code == 200:
                # Server doesn't support range requests, return all
                logger.warning(f"Server returned full file (200) instead of 206 for {url}")
                return resp.content
            else:
                resp.raise_for_status()
        except requests.RequestException as e:
            delay = RETRY_DELAY * (2 ** attempt)
            logger.warning(f"Range download failed (attempt {attempt+1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(delay)
            else:
                raise
    return b""


def download_sliced_grib(
    entries_with_ranges: list[tuple[int, int]],
    url: str,
    output_path: Path,
) -> Path:
    """Download multiple byte ranges and concatenate into a single .grib2 file.

    Args:
        entries_with_ranges: List of (start, end) byte offsets. Adjacent ranges
            will be automatically merged to reduce HTTP requests.
        url: S3 URL of the GRIB2 file.
        output_path: Where to write the sliced GRIB2 file.

    Returns:
        Path to the output file.
    """
    merged = merge_ranges(entries_with_ranges)
    logger.info(f"Downloading {len(merged)} merged ranges from {Path(url).name}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as f:
        for i, (start, end) in enumerate(merged):
            logger.info(f"  Range {i+1}/{len(merged)}: bytes {start}-{end} ({end - start + 1:,} bytes)")
            data = download_byte_range(url, start, end)
            f.write(data)

    return output_path


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping or adjacent byte ranges to minimize HTTP requests.

    Input: [(100, 200), (201, 300), (500, 600)]
    Output: [(100, 300), (500, 600)]
    """
    if not ranges:
        return []

    sorted_ranges = sorted(ranges, key=lambda x: x[0])
    merged = [list(sorted_ranges[0])]

    for start, end in sorted_ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1][1] = max(last_end, end)
        else:
            merged.append([start, end])

    return [(s, e) for s, e in merged]

"""HTTP download engine with range-request support for slicing GRIB2 files from S3."""

import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

import requests
from tqdm import tqdm

logger = logging.getLogger(__name__)

S3_BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

# Retry settings
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds, doubles each retry

# Streaming chunk size for byte-range downloads (64 KB balances rate granularity vs overhead).
STREAM_CHUNK_BYTES = 64 * 1024

# Progress-bar toggle. main() flips this to False when --no-progress is set
# or stderr is not a TTY (e.g. piped to a log file).
PROGRESS_ENABLED = True


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


def download_byte_range(
    url: str,
    start: int,
    end: int,
    progress_cb: Callable[[int], None] | None = None,
) -> bytes:
    """Download a byte range from the file using HTTP Range header.

    If ``progress_cb`` is provided, it is called with each chunk's byte count
    as the response streams in. On a retried attempt, any bytes counted before
    the failure are rolled back via ``progress_cb(-bytes_in_attempt)`` so the
    caller's bar stays accurate.
    """
    range_header = f"bytes={start}-{end}"
    for attempt in range(MAX_RETRIES):
        bytes_in_attempt = 0
        try:
            with requests.get(
                url,
                headers={"Range": range_header},
                timeout=120,
                stream=True,
            ) as resp:
                if resp.status_code not in (200, 206):
                    resp.raise_for_status()
                if resp.status_code == 200:
                    # Server doesn't support range requests, return all
                    logger.warning(f"Server returned full file (200) instead of 206 for {url}")
                chunks: list[bytes] = []
                for chunk in resp.iter_content(chunk_size=STREAM_CHUNK_BYTES):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    bytes_in_attempt += len(chunk)
                    if progress_cb is not None:
                        progress_cb(len(chunk))
                return b"".join(chunks)
        except requests.RequestException as e:
            if progress_cb is not None and bytes_in_attempt:
                # Roll back partial progress so the bar doesn't double-count on retry.
                progress_cb(-bytes_in_attempt)
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

    Renders a per-file tqdm bar showing bytes-downloaded / total + live transfer
    rate (e.g. ``2.34MB/s``). The bar is suppressed when
    ``PROGRESS_ENABLED`` is False or stderr is not a TTY.

    Args:
        entries_with_ranges: List of (start, end) byte offsets. Adjacent ranges
            will be automatically merged to reduce HTTP requests.
        url: S3 URL of the GRIB2 file.
        output_path: Where to write the sliced GRIB2 file.

    Returns:
        Path to the output file.
    """
    merged = merge_ranges(entries_with_ranges)
    total_bytes = sum(end - start + 1 for start, end in merged)
    logger.info(
        f"Downloading {len(merged)} merged ranges from {Path(url).name} "
        f"({total_bytes:,} bytes)"
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    bar = tqdm(
        total=total_bytes,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc=f"  {output_path.name}",
        leave=False,
        dynamic_ncols=True,
        disable=not (PROGRESS_ENABLED and sys.stderr.isatty()),
        position=1,
    )
    try:
        with open(output_path, "wb") as f:
            for i, (start, end) in enumerate(merged):
                logger.info(
                    f"  Range {i+1}/{len(merged)}: bytes {start}-{end} "
                    f"({end - start + 1:,} bytes)"
                )
                data = download_byte_range(url, start, end, progress_cb=bar.update)
                f.write(data)
    finally:
        bar.close()

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



def list_available_forecast_hours(date: str, cycle: int) -> list[int]:
    """Query NOAA S3 LIST API to find every forecast hour available for a given init.

    Returns a sorted, deduplicated list of forecast hour integers (e.g.
    [0, 1, 2, ..., 120, 123, 126, ..., 384]).
    """
    date_stripped = date.replace("-", "")
    cycle_str = f"{cycle:02d}"
    prefix = f"gfs.{date_stripped}/{cycle_str}/atmos/gfs.t{cycle_str}z.pgrb2.0p25.f"
    list_url = f"{S3_BASE}/"

    pattern = re.compile(r"\.pgrb2\.0p25\.f(\d{3,})(?:\.idx)?$")
    hours: set[int] = set()
    continuation: str | None = None

    while True:
        params = {
            "list-type": "2",
            "prefix": prefix,
            "max-keys": "1000",
        }
        if continuation:
            params["continuation-token"] = continuation

        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.get(list_url, params=params, timeout=60)
                resp.raise_for_status()
                break
            except requests.RequestException as e:
                delay = RETRY_DELAY * (2 ** attempt)
                logger.warning(f"S3 LIST failed (attempt {attempt+1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(delay)
                else:
                    raise

        root = ET.fromstring(resp.text)
        for key_el in root.findall("s3:Contents/s3:Key", S3_NS):
            m = pattern.search(key_el.text or "")
            if m:
                hours.add(int(m.group(1)))

        truncated = root.findtext("s3:IsTruncated", default="false", namespaces=S3_NS)
        if truncated.lower() != "true":
            break
        continuation = root.findtext("s3:NextContinuationToken", namespaces=S3_NS)
        if not continuation:
            break

    return sorted(hours)

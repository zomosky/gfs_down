"""Parse NCEP-style GRIB2 index (.idx) files to extract variable metadata and byte offsets."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IdxEntry:
    record_number: int
    byte_offset: int
    date_str: str
    variable: str
    level: str
    forecast_label: str
    byte_end: int = 0  # computed from next entry's offset

    @property
    def byte_size(self) -> int:
        return self.byte_end - self.byte_offset

    def matches(self, var_name: str, var_level: str) -> bool:
        return self.variable == var_name and self.level == var_level


def parse_idx(text: str) -> list[IdxEntry]:
    """Parse .idx file text into a list of IdxEntry objects.

    NCEP .idx format (colon-separated):
        record_num:byte_offset:d=YYYYMMDDHH:VAR:level:forecast_label:
    """
    entries = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 7:
            continue

        entry = IdxEntry(
            record_number=int(parts[0]),
            byte_offset=int(parts[1]),
            date_str=parts[2],
            variable=parts[3],
            level=parts[4],
            forecast_label=parts[5],
        )
        entries.append(entry)

    # Compute byte_end from next entry's offset
    for i in range(len(entries) - 1):
        entries[i].byte_end = entries[i + 1].byte_offset

    return entries


def filter_entries(
    entries: list[IdxEntry],
    var_name: str,
    var_level: str,
) -> list[IdxEntry]:
    """Return entries matching the given variable name and level."""
    return [e for e in entries if e.matches(var_name, var_level)]


def get_forecast_label_for_hour(hour: int) -> str:
    """Derive the expected forecast label string from the forecast hour.

    f000 -> 'anl', f001 -> '1 hour fcst', f006 -> '6 hour fcst', etc.
    """
    if hour == 0:
        return "anl"
    return f"{hour} hour fcst"


def list_all_variables(entries: list[IdxEntry]) -> list[tuple[str, str]]:
    """Return unique (variable, level) pairs from parsed idx entries."""
    seen = set()
    result = []
    for e in entries:
        key = (e.variable, e.level)
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def classify_variables(entries: list[IdxEntry]) -> dict[str, list[str]]:
    """Group entries by variable name, returning {var_name: [level, ...]} sorted.

    Multi-level variables (e.g., TMP with 57 pressure levels) will have
    multiple entries in their list. Single-level variables have one entry.
    """
    from collections import defaultdict

    groups: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        groups[e.variable].add(e.level)

    # Sort levels within each variable, sort variables alphabetically
    result = {}
    for var in sorted(groups):
        result[var] = sorted(groups[var])
    return result

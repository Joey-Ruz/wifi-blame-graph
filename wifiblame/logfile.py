"""The CSV log: one line per scheduled run."""

import csv
import os
from pathlib import Path

# Status values written to the log.
STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_NOT_HOME = "not_home"
STATUS_NO_NETWORK = "no_network"
STATUS_ASLEEP = "asleep"
STATUS_DATA_LIMIT = "data_limit"
STATUS_RATE_LIMITED = "rate_limited"

FIELDS = [
    "timestamp",
    "status",
    "error",
    "download_mbps",
    "upload_mbps",
    "ping_ms",
    "jitter_ms",
    "packet_loss_pct",
    "download_loaded_ms",
    "upload_loaded_ms",
    "router_ping_ms",
    "signal_dbm",
    "noise_dbm",
    "band",
    "channel",
    "server_name",
    "server_location",
    "isp",
    "public_ip",
    "result_url",
    "background_mbps",
    "vpn_on",
    "on_power",
    "low_power_mode",
    "data_used_mb",
    "tool",
]

NUMBER_FIELDS = [
    "download_mbps",
    "upload_mbps",
    "ping_ms",
    "jitter_ms",
    "packet_loss_pct",
    "download_loaded_ms",
    "upload_loaded_ms",
    "router_ping_ms",
    "signal_dbm",
    "noise_dbm",
    "background_mbps",
    "data_used_mb",
]

YES_NO_FIELDS = ["vpn_on", "on_power", "low_power_mode"]


def _format_value(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def upgrade_columns(log_file):
    """When new columns were added since the log was started, rewrite the log
    with the current columns. Every existing value is kept; new columns are
    blank for older lines."""
    with open(log_file, newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames == FIELDS:
            return False
        old_lines = list(reader)

    temporary = log_file.with_suffix(".upgrading")
    with open(temporary, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        for line in old_lines:
            writer.writerow(line)
    os.replace(temporary, log_file)
    return True


def append_row(row, log_file):
    """Add one run to the log, writing the header first if the file is new."""
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    unknown = set(row) - set(FIELDS)
    if unknown:
        raise ValueError(f"Unknown log fields: {sorted(unknown)}")

    is_new = not log_file.exists() or log_file.stat().st_size == 0
    if not is_new:
        upgrade_columns(log_file)
    with open(log_file, "a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        line = {}
        for field in FIELDS:
            line[field] = _format_value(row.get(field))
        writer.writerow(line)


def _parse_line(raw):
    row = dict(raw)
    for field in NUMBER_FIELDS:
        text = row.get(field, "")
        row[field] = float(text) if text not in ("", None) else None
    for field in YES_NO_FIELDS:
        text = row.get(field, "")
        if text == "yes":
            row[field] = True
        elif text == "no":
            row[field] = False
        else:
            row[field] = None
    return row


def read_rows(log_file):
    """Read every logged run, with numbers and yes/no converted back."""
    log_file = Path(log_file)
    if not log_file.exists():
        return []
    rows = []
    with open(log_file, newline="") as handle:
        for raw in csv.DictReader(handle):
            rows.append(_parse_line(raw))
    return rows

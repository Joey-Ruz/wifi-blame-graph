"""Unreliable-test and anomaly rules.

Rules:
- A test is unreliable when a VPN was on, Low Power Mode was on, or this Mac
  was already downloading more than background_limit_mbps (config.json) just before it. Battery is logged but does not
  make a test unreliable (user decision).
- A day's typical speed is the median of that day's successful, reliable tests
  (download and upload separately). It needs MIN_TESTS_FOR_TYPICAL of them;
  a day with fewer uses the typical speed of the last 7 days instead.
- An anomaly is a failed test, or a reliable successful test under
  ANOMALY_SHARE_OF_TYPICAL of the typical speed. Unreliable tests are shown
  gray instead of being flagged, since the cause of a low reading is known.
"""

from datetime import datetime, timedelta
from statistics import median

from wifiblame import config, logfile

MEASURES = ("download_mbps", "upload_mbps")
BASELINE_DAYS = 7


def unreliable_reasons(row):
    reasons = []
    if row.get("vpn_on") is True:
        reasons.append("VPN on")
    if row.get("low_power_mode") is True:
        reasons.append("Low Power Mode")
    background = row.get("background_mbps")
    limit = config.current()["background_limit_mbps"]
    if background is not None and background > limit:
        reasons.append(f"background download {background:.1f} Mbps")
    return reasons


def is_unreliable(row):
    return len(unreliable_reasons(row)) > 0


def row_time(row):
    return datetime.fromisoformat(row["timestamp"])


def row_day(row):
    return row_time(row).date()


def typical_speed(rows, measure):
    values = []
    for row in rows:
        if row["status"] != logfile.STATUS_OK:
            continue
        if is_unreliable(row):
            continue
        if row.get(measure) is None:
            continue
        values.append(row[measure])
    if len(values) < config.MIN_TESTS_FOR_TYPICAL:
        return None
    return median(values)


def group_by_day(rows):
    days = {}
    for row in rows:
        days.setdefault(row_day(row), []).append(row)
    return days


def baseline_rows(history, day):
    """Rows from the 7 days ending on day, for days with too few tests."""
    first_day = day - timedelta(days=BASELINE_DAYS - 1)
    selected = []
    for row in history:
        if first_day <= row_day(row) <= day:
            selected.append(row)
    return selected


def find_anomalies(rows, history=None):
    """Returns {row index: [(kind, text), ...]} for rows that are anomalies.

    kind is "failed", "download" or "upload". Each day is judged against its
    own typical speed. A day with fewer than MIN_TESTS_FOR_TYPICAL reliable
    tests (an hourly-or-slower schedule, or a day mostly away) is judged
    against the last 7 days of `history` instead (default: rows).
    """
    if history is None:
        history = rows
    index_by_id = {}
    for index, row in enumerate(rows):
        index_by_id[id(row)] = index

    anomalies = {}
    for day_rows in group_by_day(rows).values():
        day = row_day(day_rows[0])
        typical = {}
        basis = {}
        for measure in MEASURES:
            typical[measure] = typical_speed(day_rows, measure)
            basis[measure] = "typical"
            if typical[measure] is None:
                typical[measure] = typical_speed(baseline_rows(history, day), measure)
                basis[measure] = "7-day typical"

        for row in day_rows:
            reasons = []
            if row["status"] == logfile.STATUS_FAILED:
                reasons.append(("failed", "test failed: " + (row.get("error") or "unknown")))
            elif row["status"] == logfile.STATUS_OK and not is_unreliable(row):
                for measure in MEASURES:
                    value = row.get(measure)
                    day_typical = typical[measure]
                    if value is None or day_typical is None:
                        continue
                    if value < config.ANOMALY_SHARE_OF_TYPICAL * day_typical:
                        label = "download" if measure == "download_mbps" else "upload"
                        text = f"{label} {value:.0f} Mbps vs {basis[measure]} {day_typical:.0f}"
                        reasons.append((label, text))
            if reasons:
                anomalies[index_by_id[id(row)]] = reasons
    return anomalies


def month_data_used_gb(rows, through_day):
    """Data the speed tests used from the 1st of through_day's month through
    the end of through_day. Tests logged before data use was recorded count as 0."""
    month_start = through_day.replace(day=1)
    total_mb = 0.0
    for row in rows:
        day = row_day(row)
        if day < month_start or day > through_day:
            continue
        if row.get("data_used_mb") is not None:
            total_mb += row["data_used_mb"]
    return total_mb / 1000


def day_data_used_gb(rows, day):
    total_mb = 0.0
    for row in rows:
        if row_day(row) == day and row.get("data_used_mb") is not None:
            total_mb += row["data_used_mb"]
    return total_mb / 1000


def loaded_delay_ms(row):
    """The worse of the two busy-connection delays."""
    values = []
    for field in ("download_loaded_ms", "upload_loaded_ms"):
        if row.get(field) is not None:
            values.append(row[field])
    if not values:
        return None
    return max(values)

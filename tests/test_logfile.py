import csv

import pytest

from wifiblame import logfile


def test_new_log_gets_header_then_row(tmp_path):
    log = tmp_path / "data" / "log.csv"
    logfile.append_row({"timestamp": "2026-11-20T07:00:00", "status": "ok",
                        "download_mbps": 400.0, "vpn_on": False, "on_power": True}, log)
    with open(log, newline="") as handle:
        lines = list(csv.reader(handle))
    assert lines[0] == logfile.FIELDS
    assert len(lines) == 2


def test_rows_append_and_read_back(tmp_path):
    log = tmp_path / "log.csv"
    logfile.append_row({"timestamp": "2026-11-20T07:00:00", "status": "ok",
                        "download_mbps": 400.123, "vpn_on": False, "on_power": True}, log)
    logfile.append_row({"timestamp": "2026-11-20T07:10:00", "status": "failed",
                        "error": "timed out"}, log)
    rows = logfile.read_rows(log)
    assert len(rows) == 2
    assert rows[0]["download_mbps"] == 400.12
    assert rows[0]["vpn_on"] is False
    assert rows[0]["on_power"] is True
    assert rows[1]["status"] == "failed"
    assert rows[1]["error"] == "timed out"
    assert rows[1]["download_mbps"] is None
    assert rows[1]["on_power"] is None


def test_unknown_field_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        logfile.append_row({"timestamp": "x", "status": "ok", "nonsense": 1},
                           tmp_path / "log.csv")


def test_missing_log_reads_as_empty(tmp_path):
    assert logfile.read_rows(tmp_path / "none.csv") == []


def test_old_log_upgraded_to_new_columns_without_losing_lines(tmp_path):
    log = tmp_path / "log.csv"
    old_fields = logfile.FIELDS[:-2]
    with open(log, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=old_fields)
        writer.writeheader()
        writer.writerow({"timestamp": "2026-11-20T12:45:03", "status": "ok",
                         "download_mbps": "799.52", "on_power": "no"})
    logfile.append_row({"timestamp": "2026-11-20T13:10:00", "status": "ok",
                        "download_mbps": 600.0, "low_power_mode": True,
                        "data_used_mb": 900.0}, log)
    with open(log, newline="") as handle:
        assert next(csv.reader(handle)) == logfile.FIELDS
    rows = logfile.read_rows(log)
    assert len(rows) == 2
    assert rows[0]["download_mbps"] == 799.52
    assert rows[0]["on_power"] is False
    assert rows[0]["data_used_mb"] is None
    assert rows[1]["low_power_mode"] is True
    assert rows[1]["data_used_mb"] == 900.0

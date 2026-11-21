from datetime import date, datetime

from wifiblame import config, homepage, logfile, outages, report


def ok(day, time, down, up=40.0, **extra):
    row = {"timestamp": f"2026-11-{day}T{time}:00", "status": "ok",
           "download_mbps": down, "upload_mbps": up, "ping_ms": 18.0,
           "router_ping_ms": 4.0, "signal_dbm": -45.0, "vpn_on": False,
           "on_power": True, "background_mbps": 0.1,
           "result_url": f"https://www.speedtest.net/result/c/{day}{time}"}
    row.update(extra)
    return row


def sample_rows():
    rows = []
    for day in ("18", "19", "20"):
        for time in ("09:00", "13:00", "20:00", "21:00"):
            down = 300.0 if time == "20:00" else 700.0
            rows.append(ok(day, time, down))
    rows.append(ok("20", "10:00", 90.0))
    rows.append({"timestamp": "2026-11-20T11:00:00", "status": "failed",
                 "error": "speed test timed out"})
    rows.append({"timestamp": "2026-11-20T12:00:00", "status": "rate_limited",
                 "error": "Too many requests received."})
    return rows


SAMPLE_OUTAGES = [{"start": datetime(2026, 11, 20, 14, 0), "end": datetime(2026, 11, 20, 14, 5),
                   "kind": outages.KIND_PROVIDER, "checks": 5, "minutes": 5},
                  {"start": datetime(2026, 11, 19, 15, 0), "end": datetime(2026, 11, 19, 15, 3),
                   "kind": outages.KIND_WIFI, "checks": 3, "minutes": 3}]


def settings_with(**changes):
    return dict(config.DEFAULT_SETTINGS, **changes)


def test_report_numbers():
    summary = report.summarize(sample_rows(), SAMPLE_OUTAGES, date(2026, 11, 18),
                               date(2026, 11, 20), settings_with(plan_download_mbps=1000))
    assert summary["tests"] == 14
    assert summary["failed"] == 1
    assert summary["typical_download"] == 700.0
    assert summary["under_half_plan"] == 4
    assert summary["worst_hours"][0][1] == 20
    assert summary["outage_minutes"] == {"provider": 5, "Wi-Fi": 3}
    flagged_kinds = sorted(kind for _row, reasons in summary["flagged"] for kind, _ in reasons)
    # Three 8 pm tests at 300 (under half of 700) plus the 90 Mbps test, plus the failure.
    assert flagged_kinds == ["download", "download", "download", "download", "failed"]
    assert [item["day"] for item in summary["days"]] == [date(2026, 11, 18), date(2026, 11, 19),
                                                         date(2026, 11, 20)]


def test_report_range_excludes_other_days():
    summary = report.summarize(sample_rows(), SAMPLE_OUTAGES, date(2026, 11, 20),
                               date(2026, 11, 20), settings_with())
    assert len(summary["outages"]) == 1
    assert summary["under_half_plan"] is None


def test_report_page(tmp_path):
    path = report.write_report(sample_rows(), SAMPLE_OUTAGES, date(2026, 11, 18),
                               date(2026, 11, 20), tmp_path / "r.html",
                               settings_with(plan_download_mbps=1000))
    page = path.read_text()
    assert "11/18/2026 to 11/20/2026" in page
    assert "plan: 1000 Mbps down" in page
    assert "70% of plan" in page
    assert "https://www.speedtest.net/result/c/2010:00" in page
    assert "Provider" in page and "Home Wi-Fi" in page
    assert "Too many requests" not in page


def test_hour_pattern_uses_reliable_successful_tests():
    rows = sample_rows() + [ok("20", "09:30", 10.0, vpn_on=True)]
    pattern = {hour: (down, count) for hour, down, _up, count in homepage.hour_pattern(rows)}
    assert pattern[20] == (300.0, 3)
    assert pattern[9] == (700.0, 3)
    assert 11 not in pattern and 12 not in pattern


def test_home_page_lists_days_and_links(tmp_path):
    graphs_dir = tmp_path / "graphs"
    (graphs_dir / "daily").mkdir(parents=True)
    (graphs_dir / "daily" / "2026-11-20.html").write_text("x")
    page = homepage.write_home(sample_rows(), SAMPLE_OUTAGES, graphs_dir,
                               settings_with(plan_download_mbps=1000)).read_text()
    assert "3 days of results" in page
    assert page.index("Fri 11/20/2026") < page.index("Wed 11/18/2026")
    assert 'href="daily/2026-11-20.html"' in page
    assert "1 (5 min)" in page
    assert "Plan download 1000 Mbps" in page


def test_report_notes_apple_tests(tmp_path):
    rows = sample_rows() + [ok("20", "15:00", 650.0, tool="apple")]
    summary = report.summarize(rows, [], date(2026, 11, 18), date(2026, 11, 20), settings_with())
    assert summary["apple_tests"] == 1
    page = report.write_report(rows, [], date(2026, 11, 18), date(2026, 11, 20),
                               tmp_path / "r.html", settings_with()).read_text()
    assert "1 of the tests used Apple" in page

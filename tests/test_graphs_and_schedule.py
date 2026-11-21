from datetime import date, datetime

from wifiblame import config, graphs, logfile, setup


def write_sample_day(log, day="2026-11-20"):
    times = ["07:00", "07:10", "07:20", "07:30", "07:40", "09:00", "09:10"]
    for index, time in enumerate(times):
        row = {"timestamp": f"{day}T{time}:00", "status": "ok",
               "download_mbps": 400.0, "upload_mbps": 40.0, "ping_ms": 12.0,
               "download_loaded_ms": 40.0, "upload_loaded_ms": 70.0,
               "router_ping_ms": 3.0, "signal_dbm": -50.0, "band": "5 GHz",
               "vpn_on": False, "on_power": True, "background_mbps": 0.1,
               "result_url": f"https://www.speedtest.net/result/c/{index}",
               "data_used_mb": 1000.0}
        if time == "07:30":
            row["download_mbps"] = 90.0
        if time == "07:40":
            row["vpn_on"] = True
        if time == "09:00":
            row["on_power"] = False
        logfile.append_row(row, log)
    logfile.append_row({"timestamp": f"{day}T09:20:00", "status": "failed",
                        "error": "speed test timed out"}, log)
    logfile.append_row({"timestamp": f"{day}T09:30:00", "status": "not_home"}, log)


def test_long_gap_breaks_the_line(tmp_path):
    log = tmp_path / "log.csv"
    write_sample_day(log)
    rows = logfile.read_rows(log)
    xs, ys = graphs.series_with_gaps(rows, graphs.field("download_mbps"))
    assert ys.count(None) == 1
    assert ys.index(None) == 5


def test_graph_data_marks_anomalies_and_unreliable(tmp_path):
    log = tmp_path / "log.csv"
    write_sample_day(log)
    data = graphs.GraphData(logfile.read_rows(log))
    assert data.slow["download"][1] == [90.0]
    assert data.failed_times == [datetime(2026, 11, 20, 9, 20)]
    assert data.unreliable_x == [datetime(2026, 11, 20, 7, 40)]


def test_day_and_week_graphs_written(tmp_path, use_settings):
    use_settings(data_cap_gb=1000)
    log = tmp_path / "log.csv"
    write_sample_day(log)
    written = graphs.make_all(date(2026, 11, 20), log, tmp_path / "graphs")
    names = sorted(path.name for path in written)
    assert names == ["2026-11-20.html", "2026-11-20.png", "index.html",
                     "last-7-days.html", "last-7-days.html", "last-7-days.png"]
    for path in written:
        assert path.stat().st_size > 1000
    assert (tmp_path / "graphs" / graphs.PLOTLY_JS_NAME).exists()

    page = (tmp_path / "graphs" / "daily" / "2026-11-20.html").read_text()
    assert "Anomaly: download 90 Mbps vs typical 400" in page
    assert "VPN on" in page
    assert "on battery" in page
    assert "https://www.speedtest.net/result/c/0" in page
    assert "11/20/2026" in page
    assert "tests used 7.0 GB this day" in page
    assert "7 GB of the 1 TB cap used by tests in November so far (0.7%)" in page


def test_no_log_means_no_graphs(tmp_path):
    assert graphs.make_all(date(2026, 11, 20), tmp_path / "none.csv", tmp_path / "g") == []


def test_test_schedule_covers_7am_to_1150pm():
    times = setup.test_times()
    assert len(times) == 102
    assert times[0] == {"Hour": 7, "Minute": 0}
    assert times[-1] == {"Hour": 23, "Minute": 50}


def test_jobs_point_at_project_python(tmp_path):
    test_job = setup.build_test_job(tmp_path)
    graph_job = setup.build_graph_job(tmp_path)
    assert test_job["ProgramArguments"] == [str(tmp_path / ".venv" / "bin" / "python"),
                                            "-m", "wifiblame.runner"]
    assert graph_job["ProgramArguments"][-1] == "wifiblame.graphs"
    assert graph_job["StartCalendarInterval"] == [{"Hour": 23, "Minute": 59}]
    assert "/opt/homebrew/bin" in test_job["EnvironmentVariables"]["PATH"]


def test_jobs_are_not_throttled_as_background_work(tmp_path):
    assert setup.build_test_job(tmp_path)["ProcessType"] == "Interactive"


def test_no_cap_shows_month_total_only(tmp_path):
    log = tmp_path / "log.csv"
    write_sample_day(log)
    graphs.make_all(date(2026, 11, 20), log, tmp_path / "graphs")
    page = (tmp_path / "graphs" / "daily" / "2026-11-20.html").read_text()
    assert "7 GB used by tests in November so far" in page
    assert "cap" not in page.split("used by tests in November")[0][-40:]


def test_small_cap_shown_in_gb(tmp_path, use_settings):
    use_settings(data_cap_gb=300)
    assert "of the 300 GB cap" in graphs.data_use_line([], date(2026, 11, 20))


def test_schedule_follows_settings():
    settings = dict(config.DEFAULT_SETTINGS, first_hour=8, last_hour=21,
                    minutes_between_tests=30)
    times = setup.test_times(settings)
    assert len(times) == 28
    assert times[0] == {"Hour": 8, "Minute": 0}
    assert times[-1] == {"Hour": 21, "Minute": 30}


def test_accept_ookla_requires_typing_yes(tmp_path, monkeypatch):
    settings_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", settings_file)
    assert setup.accept_ookla(ask=lambda prompt: "no") == 1
    assert not settings_file.exists()
    assert setup.accept_ookla(ask=lambda prompt: " YES ") == 0
    assert config.load_settings(settings_file)["ookla_terms_accepted"] is True


def test_day_graph_uses_earlier_days_for_a_sparse_day(tmp_path):
    log = tmp_path / "log.csv"
    for day in ("16", "17", "18"):
        logfile.append_row({"timestamp": f"2026-11-{day}T12:00:00", "status": "ok",
                            "download_mbps": 400.0, "upload_mbps": 40.0}, log)
    logfile.append_row({"timestamp": "2026-11-19T12:00:00", "status": "ok",
                        "download_mbps": 90.0, "upload_mbps": 40.0}, log)
    graphs.make_all(date(2026, 11, 19), log, tmp_path / "graphs", one_day=date(2026, 11, 19))
    page = (tmp_path / "graphs" / "daily" / "2026-11-19.html").read_text()
    assert "download 90 Mbps vs 7-day typical 400" in page


def write_outage_checks(path, pairs):
    from wifiblame import outages
    for time, status in pairs:
        outages.append_check({"timestamp": f"2026-11-20T{time}:00", "status": status}, path)


def test_plan_line_and_outage_bands_on_graphs(tmp_path, use_settings):
    use_settings(plan_download_mbps=1000)
    log = tmp_path / "log.csv"
    write_sample_day(log)
    checks = tmp_path / "outages.csv"
    write_outage_checks(checks, [("08:00", "ok"), ("08:01", "provider_down"),
                                 ("08:02", "provider_down"), ("08:03", "provider_down"),
                                 ("08:04", "ok")])
    graphs.make_all(date(2026, 11, 20), log, tmp_path / "graphs", outage_file=checks)
    page = (tmp_path / "graphs" / "daily" / "2026-11-20.html").read_text()
    assert "Plan download 1000 Mbps" in page
    assert "(40% of plan)" in page
    assert "provider outage 11/20/2026 8:01 AM, 3 min" in page
    assert "1 outage (3 min)" in page
    png = tmp_path / "graphs" / "daily" / "2026-11-20.png"
    assert png.stat().st_size > 1000


def test_apple_tests_marked_on_graphs(tmp_path):
    log = tmp_path / "log.csv"
    write_sample_day(log)
    logfile.append_row({"timestamp": "2026-11-20T10:00:00", "status": "ok",
                        "download_mbps": 587.0, "upload_mbps": 111.0, "tool": "apple",
                        "server_name": "Apple", "vpn_on": False, "on_power": True}, log)
    data = graphs.GraphData(logfile.read_rows(log))
    assert data.apple_y == [587.0, 111.0]
    graphs.make_all(date(2026, 11, 20), log, tmp_path / "graphs")
    page = (tmp_path / "graphs" / "daily" / "2026-11-20.html").read_text()
    assert "Tested with Apple" in page
    assert "Tested with: Apple" in page

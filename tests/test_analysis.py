from wifiblame import analysis


def ok(time, down, up, **extra):
    row = {"timestamp": f"2026-11-20T{time}:00", "status": "ok",
           "download_mbps": down, "upload_mbps": up,
           "vpn_on": False, "on_power": True, "background_mbps": 0.1}
    row.update(extra)
    return row


def test_unreliable_reasons():
    assert analysis.unreliable_reasons(ok("07:00", 1, 1)) == []
    assert analysis.unreliable_reasons(ok("07:00", 1, 1, vpn_on=True)) == ["VPN on"]
    assert analysis.unreliable_reasons(ok("07:00", 1, 1, on_power=False)) == []
    busy = analysis.unreliable_reasons(ok("07:00", 1, 1, background_mbps=12.0))
    assert busy == ["background download 12.0 Mbps"]


def test_low_power_mode_is_unreliable():
    assert analysis.unreliable_reasons(ok("07:00", 1, 1, low_power_mode=True)) == ["Low Power Mode"]
    assert not analysis.is_unreliable(ok("07:00", 1, 1, low_power_mode=False))


def test_month_and_day_data_use():
    from datetime import date
    rows = [ok("07:00", 1, 1, data_used_mb=1000.0), ok("07:10", 1, 1, data_used_mb=500.0),
            ok("07:20", 1, 1)]
    last_month = ok("07:00", 1, 1, data_used_mb=9000.0)
    last_month["timestamp"] = "2026-10-31T07:00:00"
    next_day = ok("07:00", 1, 1, data_used_mb=700.0)
    next_day["timestamp"] = "2026-11-21T07:00:00"
    rows += [last_month, next_day]
    assert analysis.month_data_used_gb(rows, date(2026, 11, 20)) == 1.5
    assert analysis.month_data_used_gb(rows, date(2026, 11, 21)) == 2.2
    assert analysis.day_data_used_gb(rows, date(2026, 11, 20)) == 1.5


def test_battery_and_unknown_power_are_reliable():
    assert not analysis.is_unreliable(ok("07:00", 1, 1, on_power=False))
    assert not analysis.is_unreliable(ok("07:00", 1, 1, on_power=None))


def test_slow_test_on_battery_is_still_flagged():
    rows = [ok("07:00", 400, 40), ok("07:10", 400, 40), ok("07:20", 400, 40),
            ok("07:30", 100, 40, on_power=False)]
    assert list(analysis.find_anomalies(rows)) == [3]


def test_slow_test_is_flagged():
    rows = [ok("07:00", 400, 40), ok("07:10", 410, 41), ok("07:20", 390, 39),
            ok("07:30", 150, 40)]
    anomalies = analysis.find_anomalies(rows)
    assert list(anomalies) == [3]
    assert anomalies[3][0][0] == "download"


def test_slow_upload_flagged_separately():
    rows = [ok("07:00", 400, 40), ok("07:10", 410, 41), ok("07:20", 390, 39),
            ok("07:30", 400, 10)]
    anomalies = analysis.find_anomalies(rows)
    assert [kind for kind, _ in anomalies[3]] == ["upload"]


def test_exactly_half_is_not_flagged():
    rows = [ok("07:00", 400, 40), ok("07:10", 400, 40), ok("07:20", 400, 40),
            ok("07:30", 200, 40)]
    assert analysis.find_anomalies(rows) == {}


def test_failed_test_is_always_an_anomaly():
    rows = [{"timestamp": "2026-11-20T07:00:00", "status": "failed", "error": "timed out"}]
    anomalies = analysis.find_anomalies(rows)
    assert anomalies[0][0] == ("failed", "test failed: timed out")


def test_skipped_runs_are_never_anomalies():
    rows = [{"timestamp": "2026-11-20T07:00:00", "status": "not_home"},
            {"timestamp": "2026-11-20T07:10:00", "status": "no_network"}]
    assert analysis.find_anomalies(rows) == {}


def test_unreliable_tests_excluded_from_typical_and_not_flagged():
    rows = [ok("07:00", 400, 40), ok("07:10", 400, 40), ok("07:20", 400, 40),
            ok("07:30", 20, 2, vpn_on=True), ok("07:40", 20, 2, vpn_on=True),
            ok("07:50", 20, 2, vpn_on=True), ok("08:00", 20, 2, vpn_on=True)]
    assert analysis.typical_speed(rows, "download_mbps") == 400
    assert analysis.find_anomalies(rows) == {}


def test_too_few_tests_means_no_slow_flags():
    rows = [ok("07:00", 400, 40), ok("07:10", 10, 40)]
    assert analysis.typical_speed(rows, "download_mbps") is None
    assert analysis.find_anomalies(rows) == {}


def test_each_day_judged_against_its_own_typical():
    day_one = [ok("07:00", 400, 40), ok("07:10", 400, 40), ok("07:20", 400, 40)]
    day_two = []
    for time in ("07:00", "07:10", "07:20"):
        row = ok(time, 100, 10)
        row["timestamp"] = row["timestamp"].replace("11-20", "11-21")
        day_two.append(row)
    assert analysis.find_anomalies(day_one + day_two) == {}


def test_loaded_delay_takes_the_worse():
    row = ok("07:00", 1, 1, download_loaded_ms=40.0, upload_loaded_ms=90.0)
    assert analysis.loaded_delay_ms(row) == 90.0
    assert analysis.loaded_delay_ms(ok("07:00", 1, 1)) is None


def test_asleep_runs_are_never_anomalies():
    rows = [{"timestamp": "2026-11-20T21:56:00", "status": "asleep",
             "error": '{"error":"Cannot read: "}'}]
    assert analysis.find_anomalies(rows) == {}


def test_sparse_day_judged_against_last_7_days():
    history = []
    for day in ("16", "17", "18"):
        row = ok("12:00", 400, 40)
        row["timestamp"] = f"2026-11-{day}T12:00:00"
        history.append(row)
    today = ok("12:00", 100, 40)
    anomalies = analysis.find_anomalies([today], history + [today])
    assert anomalies[0] == [("download", "download 100 Mbps vs 7-day typical 400")]


def test_baseline_ignores_tests_older_than_7_days():
    old = ok("12:00", 400, 40)
    old["timestamp"] = "2026-11-10T12:00:00"
    today = ok("12:00", 100, 40)
    assert analysis.find_anomalies([today], [old, old, old, today]) == {}


def test_asleep_and_data_limit_runs_are_never_anomalies():
    rows = [{"timestamp": "2026-11-20T08:00:00", "status": "data_limit",
             "error": "daily data limit"}]
    assert analysis.find_anomalies(rows) == {}


def test_rate_limited_runs_are_never_anomalies():
    rows = [{"timestamp": "2026-11-21T15:00:00", "status": "rate_limited",
             "error": "Too many requests received."}]
    assert analysis.find_anomalies(rows) == {}

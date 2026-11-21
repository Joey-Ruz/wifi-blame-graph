from datetime import datetime

from wifiblame import collect, config, logfile, runner

HOME_MAC = "04:a1:0b:c2:d3:e4"
SETTINGS = dict(config.DEFAULT_SETTINGS, home_router_macs=[HOME_MAC],
                ookla_terms_accepted=True)


class FakeProbe:
    """Stands in for the collect module so no real network tools run."""

    SpeedtestError = collect.SpeedtestError

    def __init__(self, router_ip="192.168.1.1", router_mac=HOME_MAC, speedtest=None,
                 route_interface="en0", awake_readings=None, apple=None):
        self.router_ip = router_ip
        self.router_mac = router_mac
        self.speedtest = speedtest
        self.route_interface = route_interface
        self.speedtest_runs = 0
        self.apple = apple
        self.apple_runs = 0
        # Successive answers to "is the Mac fully awake?"; True once used up.
        self.awake_readings = list(awake_readings or [])

    def get_fully_awake(self):
        if self.awake_readings:
            return self.awake_readings.pop(0)
        return True

    def get_wifi_interface(self):
        return "en0"

    def get_router_ip(self, interface):
        return self.router_ip

    def get_router_mac(self, router_ip):
        return self.router_mac

    def is_home(self, router_mac, home_router_macs):
        return collect.is_home(router_mac, home_router_macs)

    def get_wifi_details(self, interface):
        return {"signal_dbm": -50.0, "noise_dbm": -95.0, "band": "5 GHz", "channel": "36"}

    def get_router_ping_ms(self, router_ip):
        return 3.5

    def get_on_power(self):
        return True

    def get_low_power_mode(self):
        return False

    def get_default_route_interface(self):
        return self.route_interface

    def is_vpn_interface(self, name):
        return collect.is_vpn_interface(name)

    def get_background_mbps(self, interface):
        return 0.2

    def run_apple_test(self, settings):
        self.apple_runs += 1
        if isinstance(self.apple, Exception):
            raise self.apple
        return dict(self.apple)

    def run_speedtest(self, settings):
        self.speedtest_runs += 1
        if isinstance(self.speedtest, Exception):
            raise self.speedtest
        return dict(self.speedtest)


GOOD_RESULT = {"download_mbps": 400.0, "upload_mbps": 40.0, "ping_ms": 12.0,
               "result_url": "https://www.speedtest.net/result/c/abc",
               "data_used_mb": 1100.0, "ookla_says_vpn": False}
MORNING = datetime(2026, 11, 20, 7, 0)


def test_successful_test_logs_every_detail(tmp_path):
    log = tmp_path / "log.csv"
    row = runner.run_once(MORNING, SETTINGS, log, FakeProbe(speedtest=GOOD_RESULT))
    assert row["status"] == logfile.STATUS_OK
    saved = logfile.read_rows(log)[0]
    assert saved["download_mbps"] == 400.0
    assert saved["signal_dbm"] == -50.0
    assert saved["band"] == "5 GHz"
    assert saved["router_ping_ms"] == 3.5
    assert saved["vpn_on"] is False
    assert saved["on_power"] is True
    assert saved["background_mbps"] == 0.2
    assert saved["result_url"] == "https://www.speedtest.net/result/c/abc"
    assert saved["low_power_mode"] is False
    assert saved["data_used_mb"] == 1100.0


def test_away_from_home_skips_the_test(tmp_path):
    log = tmp_path / "log.csv"
    probe = FakeProbe(router_mac="11:22:33:44:55:66", speedtest=GOOD_RESULT)
    row = runner.run_once(MORNING, SETTINGS, log, probe)
    assert row["status"] == logfile.STATUS_NOT_HOME
    assert probe.speedtest_runs == 0
    assert logfile.read_rows(log)[0]["status"] == "not_home"


def test_no_wifi_logs_no_network(tmp_path):
    log = tmp_path / "log.csv"
    probe = FakeProbe(router_ip=None, speedtest=GOOD_RESULT)
    row = runner.run_once(MORNING, SETTINGS, log, probe)
    assert row["status"] == logfile.STATUS_NO_NETWORK
    assert probe.speedtest_runs == 0


def test_failed_test_is_logged_not_dropped(tmp_path):
    log = tmp_path / "log.csv"
    probe = FakeProbe(speedtest=collect.SpeedtestError("speed test timed out"))
    row = runner.run_once(MORNING, SETTINGS, log, probe)
    assert row["status"] == logfile.STATUS_FAILED
    saved = logfile.read_rows(log)[0]
    assert saved["error"] == "speed test timed out"
    assert saved["signal_dbm"] == -50.0


def test_vpn_detected_from_route_or_ookla(tmp_path):
    by_route = runner.run_once(MORNING, SETTINGS, tmp_path / "a.csv",
                               FakeProbe(speedtest=GOOD_RESULT, route_interface="utun3"))
    assert by_route["vpn_on"] is True
    ookla_vpn = dict(GOOD_RESULT, ookla_says_vpn=True)
    by_ookla = runner.run_once(MORNING, SETTINGS, tmp_path / "b.csv",
                               FakeProbe(speedtest=ookla_vpn))
    assert by_ookla["vpn_on"] is True


def test_outside_hours_logs_nothing(tmp_path):
    log = tmp_path / "log.csv"
    probe = FakeProbe(speedtest=GOOD_RESULT)
    for moment in (datetime(2026, 11, 20, 6, 50), datetime(2026, 11, 21, 0, 0),
                   datetime(2026, 11, 21, 1, 30)):
        assert runner.run_once(moment, SETTINGS, log, probe) is None
    assert not log.exists()
    assert probe.speedtest_runs == 0


def test_last_slot_of_day_still_runs(tmp_path):
    row = runner.run_once(datetime(2026, 11, 20, 23, 50), SETTINGS, tmp_path / "log.csv",
                          FakeProbe(speedtest=GOOD_RESULT))
    assert row["status"] == logfile.STATUS_OK


def test_brief_sleep_wake_skips_the_test(tmp_path):
    log = tmp_path / "log.csv"
    probe = FakeProbe(speedtest=GOOD_RESULT, awake_readings=[False])
    row = runner.run_once(datetime(2026, 11, 20, 21, 56), SETTINGS, log, probe)
    assert row["status"] == logfile.STATUS_ASLEEP
    assert probe.speedtest_runs == 0
    assert logfile.read_rows(log)[0]["status"] == "asleep"


def test_failure_from_lid_closing_mid_test_is_logged_asleep(tmp_path):
    log = tmp_path / "log.csv"
    probe = FakeProbe(speedtest=collect.SpeedtestError('{"error":"Cannot read: "}'),
                      awake_readings=[True, False])
    row = runner.run_once(MORNING, SETTINGS, log, probe)
    assert row["status"] == logfile.STATUS_ASLEEP
    assert row["error"] == '{"error":"Cannot read: "}'


def test_real_failure_while_awake_stays_failed(tmp_path):
    probe = FakeProbe(speedtest=collect.SpeedtestError("speed test timed out"),
                      awake_readings=[True, True])
    row = runner.run_once(MORNING, SETTINGS, tmp_path / "log.csv", probe)
    assert row["status"] == logfile.STATUS_FAILED


def test_unknown_wake_state_still_tests(tmp_path):
    probe = FakeProbe(speedtest=GOOD_RESULT, awake_readings=[None])
    row = runner.run_once(MORNING, SETTINGS, tmp_path / "log.csv", probe)
    assert row["status"] == logfile.STATUS_OK


def test_data_limit_skips_the_test(tmp_path):
    log = tmp_path / "log.csv"
    logfile.append_row({"timestamp": "2026-11-20T06:59:00", "status": "ok",
                        "data_used_mb": 19500.0}, log)
    probe = FakeProbe(speedtest=GOOD_RESULT)
    settings = dict(SETTINGS, daily_data_limit_gb=20)
    row = runner.run_once(MORNING, settings, log, probe)
    assert row["status"] == logfile.STATUS_DATA_LIMIT
    assert row["error"].startswith("daily data limit")
    assert probe.speedtest_runs == 0


def test_ookla_refusal_logged_as_rate_limited(tmp_path):
    log = tmp_path / "log.csv"
    probe = FakeProbe(speedtest=collect.SpeedtestRateLimited("Too many requests received."))
    row = runner.run_once(MORNING, dict(SETTINGS, speed_test_tool="ookla"), log, probe)
    assert row["status"] == logfile.STATUS_RATE_LIMITED
    assert logfile.read_rows(log)[0]["error"] == "Too many requests received."


APPLE_RESULT = {"download_mbps": 587.0, "upload_mbps": 111.0, "ping_ms": 38.0,
                "server_name": "Apple", "data_used_mb": 1074.6, "tool": "apple"}


def test_default_falls_back_to_apple_when_ookla_refuses(tmp_path):
    log = tmp_path / "log.csv"
    probe = FakeProbe(speedtest=collect.SpeedtestRateLimited("Too many requests received."),
                      apple=APPLE_RESULT)
    row = runner.run_once(MORNING, SETTINGS, log, probe)
    assert row["status"] == logfile.STATUS_OK
    assert probe.speedtest_runs == 1 and probe.apple_runs == 1
    saved = logfile.read_rows(log)[0]
    assert saved["tool"] == "apple"
    assert saved["download_mbps"] == 587.0


def test_default_uses_ookla_when_it_works(tmp_path):
    probe = FakeProbe(speedtest=dict(GOOD_RESULT, tool="ookla"), apple=APPLE_RESULT)
    row = runner.run_once(MORNING, SETTINGS, tmp_path / "log.csv", probe)
    assert row["tool"] == "ookla"
    assert probe.apple_runs == 0


def test_real_ookla_failure_does_not_fall_back(tmp_path):
    probe = FakeProbe(speedtest=collect.SpeedtestError('{"error":"Cannot read: "}'),
                      apple=APPLE_RESULT)
    row = runner.run_once(MORNING, SETTINGS, tmp_path / "log.csv", probe)
    assert row["status"] == logfile.STATUS_FAILED
    assert probe.apple_runs == 0


def test_refusal_then_apple_failure_is_a_failure(tmp_path):
    probe = FakeProbe(speedtest=collect.SpeedtestRateLimited("Too many requests received."),
                      apple=collect.SpeedtestError("Apple test timed out"))
    row = runner.run_once(MORNING, SETTINGS, tmp_path / "log.csv", probe)
    assert row["status"] == logfile.STATUS_FAILED
    assert "Apple backup failed: Apple test timed out" in row["error"]


def test_apple_only_never_runs_ookla(tmp_path):
    probe = FakeProbe(speedtest=GOOD_RESULT, apple=APPLE_RESULT)
    row = runner.run_once(MORNING, dict(SETTINGS, speed_test_tool="apple"),
                          tmp_path / "log.csv", probe)
    assert row["tool"] == "apple"
    assert probe.speedtest_runs == 0


def test_ookla_only_never_runs_apple(tmp_path):
    probe = FakeProbe(speedtest=collect.SpeedtestRateLimited("Too many requests received."),
                      apple=APPLE_RESULT)
    runner.run_once(MORNING, dict(SETTINGS, speed_test_tool="ookla"), tmp_path / "log.csv", probe)
    assert probe.apple_runs == 0

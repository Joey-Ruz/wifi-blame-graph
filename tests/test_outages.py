from datetime import datetime

from wifiblame import collect, config, outages, setup

HOME_MAC = "04:a1:0b:c2:d3:e4"
SETTINGS = dict(config.DEFAULT_SETTINGS, home_router_macs=[HOME_MAC])
NOON = datetime(2026, 11, 21, 12, 0)


class FakeProbe:
    def __init__(self, router_ip="192.168.1.1", router_mac=HOME_MAC, answers=None, awake=True):
        self.router_ip = router_ip
        self.router_mac = router_mac
        self.answers = answers or {}
        self.awake = awake
        self.pinged = []

    def get_fully_awake(self):
        return self.awake

    def get_wifi_interface(self):
        return "en0"

    def get_router_ip(self, interface):
        return self.router_ip

    def get_router_mac(self, router_ip):
        return self.router_mac

    def is_home(self, router_mac, home_router_macs):
        return collect.is_home(router_mac, home_router_macs)

    def ping_once(self, host):
        self.pinged.append(host)
        return self.answers.get(host, False)


def everything_answers():
    return {"192.168.1.1": True, "1.1.1.1": True, "8.8.8.8": True}


def test_connected_check_is_ok(tmp_path):
    log = tmp_path / "outages.csv"
    probe = FakeProbe(answers=everything_answers())
    assert outages.check_once(NOON, SETTINGS, log, probe)["status"] == "ok"
    assert probe.pinged == ["1.1.1.1"]
    assert outages.read_checks(log)[0]["status"] == "ok"


def test_router_up_internet_down_is_provider(tmp_path):
    probe = FakeProbe(answers={"192.168.1.1": True})
    row = outages.check_once(NOON, SETTINGS, tmp_path / "o.csv", probe)
    assert row["status"] == "provider_down"


def test_nothing_answers_is_wifi(tmp_path):
    row = outages.check_once(NOON, SETTINGS, tmp_path / "o.csv", FakeProbe())
    assert row["status"] == "wifi_down"


def test_unreadable_router_still_checked(tmp_path):
    row = outages.check_once(NOON, SETTINGS, tmp_path / "o.csv", FakeProbe(router_mac=None))
    assert row["status"] == "wifi_down"


def test_no_wifi_logged(tmp_path):
    row = outages.check_once(NOON, SETTINGS, tmp_path / "o.csv", FakeProbe(router_ip=None))
    assert row["status"] == "no_wifi"


def test_skipped_away_asleep_or_outside_hours(tmp_path):
    log = tmp_path / "o.csv"
    assert outages.check_once(NOON, SETTINGS, log, FakeProbe(router_mac="11:22:33:44:55:66")) is None
    assert outages.check_once(NOON, SETTINGS, log, FakeProbe(awake=False)) is None
    assert outages.check_once(datetime(2026, 11, 21, 6, 59), SETTINGS, log, FakeProbe()) is None
    assert not log.exists()


def checks(*pairs):
    rows = []
    for time, status in pairs:
        rows.append({"timestamp": f"2026-11-21T{time}:00", "status": status})
    return rows


def test_three_missed_in_a_row_is_an_outage():
    found = outages.find_outages(checks(("12:00", "ok"), ("12:01", "provider_down"),
                                        ("12:02", "provider_down"), ("12:03", "provider_down"),
                                        ("12:04", "ok")))
    assert len(found) == 1
    assert found[0]["kind"] == "provider"
    assert found[0]["start"] == datetime(2026, 11, 21, 12, 1)
    assert found[0]["minutes"] == 3


def test_two_missed_is_not_an_outage():
    found = outages.find_outages(checks(("12:00", "ok"), ("12:01", "wifi_down"),
                                        ("12:02", "wifi_down"), ("12:03", "ok")))
    assert found == []


def test_wifi_outage_includes_dropped_wifi_after_being_home():
    found = outages.find_outages(checks(("12:00", "ok"), ("12:01", "wifi_down"),
                                        ("12:02", "no_wifi"), ("12:03", "no_wifi"),
                                        ("12:04", "ok")))
    assert found[0]["kind"] == "Wi-Fi"
    assert found[0]["minutes"] == 3


def test_no_wifi_while_away_is_not_an_outage():
    found = outages.find_outages(checks(("12:00", "no_wifi"), ("12:01", "no_wifi"),
                                        ("12:02", "no_wifi"), ("12:03", "no_wifi")))
    assert found == []


def test_long_gap_ends_the_run():
    found = outages.find_outages(checks(("12:00", "provider_down"), ("12:01", "provider_down"),
                                        ("12:30", "provider_down")))
    assert found == []


def test_outage_job_runs_every_minute(tmp_path):
    job = setup.build_outage_job(tmp_path)
    assert job["StartInterval"] == 60
    assert "StartCalendarInterval" not in job
    assert job["ProgramArguments"][-1] == "wifiblame.outages"
    assert "StartInterval" not in setup.build_test_job(tmp_path)

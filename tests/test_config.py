import json

import pytest

from wifiblame import collect, config


def write(tmp_path, values):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values))
    return path


def test_missing_file_means_defaults(tmp_path):
    settings = config.load_settings(tmp_path / "none.json")
    assert settings == config.DEFAULT_SETTINGS


def test_saved_values_override_defaults(tmp_path):
    settings = config.load_settings(write(tmp_path, {"minutes_between_tests": 30,
                                                     "data_cap_gb": 1000}))
    assert settings["minutes_between_tests"] == 30
    assert settings["data_cap_gb"] == 1000
    assert settings["first_hour"] == 7


@pytest.mark.parametrize("values", [
    {"minutes_between_tests": 7},
    {"minutes_between_tests": 90},
    {"daily_data_limit_gb": 0},
    {"pause_at_percent_of_cap": 25},
    {"data_cap_gb": 1000, "pause_at_percent_of_cap": 150},
    {"minutes_between_tests": 0},
    {"first_hour": 22, "last_hour": 6},
    {"last_hour": 24},
    {"data_cap_gb": -5},
    {"home_router_macs": "02:00:00:00:00:02"},
    {"speed_test_tool": "fast.com"},
])
def test_bad_values_are_rejected(tmp_path, values):
    with pytest.raises(config.SettingsError):
        config.load_settings(write(tmp_path, values))


def test_misspelled_setting_is_rejected(tmp_path):
    with pytest.raises(config.SettingsError):
        config.load_settings(write(tmp_path, {"minutes_between_test": 10}))


def test_configured_speedtest_path_wins():
    settings = dict(config.DEFAULT_SETTINGS, speedtest_path="/custom/speedtest")
    assert config.find_speedtest(settings) == "/custom/speedtest"


def test_speedtest_refuses_without_accepted_terms(monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("speedtest should not start")
    monkeypatch.setattr(collect.subprocess, "run", must_not_run)
    settings = dict(config.DEFAULT_SETTINGS, speedtest_path="/custom/speedtest")
    with pytest.raises(collect.SpeedtestError, match="terms not accepted"):
        collect.run_speedtest(settings)


def test_speedtest_sends_acceptance_once_accepted(monkeypatch):
    seen = {}

    class Done:
        returncode = 0
        stdout = '{"type": "result", "download": {"bandwidth": 1000000}}\n'
        stderr = ""

    def fake_run(command, **kwargs):
        seen["command"] = command
        return Done()
    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    settings = dict(config.DEFAULT_SETTINGS, speedtest_path="/custom/speedtest",
                    ookla_terms_accepted=True)
    result = collect.run_speedtest(settings)
    assert "--accept-license" in seen["command"] and "--accept-gdpr" in seen["command"]
    assert result["download_mbps"] == 8.0


def test_once_a_day_and_limits_accepted(tmp_path):
    settings = config.load_settings(write(tmp_path, {
        "minutes_between_tests": 1440, "data_cap_gb": 1000,
        "daily_data_limit_gb": 20, "pause_at_percent_of_cap": 25}))
    assert settings["minutes_between_tests"] == 1440

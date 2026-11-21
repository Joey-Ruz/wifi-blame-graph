"""Paths, user settings (config.json), and fixed thresholds."""

import json
import os
import shutil
from functools import lru_cache
from pathlib import Path

from wifiblame import schedule

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"
GRAPHS_DIR = PROJECT_DIR / "graphs"
LOG_FILE = DATA_DIR / "speed_log.csv"
OUTAGE_LOG_FILE = DATA_DIR / "outage_log.csv"
# WIFIBLAME_CONFIG lets the tests point at a throwaway settings file.
CONFIG_FILE = Path(os.environ.get("WIFIBLAME_CONFIG", PROJECT_DIR / "config.json"))

# Settings a user can change in config.json. Anything left out uses these.
DEFAULT_SETTINGS = {
    "home_router_macs": [],
    "first_hour": 7,
    "last_hour": 23,
    "minutes_between_tests": 10,
    "data_cap_gb": None,
    "daily_data_limit_gb": None,
    "pause_at_percent_of_cap": None,
    "background_limit_mbps": 5.0,
    "speedtest_path": None,
    "ookla_terms_accepted": False,
    "blocked_servers": [],
    "speed_test_tool": "ookla_then_apple",
    "plan_download_mbps": None,
    "plan_upload_mbps": None,
}

SPEEDTEST_TIMEOUT_SECONDS = 180
SERVER_CACHE_FILE = DATA_DIR / "ookla_servers.json"
SERVER_CACHE_SECONDS = 24 * 60 * 60
SPEEDTEST_SEARCH_PATHS = ["/opt/homebrew/bin/speedtest", "/usr/local/bin/speedtest"]

# A test is "slow" when it is under this share of the day's typical speed.
ANOMALY_SHARE_OF_TYPICAL = 0.5
# Fewer reliable tests than this in a day and no "slow" anomalies are flagged,
# because the typical speed isn't trustworthy yet.
MIN_TESTS_FOR_TYPICAL = 3

# Missed connection checks in a row (one a minute) that make an outage.
MIN_CHECKS_FOR_OUTAGE = 3

BACKGROUND_SAMPLE_SECONDS = 5
ROUTER_PING_COUNT = 5


# "ookla_then_apple": Ookla, and Apple's built-in tester when Ookla refuses.
SPEED_TEST_TOOLS = ("ookla_then_apple", "ookla", "apple")


def uses_ookla(settings):
    return settings["speed_test_tool"] in ("ookla_then_apple", "ookla")


class SettingsError(Exception):
    pass


def validate(settings):
    first = settings["first_hour"]
    last = settings["last_hour"]
    step = settings["minutes_between_tests"]
    if not (isinstance(first, int) and isinstance(last, int) and 0 <= first <= last <= 23):
        raise SettingsError("first_hour and last_hour must be whole hours 0-23, first <= last.")
    if not schedule.is_valid_interval(step):
        raise SettingsError("minutes_between_tests must divide evenly into an hour "
                            "(such as 5, 10, 15, 20, 30 or 60) or be whole hours that "
                            "divide a day: 120, 180, 240, 360, 480, 720 or 1440 (once a day).")
    for name in ("data_cap_gb", "daily_data_limit_gb"):
        value = settings[name]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or value <= 0):
            raise SettingsError(f"{name} must be a positive number of GB, or null for none.")
    percent = settings["pause_at_percent_of_cap"]
    if percent is not None:
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 < percent <= 100:
            raise SettingsError("pause_at_percent_of_cap must be between 1 and 100, or null.")
        if settings["data_cap_gb"] is None:
            raise SettingsError("pause_at_percent_of_cap needs data_cap_gb to be set.")
    if not isinstance(settings["home_router_macs"], list):
        raise SettingsError("home_router_macs must be a list.")
    if settings["speed_test_tool"] not in SPEED_TEST_TOOLS:
        raise SettingsError("speed_test_tool must be one of: " + ", ".join(SPEED_TEST_TOOLS))
    if not isinstance(settings["blocked_servers"], list):
        raise SettingsError("blocked_servers must be a list of server names or ids.")
    for name in ("plan_download_mbps", "plan_upload_mbps"):
        value = settings[name]
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                  or value <= 0):
            raise SettingsError(f"{name} must be a positive number of Mbps, or null.")


def load_settings(config_file=None):
    """Read config.json over the defaults. A missing file means all defaults."""
    if config_file is None:
        config_file = CONFIG_FILE
    settings = dict(DEFAULT_SETTINGS)
    settings["home_router_macs"] = []
    settings["blocked_servers"] = []
    if Path(config_file).exists():
        with open(config_file) as handle:
            saved = json.load(handle)
        unknown = set(saved) - set(DEFAULT_SETTINGS)
        if unknown:
            raise SettingsError(f"Unknown settings in {config_file}: {sorted(unknown)}")
        settings.update(saved)
    validate(settings)
    return settings


@lru_cache(maxsize=1)
def current():
    """The settings for this run, read once."""
    return load_settings()


def save_settings(settings, config_file=None):
    if config_file is None:
        config_file = CONFIG_FILE
    validate(settings)
    with open(config_file, "w") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    current.cache_clear()


def find_speedtest(settings):
    """The Ookla tool: the configured path, else wherever Homebrew put it."""
    if settings.get("speedtest_path"):
        return settings["speedtest_path"]
    for candidate in SPEEDTEST_SEARCH_PATHS:
        if Path(candidate).exists():
            return candidate
    return shutil.which("speedtest")

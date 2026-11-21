from datetime import datetime

import pytest

from wifiblame import budget, config, schedule


def settings_with(**changes):
    settings = dict(config.DEFAULT_SETTINGS)
    settings.update(changes)
    return settings


@pytest.mark.parametrize("minutes", [5, 10, 15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440])
def test_supported_intervals(minutes):
    assert schedule.is_valid_interval(minutes)


@pytest.mark.parametrize("minutes", [0, 7, 45, 90, 300, 2880, True, 10.0])
def test_unsupported_intervals(minutes):
    assert not schedule.is_valid_interval(minutes)


def test_every_5_minutes():
    times = schedule.test_times(settings_with(minutes_between_tests=5))
    assert len(times) == 17 * 12
    assert times[1] == {"Hour": 7, "Minute": 5}
    assert times[-1] == {"Hour": 23, "Minute": 55}


def test_hourly():
    times = schedule.test_times(settings_with(minutes_between_tests=60))
    assert times == [{"Hour": hour, "Minute": 0} for hour in range(7, 24)]


def test_every_6_hours_counts_from_first_hour():
    times = schedule.test_times(settings_with(minutes_between_tests=360))
    assert times == [{"Hour": 7, "Minute": 0}, {"Hour": 13, "Minute": 0},
                     {"Hour": 19, "Minute": 0}]


def test_once_a_day_without_workaround():
    times = schedule.test_times(settings_with(minutes_between_tests=1440, first_hour=12))
    assert times == [{"Hour": 12, "Minute": 0}]


def test_long_gap_stays_inside_the_window():
    times = schedule.test_times(settings_with(minutes_between_tests=720, first_hour=9,
                                              last_hour=17))
    assert times == [{"Hour": 9, "Minute": 0}]


def logged(time, mb, day="2026-11-21"):
    return {"timestamp": f"{day}T{time}:00", "status": "ok", "data_used_mb": mb}


NOON = datetime(2026, 11, 21, 12, 0)


def test_no_limits_never_blocks():
    rows = [logged("07:00", 900_000.0)]
    assert budget.blocked_reason(rows, NOON, settings_with()) is None


def test_daily_limit_blocks_a_test_that_would_go_over():
    rows = [logged("07:00", 9000.0), logged("08:00", 9000.0)]  # 18 GB, next ~9 GB
    reason = budget.blocked_reason(rows, NOON, settings_with(daily_data_limit_gb=20))
    assert reason == "daily data limit: tests used 18.0 of 20 GB today"


def test_daily_limit_allows_a_test_that_fits():
    rows = [logged("07:00", 1000.0)]
    assert budget.blocked_reason(rows, NOON, settings_with(daily_data_limit_gb=20)) is None


def test_daily_limit_resets_each_day():
    rows = [logged("07:00", 19000.0, day="2026-11-20"), logged("08:00", 1000.0, day="2026-11-20")]
    assert budget.blocked_reason(rows, NOON, settings_with(daily_data_limit_gb=20)) is None


def test_monthly_pause_at_share_of_cap():
    rows = [logged("07:00", 240_000.0, day="2026-11-02"), logged("08:00", 9000.0)]
    settings = settings_with(data_cap_gb=1000, pause_at_percent_of_cap=25)
    reason = budget.blocked_reason(rows, NOON, settings)
    assert reason.startswith("monthly pause: tests used 249 GB this month")


def test_monthly_pause_resets_next_month():
    rows = [logged("07:00", 260_000.0, day="2026-10-30"),
            logged("08:00", 1000.0, day="2026-10-31")]
    settings = settings_with(data_cap_gb=1000, pause_at_percent_of_cap=25)
    assert budget.blocked_reason(rows, NOON, settings) is None


def test_estimate_without_limits():
    numbers = budget.estimate(settings_with(data_cap_gb=1000), gb_per_test=1.0)
    assert numbers["per_day_gb"] == 102
    assert numbers["per_month_gb"] == 3060
    assert numbers["share_of_cap"] == 306


def test_estimate_respects_daily_limit_and_pause():
    settings = settings_with(data_cap_gb=1000, daily_data_limit_gb=20, pause_at_percent_of_cap=25)
    numbers = budget.estimate(settings, gb_per_test=1.0)
    assert numbers["per_day_gb"] == 20
    assert numbers["per_month_gb"] == 250
    assert numbers["share_of_cap"] == 25


def test_estimate_text_before_any_test():
    text = budget.describe_estimate(settings_with(), [])
    assert text.startswith("102 tests a day. Data estimate available after the first test")


def test_estimate_text_warns_on_large_share():
    text = budget.describe_estimate(settings_with(data_cap_gb=1000), [logged("07:00", 1000.0)])
    assert "306% of your 1000 GB cap" in text
    assert "Warning" in text

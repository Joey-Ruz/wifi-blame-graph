"""When tests run: every minutes_between_tests, from first_hour through the end
of last_hour.

minutes_between_tests can be anything from every few minutes to once a day:
5, 10, 15, 20, 30, 60 (hourly), 120, 180, 240, 360, 480, 720, or 1440 (daily).
Long gaps start counting from first_hour, so 360 with first_hour 7 tests at
7:00 am, 1:00 pm and 7:00 pm; 1440 tests once a day at first_hour.
"""

MINUTES_PER_DAY = 24 * 60


def is_valid_interval(minutes):
    if not isinstance(minutes, int) or isinstance(minutes, bool) or minutes < 1:
        return False
    if minutes <= 60:
        return 60 % minutes == 0
    return minutes % 60 == 0 and MINUTES_PER_DAY % minutes == 0


def test_times(settings):
    """Every scheduled test time in a day, as launchd {"Hour", "Minute"} entries."""
    step = settings["minutes_between_tests"]
    first_minute = settings["first_hour"] * 60
    last_minute = settings["last_hour"] * 60 + 59
    times = []
    minute_of_day = first_minute
    while minute_of_day <= last_minute:
        times.append({"Hour": minute_of_day // 60, "Minute": minute_of_day % 60})
        minute_of_day += step
    return times


def tests_per_day(settings):
    return len(test_times(settings))

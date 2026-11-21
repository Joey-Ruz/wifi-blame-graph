"""Data limits: skip a test when running it would go over the user's limits,
and estimate how much data a schedule will use.

Two optional limits (config.json):
- daily_data_limit_gb: most data the tests may use in one day.
- pause_at_percent_of_cap: stop testing for the rest of the month once the
  tests have used this share of data_cap_gb.

"Would go over" uses the most recent test's data use as the size of the next
test, so a limit is respected before it is crossed, not after.
"""

from wifiblame import analysis, schedule

DAYS_PER_MONTH = 30


def latest_test_gb(rows):
    """Data used by the most recent test that recorded it, or None."""
    for row in reversed(rows):
        if row.get("data_used_mb") is not None:
            return row["data_used_mb"] / 1000
    return None


def blocked_reason(rows, now, settings):
    """Why the next test must be skipped to stay within limits, or None."""
    today = now.date()
    next_test_gb = latest_test_gb(rows) or 0.0

    daily_limit = settings.get("daily_data_limit_gb")
    if daily_limit is not None:
        used_today = analysis.day_data_used_gb(rows, today)
        if used_today + next_test_gb > daily_limit:
            return (f"daily data limit: tests used {used_today:.1f} of {daily_limit:g} GB today")

    pause_percent = settings.get("pause_at_percent_of_cap")
    cap = settings.get("data_cap_gb")
    if pause_percent is not None and cap:
        month_limit = cap * pause_percent / 100
        used_month = analysis.month_data_used_gb(rows, today)
        if used_month + next_test_gb > month_limit:
            return (f"monthly pause: tests used {used_month:.0f} GB this month, "
                    f"the {pause_percent:g}% of the {cap:g} GB cap allowed")
    return None


def estimate(settings, gb_per_test):
    """Rough data use for a schedule, assuming the Mac is awake and home for
    every scheduled test. Returns per-day and per-month GB, and the share of
    the cap (None without a cap)."""
    per_day = schedule.tests_per_day(settings) * gb_per_test
    daily_limit = settings.get("daily_data_limit_gb")
    if daily_limit is not None:
        per_day = min(per_day, daily_limit)
    per_month = per_day * DAYS_PER_MONTH

    cap = settings.get("data_cap_gb")
    pause_percent = settings.get("pause_at_percent_of_cap")
    if cap and pause_percent is not None:
        per_month = min(per_month, cap * pause_percent / 100)

    share_of_cap = None
    if cap:
        share_of_cap = per_month / cap * 100
    return {"per_day_gb": per_day, "per_month_gb": per_month, "share_of_cap": share_of_cap}


def describe_estimate(settings, rows):
    """A plain-language data-use estimate for setup and status."""
    gb_per_test = latest_test_gb(rows)
    count = schedule.tests_per_day(settings)
    if gb_per_test is None:
        return (f"{count} tests a day. Data estimate available after the first test "
                f"(run setup status then).")
    numbers = estimate(settings, gb_per_test)
    text = (f"{count} tests a day at about {gb_per_test:.2f} GB each: "
            f"up to {numbers['per_day_gb']:.0f} GB a day, {numbers['per_month_gb']:.0f} GB a month")
    if numbers["share_of_cap"] is not None:
        text += f" ({numbers['share_of_cap']:.0f}% of your {settings['data_cap_gb']:g} GB cap)"
    text += ". Assumes the Mac is awake and home for every test."
    if numbers["share_of_cap"] is not None and numbers["share_of_cap"] > 50:
        text += (" Warning: that's a large share of your cap. Consider fewer tests, "
                 "daily_data_limit_gb, or pause_at_percent_of_cap.")
    return text

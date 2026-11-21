"""One scheduled run: check the window and the network, test, log one line.

Run with: .venv/bin/python -m wifiblame.runner
"""

import sys
from datetime import datetime

from wifiblame import budget, collect, config, logfile


def in_test_window(now, settings):
    return settings["first_hour"] <= now.hour <= settings["last_hour"]


def run_chosen_test(settings, probe):
    """Ookla, Apple's tester, or Ookla with Apple as backup when Ookla refuses."""
    tool = settings["speed_test_tool"]
    if tool == "apple":
        return probe.run_apple_test(settings)
    if tool == "ookla":
        return probe.run_speedtest(settings)
    try:
        return probe.run_speedtest(settings)
    except collect.SpeedtestRateLimited as refusal:
        try:
            return probe.run_apple_test(settings)
        except collect.SpeedtestError as error:
            raise collect.SpeedtestError(
                f"Ookla refused ({refusal}); Apple backup failed: {error}")


def run_once(now, settings, log_file, probe=collect):
    """Returns the logged row, or None when the run is outside test hours.

    `probe` supplies the network facts; tests pass a fake in its place.
    """
    if not in_test_window(now, settings):
        # A wake-from-sleep catch-up after midnight lands here; skip quietly.
        return None

    row = {"timestamp": now.isoformat(timespec="seconds")}

    # A brief wake while the lid is closed lasts seconds; a test started then
    # gets cut off when the Mac falls back asleep, so skip it.
    if probe.get_fully_awake() is False:
        row["status"] = logfile.STATUS_ASLEEP
        logfile.append_row(row, log_file)
        return row

    interface = probe.get_wifi_interface()
    router_ip = probe.get_router_ip(interface)
    if router_ip is None:
        row["status"] = logfile.STATUS_NO_NETWORK
        row["error"] = "Wi-Fi not connected"
        logfile.append_row(row, log_file)
        return row

    router_mac = probe.get_router_mac(router_ip)
    if not probe.is_home(router_mac, settings["home_router_macs"]):
        row["status"] = logfile.STATUS_NOT_HOME
        logfile.append_row(row, log_file)
        return row

    reason = budget.blocked_reason(logfile.read_rows(log_file), now, settings)
    if reason is not None:
        row["status"] = logfile.STATUS_DATA_LIMIT
        row["error"] = reason
        logfile.append_row(row, log_file)
        return row

    # Gather the side facts before the speed test so they don't compete with it.
    row.update(probe.get_wifi_details(interface))
    row["router_ping_ms"] = probe.get_router_ping_ms(router_ip)
    row["on_power"] = probe.get_on_power()
    row["low_power_mode"] = probe.get_low_power_mode()
    route_interface = probe.get_default_route_interface()
    vpn_by_route = probe.is_vpn_interface(route_interface)
    row["background_mbps"] = probe.get_background_mbps(interface)

    try:
        result = run_chosen_test(settings, probe)
    except collect.SpeedtestError as error:
        row["status"] = logfile.STATUS_FAILED
        if isinstance(error, collect.SpeedtestRateLimited):
            row["status"] = logfile.STATUS_RATE_LIMITED
        row["error"] = str(error)
        # Lid closed mid-test: the failure is the Mac sleeping, not the internet.
        if probe.get_fully_awake() is False:
            row["status"] = logfile.STATUS_ASLEEP
        row["vpn_on"] = vpn_by_route
        logfile.append_row(row, log_file)
        return row

    ookla_says_vpn = result.pop("ookla_says_vpn", None)
    row.update(result)
    row["vpn_on"] = bool(vpn_by_route or ookla_says_vpn)
    row["status"] = logfile.STATUS_OK
    logfile.append_row(row, log_file)
    return row


def main():
    try:
        settings = config.load_settings()
    except config.SettingsError as error:
        print(f"Settings problem: {error}", file=sys.stderr)
        return 1
    if not settings["home_router_macs"]:
        print("No home router recorded yet. Run: .venv/bin/python -m wifiblame.setup record-home",
              file=sys.stderr)
        return 1
    row = run_once(datetime.now(), settings, config.LOG_FILE)
    if row is None:
        print("Outside test hours; nothing logged.")
    else:
        print(f"{row['timestamp']} {row['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

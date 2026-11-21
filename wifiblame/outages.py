"""Outage checks: a tiny connection check every minute during test hours.

Each check pings the router once and up to two well-known internet addresses:
- internet answers                      -> "ok"
- router answers, internet doesn't      -> "provider_down"  (your provider)
- neither answers                       -> "wifi_down"      (your Wi-Fi/router)
- Wi-Fi not connected                   -> "no_wifi"        (a Wi-Fi outage only
                                            right after being home; otherwise away)

MIN_CHECKS_FOR_OUTAGE missed checks in a row make an outage.

Run with: .venv/bin/python -m wifiblame.outages   (the schedule runs it every minute)
"""

import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

from wifiblame import collect, config

FIELDS = ["timestamp", "status"]
STATUS_OK = "ok"
STATUS_PROVIDER_DOWN = "provider_down"
STATUS_WIFI_DOWN = "wifi_down"
STATUS_NO_WIFI = "no_wifi"

INTERNET_HOSTS = ["1.1.1.1", "8.8.8.8"]
# Checks further apart than this (the Mac slept, or was away) end an outage.
MAX_GAP = timedelta(minutes=3)

KIND_PROVIDER = "provider"
KIND_WIFI = "Wi-Fi"


def append_check(row, log_file):
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_file.exists() or log_file.stat().st_size == 0
    with open(log_file, "a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({"timestamp": row["timestamp"], "status": row["status"]})


def read_checks(log_file):
    log_file = Path(log_file)
    if not log_file.exists():
        return []
    with open(log_file, newline="") as handle:
        return list(csv.DictReader(handle))


def check_once(now, settings, log_file, probe=collect):
    """Run one check and log it. Returns the row, or None when nothing is logged
    (outside test hours, Mac asleep, or on someone else's network)."""
    if not settings["first_hour"] <= now.hour <= settings["last_hour"]:
        return None
    if probe.get_fully_awake() is False:
        return None

    row = {"timestamp": now.isoformat(timespec="seconds")}
    interface = probe.get_wifi_interface()
    router_ip = probe.get_router_ip(interface)
    if router_ip is None:
        row["status"] = STATUS_NO_WIFI
        append_check(row, log_file)
        return row

    router_mac = probe.get_router_mac(router_ip)
    # A known router that isn't home means away; an unreadable one may be a
    # router that stopped answering, so keep checking.
    if router_mac is not None and not probe.is_home(router_mac, settings["home_router_macs"]):
        return None

    internet_ok = False
    for host in INTERNET_HOSTS:
        if probe.ping_once(host):
            internet_ok = True
            break
    if internet_ok:
        row["status"] = STATUS_OK
    elif probe.ping_once(router_ip):
        row["status"] = STATUS_PROVIDER_DOWN
    else:
        row["status"] = STATUS_WIFI_DOWN
    append_check(row, log_file)
    return row


def _close_run(run, outages, min_checks):
    if len(run) < min_checks:
        return
    provider_count = 0
    for _moment, kind in run:
        if kind == KIND_PROVIDER:
            provider_count += 1
    kind = KIND_PROVIDER if provider_count * 2 > len(run) else KIND_WIFI
    start = run[0][0]
    end = run[-1][0] + timedelta(minutes=1)
    outages.append({
        "start": start,
        "end": end,
        "kind": kind,
        "checks": len(run),
        "minutes": round((end - start).total_seconds() / 60),
    })


def find_outages(checks, min_checks=None):
    """Turn minute checks into outages: runs of at least min_checks missed
    checks in a row, no more than MAX_GAP apart."""
    if min_checks is None:
        min_checks = config.MIN_CHECKS_FOR_OUTAGE
    ordered = sorted(checks, key=lambda check: check["timestamp"])
    outages = []
    run = []
    was_home = False
    previous = None
    for check in ordered:
        moment = datetime.fromisoformat(check["timestamp"])
        if previous is not None and moment - previous > MAX_GAP:
            _close_run(run, outages, min_checks)
            run = []
            was_home = False
        previous = moment

        status = check["status"]
        if status == STATUS_OK:
            _close_run(run, outages, min_checks)
            run = []
            was_home = True
        elif status == STATUS_PROVIDER_DOWN:
            run.append((moment, KIND_PROVIDER))
            was_home = True
        elif status == STATUS_WIFI_DOWN:
            run.append((moment, KIND_WIFI))
            was_home = True
        elif status == STATUS_NO_WIFI and was_home:
            run.append((moment, KIND_WIFI))
        else:
            _close_run(run, outages, min_checks)
            run = []
    _close_run(run, outages, min_checks)
    return outages


def outages_between(outages, first_day, last_day):
    selected = []
    for outage in outages:
        if first_day <= outage["start"].date() <= last_day:
            selected.append(outage)
    return selected


def main():
    try:
        settings = config.load_settings()
    except config.SettingsError as error:
        print(f"Settings problem: {error}", file=sys.stderr)
        return 1
    if not settings["home_router_macs"]:
        return 0
    check_once(datetime.now(), settings, config.OUTAGE_LOG_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())

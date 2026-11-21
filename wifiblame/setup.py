"""Setup: accept Ookla's terms, record the home router, turn the schedule on or off.

  .venv/bin/python -m wifiblame.setup accept-ookla  read and accept Ookla's terms
  .venv/bin/python -m wifiblame.setup record-home   while on home Wi-Fi
  .venv/bin/python -m wifiblame.setup install       turn the schedule on
  .venv/bin/python -m wifiblame.setup remove        turn the schedule off
  .venv/bin/python -m wifiblame.setup status
"""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from wifiblame import budget, collect, config, logfile, schedule

TEST_LABEL = "local.wifiblamegraph.test"
GRAPH_LABEL = "local.wifiblamegraph.graphs"
OUTAGE_LABEL = "local.wifiblamegraph.outages"
ALL_LABELS = (TEST_LABEL, GRAPH_LABEL, OUTAGE_LABEL)
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
NIGHTLY_GRAPH_HOUR = 23
NIGHTLY_GRAPH_MINUTE = 59


def test_times(settings=None):
    if settings is None:
        settings = config.current()
    return schedule.test_times(settings)


def _job(label, module, schedule, project_dir, every_seconds=None):
    python = str(Path(project_dir) / ".venv" / "bin" / "python")
    log = str(Path(project_dir) / "data" / "schedule.log")
    job = {
        "Label": label,
        "ProgramArguments": [python, "-m", module],
        "WorkingDirectory": str(project_dir),
        "StartCalendarInterval": schedule,
        "StartInterval": every_seconds,
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        "StandardOutPath": log,
        "StandardErrorPath": log,
        # "Interactive" stops macOS from treating the speed test as low-priority
        # background work, which slowed its downloads (24 vs 800 Mbps).
        "ProcessType": "Interactive",
    }
    for key in ("StartCalendarInterval", "StartInterval"):
        if job[key] is None:
            del job[key]
    return job


def build_test_job(project_dir=config.PROJECT_DIR):
    return _job(TEST_LABEL, "wifiblame.runner", test_times(), project_dir)


def build_outage_job(project_dir=config.PROJECT_DIR):
    """Every minute; the check itself skips outside test hours and while asleep."""
    job = _job(OUTAGE_LABEL, "wifiblame.outages", None, project_dir, every_seconds=60)
    job["StandardOutPath"] = "/dev/null"
    return job


def build_graph_job(project_dir=config.PROJECT_DIR):
    schedule = [{"Hour": NIGHTLY_GRAPH_HOUR, "Minute": NIGHTLY_GRAPH_MINUTE}]
    return _job(GRAPH_LABEL, "wifiblame.graphs", schedule, project_dir)


def _plist_path(label):
    return LAUNCH_AGENTS_DIR / f"{label}.plist"


def _launchctl(*args):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)


OOKLA_TERMS = [
    "https://www.speedtest.net/about/eula",
    "https://www.speedtest.net/about/terms",
    "https://www.speedtest.net/about/privacy",
]


def accept_ookla(ask=input):
    """Ookla requires each user to accept its license, terms and privacy policy."""
    print("Speed tests use Ookla's Speedtest tool. Before it can run, you must accept:")
    for link in OOKLA_TERMS:
        print(f"  {link}")
    answer = ask("Type yes to accept Ookla's terms: ").strip().lower()
    if answer != "yes":
        print("Not accepted. No speed tests will run until you accept.")
        return 1
    settings = config.load_settings()
    settings["ookla_terms_accepted"] = True
    config.save_settings(settings)
    print("Accepted. Saved in config.json.")
    return 0


def record_home():
    interface = collect.get_wifi_interface()
    router_ip = collect.get_router_ip(interface)
    if router_ip is None:
        print("Wi-Fi is not connected. Connect to your home Wi-Fi and try again.")
        return 1
    router_mac = collect.get_router_mac(router_ip)
    if router_mac is None:
        print(f"Couldn't read the router's hardware address at {router_ip}.")
        return 1
    settings = config.load_settings()
    if router_mac in settings["home_router_macs"]:
        print(f"Home router {router_mac} is already recorded.")
        return 0
    settings["home_router_macs"].append(router_mac)
    config.save_settings(settings)
    print(f"Recorded home router {router_mac} ({router_ip}).")
    return 0


def install():
    settings = config.load_settings()
    if config.uses_ookla(settings) and not settings["ookla_terms_accepted"]:
        print("Accept Ookla's terms first (accept-ookla), or set speed_test_tool to \"apple\".")
        return 1
    if not settings["home_router_macs"]:
        print("Record your home router first (record-home).")
        return 1
    if config.uses_ookla(settings) and config.find_speedtest(settings) is None:
        print("Ookla's speedtest tool isn't installed. See the README for the Homebrew steps, "
              "or set speed_test_tool to \"apple\".")
        return 1
    (config.PROJECT_DIR / "data").mkdir(exist_ok=True)
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    for job in (build_test_job(), build_graph_job(), build_outage_job()):
        path = _plist_path(job["Label"])
        _launchctl("bootout", f"{domain}/{job['Label']}")
        with open(path, "wb") as handle:
            plistlib.dump(job, handle)
        result = _launchctl("bootstrap", domain, str(path))
        if result.returncode != 0:
            print(f"Couldn't start {job['Label']}: {result.stderr.strip()}")
            return 1
        print(f"Scheduled {job['Label']}")
    print(budget.describe_estimate(settings, logfile.read_rows(config.LOG_FILE)))
    return 0


def remove():
    domain = f"gui/{os.getuid()}"
    for label in ALL_LABELS:
        _launchctl("bootout", f"{domain}/{label}")
        path = _plist_path(label)
        if path.exists():
            path.unlink()
        print(f"Stopped {label}")
    return 0


def status():
    settings = config.load_settings()
    tools = {"ookla_then_apple": "Ookla, with Apple's tester when Ookla refuses",
             "ookla": "Ookla only", "apple": "Apple's built-in tester only"}
    print(f"Speed test tool: {tools[settings['speed_test_tool']]}")
    if config.uses_ookla(settings):
        print(f"Ookla terms accepted: {'yes' if settings['ookla_terms_accepted'] else 'no'}")
    print(f"Home routers: {', '.join(settings['home_router_macs']) or 'none recorded'}")
    print(f"Tests: every {settings['minutes_between_tests']} minutes, "
          f"{settings['first_hour']}:00 through {settings['last_hour']}:59")
    print(budget.describe_estimate(settings, logfile.read_rows(config.LOG_FILE)))
    limits = []
    if settings["daily_data_limit_gb"] is not None:
        limits.append(f"at most {settings['daily_data_limit_gb']:g} GB a day")
    if settings["pause_at_percent_of_cap"] is not None:
        limits.append(f"pause at {settings['pause_at_percent_of_cap']:g}% of the cap")
    print(f"Data limits: {', '.join(limits) if limits else 'none'}")
    domain = f"gui/{os.getuid()}"
    for label in ALL_LABELS:
        running = _launchctl("print", f"{domain}/{label}").returncode == 0
        print(f"{label}: {'scheduled' if running else 'not scheduled'}")
    return 0


COMMANDS = {"accept-ookla": accept_ookla, "record-home": record_home, "install": install, "remove": remove, "status": status}


def main(argv):
    if len(argv) != 2 or argv[1] not in COMMANDS:
        print(__doc__)
        return 1
    return COMMANDS[argv[1]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv))

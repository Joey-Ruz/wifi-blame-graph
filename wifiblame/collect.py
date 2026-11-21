"""Gathers network facts from macOS tools and the Ookla Speedtest.

Each fact has a pure parse_* function (tested with saved tool output) and a
thin get_* function that runs the tool. Any tool failure returns None so one
missing fact never stops a test from being logged.
"""

import json
import re
from pathlib import Path
import subprocess
import time

from wifiblame import config

VPN_INTERFACE_PREFIXES = ("utun", "ipsec", "ppp", "tun", "tap")


def _run(command, timeout=30):
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


# ---------- Which interface is Wi-Fi ----------

def parse_wifi_interface(hardware_ports_text):
    lines = hardware_ports_text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "Hardware Port: Wi-Fi" and index + 1 < len(lines):
            match = re.match(r"\s*Device:\s*(\S+)", lines[index + 1])
            if match:
                return match.group(1)
    return None


def get_wifi_interface():
    text = _run(["/usr/sbin/networksetup", "-listallhardwareports"])
    if text is None:
        return "en0"
    return parse_wifi_interface(text) or "en0"


# ---------- Home router identity ----------

def get_router_ip(interface):
    """The router handed out on the Wi-Fi interface, even when a VPN is on."""
    text = _run(["/usr/sbin/ipconfig", "getoption", interface, "router"])
    if text is None:
        return None
    text = text.strip()
    return text or None


def normalize_mac(mac):
    """macOS arp drops leading zeros (0:1:a:...); store as 00:01:0a:..."""
    parts = mac.strip().lower().split(":")
    if len(parts) != 6:
        return None
    padded = []
    for part in parts:
        if not re.fullmatch(r"[0-9a-f]{1,2}", part):
            return None
        padded.append(part.zfill(2))
    return ":".join(padded)


def parse_arp_output(text):
    match = re.search(r" at (\S+) on ", text)
    if not match:
        return None
    return normalize_mac(match.group(1))


def get_router_mac(router_ip):
    text = _run(["/usr/sbin/arp", "-n", router_ip])
    if text is None:
        return None
    return parse_arp_output(text)


def is_home(router_mac, home_router_macs):
    if router_mac is None:
        return False
    known = set()
    for mac in home_router_macs:
        normalized = normalize_mac(mac)
        if normalized:
            known.add(normalized)
    return router_mac in known


# ---------- Delay to the router ----------

def parse_ping_average(text):
    match = re.search(r"= [\d.]+/([\d.]+)/", text)
    if not match:
        return None
    return float(match.group(1))


def ping_once(host, timeout_seconds=2):
    """One ping; True if it was answered."""
    try:
        result = subprocess.run(
            ["/sbin/ping", "-q", "-c", "1", "-t", str(timeout_seconds), host],
            capture_output=True, text=True, timeout=timeout_seconds + 3,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def get_router_ping_ms(router_ip):
    text = _run(
        ["/sbin/ping", "-q", "-c", str(config.ROUTER_PING_COUNT), "-i", "0.2", router_ip],
        timeout=20,
    )
    if text is None:
        return None
    return parse_ping_average(text)


# ---------- Wi-Fi signal ----------

def parse_airport_json(data, interface):
    """Signal, noise, band and channel from system_profiler's Wi-Fi report."""
    empty = {"signal_dbm": None, "noise_dbm": None, "band": None, "channel": None}
    try:
        interfaces = data["SPAirPortDataType"][0]["spairport_airport_interfaces"]
    except (KeyError, IndexError, TypeError):
        return empty

    for entry in interfaces:
        if entry.get("_name") != interface:
            continue
        current = entry.get("spairport_current_network_information")
        if not current:
            return empty

        result = dict(empty)
        signal_noise = current.get("spairport_signal_noise", "")
        match = re.match(r"\s*(-?\d+) dBm / (-?\d+) dBm", signal_noise)
        if match:
            result["signal_dbm"] = float(match.group(1))
            result["noise_dbm"] = float(match.group(2))

        channel_text = current.get("spairport_network_channel", "")
        match = re.match(r"\s*(\d+)\s*\((\d+(?:\.\d+)?GHz)", channel_text)
        if match:
            result["channel"] = match.group(1)
            result["band"] = match.group(2).replace("GHz", " GHz")
        return result
    return empty


def get_wifi_details(interface):
    text = _run(["/usr/sbin/system_profiler", "SPAirPortDataType", "-json"], timeout=60)
    if text is None:
        return parse_airport_json({}, interface)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return parse_airport_json({}, interface)
    return parse_airport_json(data, interface)


# ---------- Power and VPN ----------

def parse_pmset_on_power(text):
    if "'AC Power'" in text:
        return True
    if "'Battery Power'" in text:
        return False
    return None


def parse_pmset_low_power(text):
    """macOS names the setting "lowpowermode" on some power sources and
    "powermode" on others (0 automatic, 1 Low Power, 2 High Power)."""
    for line in text.splitlines():
        columns = line.split()
        if len(columns) >= 2 and columns[0] in ("lowpowermode", "powermode"):
            return columns[1] == "1"
    return None


def get_low_power_mode():
    text = _run(["/usr/bin/pmset", "-g"])
    if text is None:
        return None
    return parse_pmset_low_power(text)


def parse_fully_awake(text):
    """During the brief wakes macOS makes while the lid is closed ("dark wake"),
    the system runs without graphics. Graphics present means fully awake."""
    for line in text.splitlines():
        if "Current System Capabilities are:" in line:
            capabilities = line.split(":", 1)[1].split()
            return "Graphics" in capabilities
    return None


def get_fully_awake():
    text = _run(["/usr/bin/pmset", "-g", "systemstate"])
    if text is None:
        return None
    return parse_fully_awake(text)


def get_on_power():
    text = _run(["/usr/bin/pmset", "-g", "batt"])
    if text is None:
        return None
    return parse_pmset_on_power(text)


def parse_default_route_interface(text):
    match = re.search(r"interface:\s*(\S+)", text)
    if not match:
        return None
    return match.group(1)


def is_vpn_interface(interface_name):
    if interface_name is None:
        return False
    return interface_name.startswith(VPN_INTERFACE_PREFIXES)


def get_default_route_interface():
    text = _run(["/sbin/route", "-n", "get", "default"])
    if text is None:
        return None
    return parse_default_route_interface(text)


# ---------- Background traffic on this Mac ----------

def parse_netstat_received_bytes(text, interface):
    for line in text.splitlines():
        columns = line.split()
        if len(columns) < 7:
            continue
        if columns[0] == interface and columns[2].startswith("<Link#"):
            try:
                return int(columns[6])
            except ValueError:
                return None
    return None


def _received_bytes(interface):
    text = _run(["/usr/sbin/netstat", "-ib", "-I", interface])
    if text is None:
        return None
    return parse_netstat_received_bytes(text, interface)


def get_background_mbps(interface, seconds=None):
    """Download rate on this Mac over a short window just before the test."""
    if seconds is None:
        seconds = config.BACKGROUND_SAMPLE_SECONDS
    before = _received_bytes(interface)
    started = time.monotonic()
    time.sleep(seconds)
    after = _received_bytes(interface)
    elapsed = time.monotonic() - started
    if before is None or after is None or elapsed <= 0:
        return None
    return (after - before) * 8 / elapsed / 1_000_000


# ---------- Ookla Speedtest ----------

def _mbps(bytes_per_second):
    if bytes_per_second is None:
        return None
    return bytes_per_second * 8 / 1_000_000


def parse_speedtest_json(data):
    """Turn Ookla's JSON result into log fields."""
    ping = data.get("ping", {})
    download = data.get("download", {})
    upload = data.get("upload", {})
    server = data.get("server", {})
    interface = data.get("interface", {})
    result = data.get("result", {})

    location_parts = []
    for key in ("location", "country"):
        if server.get(key):
            location_parts.append(server[key])

    data_used_mb = None
    if download.get("bytes") is not None or upload.get("bytes") is not None:
        total_bytes = (download.get("bytes") or 0) + (upload.get("bytes") or 0)
        data_used_mb = total_bytes / 1_000_000

    return {
        "download_mbps": _mbps(download.get("bandwidth")),
        "upload_mbps": _mbps(upload.get("bandwidth")),
        "ping_ms": ping.get("latency"),
        "jitter_ms": ping.get("jitter"),
        "packet_loss_pct": data.get("packetLoss"),
        "download_loaded_ms": download.get("latency", {}).get("iqm"),
        "upload_loaded_ms": upload.get("latency", {}).get("iqm"),
        "server_name": server.get("name"),
        "server_location": ", ".join(location_parts) or None,
        "isp": data.get("isp"),
        "public_ip": interface.get("externalIp"),
        "result_url": result.get("url"),
        "data_used_mb": data_used_mb,
        "tool": "ookla",
        "ookla_says_vpn": interface.get("isVpn"),
    }


class SpeedtestError(Exception):
    pass


class SpeedtestRateLimited(SpeedtestError):
    """Ookla refused the test because too many were run; not an internet problem."""


def is_rate_limit_message(text):
    return "too many requests" in text.lower()


def is_blocked(server, blocked_servers):
    """A server is blocked when its id or name (any capitalization) is listed."""
    name = (server.get("name") or "").strip().lower()
    for entry in blocked_servers:
        if isinstance(entry, int) and not isinstance(entry, bool):
            if server.get("id") == entry:
                return True
        elif str(entry).strip().lower() == name:
            return True
    return False


def choose_server(servers, blocked_servers):
    """The nearest server (Ookla lists nearest first) that isn't blocked."""
    for server in servers:
        if not is_blocked(server, blocked_servers):
            return server
    return None


def parse_server_list(text):
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("type") == "serverList":
            return data.get("servers", [])
    return None


def list_servers(speedtest, cache_file=None, now=None):
    """Ookla's nearby servers, nearest first, or None if the list can't be read.

    The list is saved for a day so blocking servers doesn't add an extra Ookla
    request to every test (Ookla limits how often its tool may be used).
    """
    if cache_file is None:
        cache_file = config.SERVER_CACHE_FILE
    if now is None:
        now = time.time()
    cache_file = Path(cache_file)
    if cache_file.exists() and now - cache_file.stat().st_mtime < config.SERVER_CACHE_SECONDS:
        try:
            return json.loads(cache_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    command = [speedtest, "--servers", "--format=json", "--accept-license", "--accept-gdpr"]
    text = _run(command, timeout=30)
    if text is None:
        return None
    servers = parse_server_list(text)
    if servers:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(servers))
    return servers


APPLE_TESTER = "/usr/bin/networkQuality"
APPLE_MAX_SECONDS = 60


def _per_minute_to_ms(rounds_per_minute):
    """networkQuality reports responsiveness in round trips per minute (RPM);
    the delay of one round trip is 60,000 ms divided by that."""
    if not rounds_per_minute:
        return None
    return 60000 / rounds_per_minute


def parse_apple_json(data):
    """Turn networkQuality's JSON into log fields (fields it lacks stay empty)."""
    if data.get("dl_throughput") is None and data.get("ul_throughput") is None:
        raise SpeedtestError(str(data.get("error_code") or "Apple test returned no result"))
    data_used_mb = None
    if data.get("dl_bytes_transferred") is not None or data.get("ul_bytes_transferred") is not None:
        total = (data.get("dl_bytes_transferred") or 0) + (data.get("ul_bytes_transferred") or 0)
        data_used_mb = total / 1_000_000
    return {
        "download_mbps": _mbps(data.get("dl_throughput") / 8) if data.get("dl_throughput") else None,
        "upload_mbps": _mbps(data.get("ul_throughput") / 8) if data.get("ul_throughput") else None,
        "ping_ms": data.get("base_rtt"),
        "download_loaded_ms": _per_minute_to_ms(data.get("dl_responsiveness")),
        "upload_loaded_ms": _per_minute_to_ms(data.get("ul_responsiveness")),
        "server_name": "Apple",
        "server_location": data.get("test_endpoint"),
        "data_used_mb": data_used_mb,
        "tool": "apple",
    }


def run_apple_test(settings=None):
    """Run Apple's built-in tester once (download, then upload)."""
    command = [APPLE_TESTER, "-c", "-s", "-M", str(APPLE_MAX_SECONDS)]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True,
            timeout=APPLE_MAX_SECONDS + 60, stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise SpeedtestError("Apple test timed out")
    except OSError as error:
        raise SpeedtestError(f"Apple test could not start: {error}")
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        message = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise SpeedtestError((message[-1] if message else "Apple test returned no result")[:200])
    return parse_apple_json(data)


def run_speedtest(settings):
    """Run Ookla once. Raises SpeedtestError with a short reason on failure,
    or SpeedtestRateLimited when Ookla refuses because of its usage limit.

    The license flags are sent only because the user typed "yes" to Ookla's
    terms during setup (ookla_terms_accepted in config.json).
    """
    speedtest = config.find_speedtest(settings)
    if speedtest is None:
        raise SpeedtestError("Ookla speedtest not found; install it with Homebrew")
    if not settings.get("ookla_terms_accepted"):
        raise SpeedtestError("Ookla's terms not accepted yet; run setup accept-ookla")
    command = [speedtest, "--format=json", "--progress=no",
               "--accept-license", "--accept-gdpr"]

    blocked = settings.get("blocked_servers") or []
    if blocked:
        # If the list can't be read, fall back to Ookla's own choice.
        servers = list_servers(speedtest)
        if servers:
            server = choose_server(servers, blocked)
            if server is None:
                raise SpeedtestError("every nearby test server is on the block list")
            command.append(f"--server-id={server['id']}")

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=config.SPEEDTEST_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise SpeedtestError("speed test timed out")
    except OSError as error:
        raise SpeedtestError(f"speed test could not start: {error}")

    if completed.returncode != 0:
        output = (completed.stderr or "") + "\n" + (completed.stdout or "")
        lines = output.strip().splitlines()
        reason = lines[-1] if lines else f"exit code {completed.returncode}"
        if is_rate_limit_message(output):
            raise SpeedtestRateLimited(reason[:200])
        raise SpeedtestError(reason[:200])

    # Ookla prints one JSON object per line; the result line has type "result".
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if data.get("type") == "result":
            return parse_speedtest_json(data)
    output = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if is_rate_limit_message(output):
        raise SpeedtestRateLimited("Too many requests received (Ookla usage limit)")
    raise SpeedtestError("speed test returned no result")

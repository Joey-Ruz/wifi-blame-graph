from wifiblame import collect

HARDWARE_PORTS = """
Hardware Port: Thunderbolt Bridge
Device: bridge0
Ethernet Address: N/A

Hardware Port: Wi-Fi
Device: en0
Ethernet Address: aa:bb:cc:dd:ee:ff
"""

ARP_OUTPUT = "? (192.168.1.1) at 4:a1:b:c2:d3:e4 on en0 ifscope [ethernet]\n"

PING_OUTPUT = """PING 192.168.1.1 (192.168.1.1): 56 data bytes

--- 192.168.1.1 ping statistics ---
5 packets transmitted, 5 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 4.181/6.578/7.922/1.699 ms
"""

AIRPORT_JSON = {
    "SPAirPortDataType": [{
        "spairport_airport_interfaces": [
            {
                "_name": "en0",
                "spairport_current_network_information": {
                    "_name": "<redacted>",
                    "spairport_network_channel": "157 (5GHz, 80MHz)",
                    "spairport_signal_noise": "-40 dBm / -96 dBm",
                },
            },
            {"_name": "awdl0"},
        ]
    }]
}

NETSTAT_OUTPUT = """Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
en0        1500  <Link#14>   02:00:00:00:00:01 122837263     0 137320731614 34181338     0 16299074344     0
en0        1500  my-mac      fe80:e::1c8a:dd96 122837263     - 137320731614 34181338     - 16299074344     -
"""

OOKLA_RESULT = {
    "type": "result",
    "ping": {"jitter": 1.5, "latency": 12.25, "low": 11.0, "high": 14.0},
    "download": {"bandwidth": 50_000_000, "bytes": 600_000_000, "elapsed": 12000,
                 "latency": {"iqm": 45.5, "low": 12.0, "high": 120.0, "jitter": 8.0}},
    "upload": {"bandwidth": 5_000_000, "bytes": 60_000_000, "elapsed": 12000,
               "latency": {"iqm": 80.0, "low": 12.0, "high": 200.0, "jitter": 20.0}},
    "packetLoss": 0.5,
    "isp": "Example Cable",
    "interface": {"internalIp": "10.0.0.5", "name": "en0", "isVpn": False,
                  "externalIp": "203.0.113.9"},
    "server": {"id": 1, "name": "Example Fiber", "location": "Springfield, IL",
               "country": "United States"},
    "result": {"id": "abc", "url": "https://www.speedtest.net/result/c/abc"},
}


def test_wifi_interface_found():
    assert collect.parse_wifi_interface(HARDWARE_PORTS) == "en0"


def test_wifi_interface_missing():
    assert collect.parse_wifi_interface("Hardware Port: Ethernet\nDevice: en5\n") is None


def test_arp_mac_is_padded_to_two_digits():
    assert collect.parse_arp_output(ARP_OUTPUT) == "04:a1:0b:c2:d3:e4"


def test_arp_incomplete_entry():
    assert collect.parse_arp_output("? (192.168.1.1) at (incomplete) on en0\n") is None


def test_home_check_matches_regardless_of_format():
    assert collect.is_home("04:a1:0b:c2:d3:e4", ["4:A1:B:C2:D3:E4"])


def test_home_check_rejects_other_router():
    assert not collect.is_home("04:a1:0b:c2:d3:e4", ["11:22:33:44:55:66"])


def test_home_check_rejects_unknown_router():
    assert not collect.is_home(None, ["04:a1:0b:c2:d3:e4"])


def test_ping_average():
    assert collect.parse_ping_average(PING_OUTPUT) == 6.578


def test_ping_no_reply():
    assert collect.parse_ping_average("5 packets transmitted, 0 packets received") is None


def test_airport_signal_band_channel():
    details = collect.parse_airport_json(AIRPORT_JSON, "en0")
    assert details == {"signal_dbm": -40.0, "noise_dbm": -96.0, "band": "5 GHz",
                       "channel": "157"}


def test_airport_not_connected():
    details = collect.parse_airport_json(AIRPORT_JSON, "awdl0")
    assert details["signal_dbm"] is None and details["band"] is None


def test_power_states():
    assert collect.parse_pmset_on_power("Now drawing from 'AC Power'\n") is True
    assert collect.parse_pmset_on_power("Now drawing from 'Battery Power'\n") is False
    assert collect.parse_pmset_on_power("") is None


def test_vpn_by_route_interface():
    assert collect.is_vpn_interface("utun4")
    assert not collect.is_vpn_interface("en0")
    assert not collect.is_vpn_interface(None)
    assert collect.parse_default_route_interface("  interface: utun4\n") == "utun4"


def test_netstat_received_bytes():
    assert collect.parse_netstat_received_bytes(NETSTAT_OUTPUT, "en0") == 137320731614


def test_speedtest_result_fields():
    fields = collect.parse_speedtest_json(OOKLA_RESULT)
    assert fields["download_mbps"] == 400.0
    assert fields["upload_mbps"] == 40.0
    assert fields["ping_ms"] == 12.25
    assert fields["jitter_ms"] == 1.5
    assert fields["packet_loss_pct"] == 0.5
    assert fields["download_loaded_ms"] == 45.5
    assert fields["upload_loaded_ms"] == 80.0
    assert fields["server_name"] == "Example Fiber"
    assert fields["server_location"] == "Springfield, IL, United States"
    assert fields["isp"] == "Example Cable"
    assert fields["public_ip"] == "203.0.113.9"
    assert fields["result_url"] == "https://www.speedtest.net/result/c/abc"
    assert fields["ookla_says_vpn"] is False


def test_speedtest_without_packet_loss():
    data = dict(OOKLA_RESULT)
    del data["packetLoss"]
    assert collect.parse_speedtest_json(data)["packet_loss_pct"] is None


def test_low_power_mode_reading():
    assert collect.parse_pmset_low_power(" lowpowermode         1\n") is True
    assert collect.parse_pmset_low_power(" lowpowermode         0\n") is False
    assert collect.parse_pmset_low_power(" sleep 1\n") is None


def test_fully_awake_reading():
    awake = "Current System Capabilities are: CPU Graphics Audio Network \nCurrent Power State: 4\n"
    dark_wake = "Current System Capabilities are: CPU Network \nCurrent Power State: 4\n"
    assert collect.parse_fully_awake(awake) is True
    assert collect.parse_fully_awake(dark_wake) is False
    assert collect.parse_fully_awake("") is None


def test_low_power_mode_reading_on_charger():
    charger = " hibernatemode        3\n powermode            1\n"
    assert collect.parse_pmset_low_power(charger) is True
    assert collect.parse_pmset_low_power(" powermode            0\n") is False
    assert collect.parse_pmset_low_power(" powermode            2\n") is False


def test_speedtest_data_used_is_download_plus_upload():
    assert collect.parse_speedtest_json(OOKLA_RESULT)["data_used_mb"] == 660.0


RATE_LIMIT_OUTPUT = """[2026-11-21 15:28:26.533] [error] Configuration - client error (UnknownException)
[2026-11-21 15:28:26.534] [error] Limit reached:

Too many requests received. You may run the Speedtest CLI on a single computer for personal, non-commercial use only.
"""

SERVERS = [
    {"id": 1, "name": "Surf Internet", "location": "Springfield, IL"},
    {"id": 2, "name": "Sharktech Inc.", "location": "Springfield, IL"},
    {"id": 3, "name": "Example Fiber", "location": "Springfield, IL"},
]


class Finished:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def accepted_settings(**changes):
    from wifiblame import config
    settings = dict(config.DEFAULT_SETTINGS, speedtest_path="/custom/speedtest",
                    ookla_terms_accepted=True)
    settings.update(changes)
    return settings


def test_rate_limit_message_detected():
    assert collect.is_rate_limit_message(RATE_LIMIT_OUTPUT)
    assert not collect.is_rate_limit_message("Cannot read: ")


def test_ookla_refusal_raises_rate_limited(monkeypatch):
    monkeypatch.setattr(collect.subprocess, "run",
                        lambda command, **kwargs: Finished(1, stderr=RATE_LIMIT_OUTPUT))
    import pytest
    with pytest.raises(collect.SpeedtestRateLimited):
        collect.run_speedtest(accepted_settings())


def test_quiet_refusal_with_success_code_is_rate_limited(monkeypatch):
    monkeypatch.setattr(collect.subprocess, "run",
                        lambda command, **kwargs: Finished(0, stdout=RATE_LIMIT_OUTPUT))
    import pytest
    with pytest.raises(collect.SpeedtestRateLimited):
        collect.run_speedtest(accepted_settings())


def test_other_failures_are_not_rate_limited(monkeypatch):
    monkeypatch.setattr(collect.subprocess, "run",
                        lambda command, **kwargs: Finished(1, stderr="Cannot read: "))
    import pytest
    with pytest.raises(collect.SpeedtestError) as caught:
        collect.run_speedtest(accepted_settings())
    assert not isinstance(caught.value, collect.SpeedtestRateLimited)


def test_blocked_by_name_or_id():
    assert collect.is_blocked(SERVERS[0], ["surf internet"])
    assert collect.is_blocked(SERVERS[1], [2])
    assert not collect.is_blocked(SERVERS[2], ["Surf Internet", 2])


def test_nearest_unblocked_server_chosen():
    assert collect.choose_server(SERVERS, ["Surf Internet", "Sharktech Inc."])["id"] == 3
    assert collect.choose_server(SERVERS, []) ["id"] == 1
    assert collect.choose_server(SERVERS, [1, 2, 3]) is None


def test_blocked_list_pins_the_test_to_a_good_server(monkeypatch):
    seen = {}
    monkeypatch.setattr(collect, "list_servers", lambda speedtest: SERVERS)

    def fake_run(command, **kwargs):
        seen["command"] = command
        return Finished(0, stdout='{"type": "result", "server": {"name": "Example Fiber"}}\n')
    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    result = collect.run_speedtest(accepted_settings(blocked_servers=["Surf Internet", "Sharktech Inc."]))
    assert "--server-id=3" in seen["command"]
    assert result["server_name"] == "Example Fiber"


def test_unreadable_server_list_falls_back_to_ookla_choice(monkeypatch):
    seen = {}
    monkeypatch.setattr(collect, "list_servers", lambda speedtest: None)

    def fake_run(command, **kwargs):
        seen["command"] = command
        return Finished(0, stdout='{"type": "result"}\n')
    monkeypatch.setattr(collect.subprocess, "run", fake_run)
    collect.run_speedtest(accepted_settings(blocked_servers=["Surf Internet"]))
    assert not any(part.startswith("--server-id") for part in seen["command"])


def test_server_list_is_cached_for_a_day(tmp_path, monkeypatch):
    import json
    cache = tmp_path / "servers.json"
    calls = []

    def fake_run_tool(command, timeout=30):
        calls.append(command)
        return json.dumps({"type": "serverList", "servers": SERVERS}) + "\n"
    monkeypatch.setattr(collect, "_run", fake_run_tool)
    first = collect.list_servers("/custom/speedtest", cache_file=cache)
    again = collect.list_servers("/custom/speedtest", cache_file=cache)
    assert first == again == SERVERS
    assert len(calls) == 1
    stale = cache.stat().st_mtime + 25 * 60 * 60
    collect.list_servers("/custom/speedtest", cache_file=cache, now=stale)
    assert len(calls) == 2


def test_refused_server_list_is_not_cached(tmp_path, monkeypatch):
    cache = tmp_path / "servers.json"
    monkeypatch.setattr(collect, "_run", lambda command, timeout=30: RATE_LIMIT_OUTPUT)
    assert collect.list_servers("/custom/speedtest", cache_file=cache) is None
    assert not cache.exists()


APPLE_JSON = {
    "base_rtt": 38.02, "dl_throughput": 586567296, "ul_throughput": 111050112,
    "dl_responsiveness": 600.0, "ul_responsiveness": 240.0,
    "dl_bytes_transferred": 860208829, "ul_bytes_transferred": 214433792,
    "interface_name": "en0", "test_endpoint": "example-edge.aaplimg.com",
}


def test_apple_result_fields():
    fields = collect.parse_apple_json(APPLE_JSON)
    assert round(fields["download_mbps"], 1) == 586.6
    assert round(fields["upload_mbps"], 1) == 111.1
    assert fields["ping_ms"] == 38.02
    assert fields["download_loaded_ms"] == 100.0
    assert fields["upload_loaded_ms"] == 250.0
    assert round(fields["data_used_mb"], 1) == 1074.6
    assert fields["server_name"] == "Apple"
    assert fields["tool"] == "apple"


def test_apple_result_without_speeds_is_an_error():
    import pytest
    with pytest.raises(collect.SpeedtestError):
        collect.parse_apple_json({"error_code": -1009})


def test_apple_tester_unreadable_output(monkeypatch):
    import pytest
    monkeypatch.setattr(collect.subprocess, "run",
                        lambda command, **kwargs: Finished(1, stderr="No network"))
    with pytest.raises(collect.SpeedtestError, match="No network"):
        collect.run_apple_test()


def test_ookla_results_say_ookla():
    assert collect.parse_speedtest_json(OOKLA_RESULT)["tool"] == "ookla"

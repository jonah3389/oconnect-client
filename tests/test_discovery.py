"""UDP应答使用本机合成手机模拟，默认测试不连接真实手机。"""

import socket
import threading
from unittest.mock import Mock

from oconnect_client import discovery
from oconnect_client.discovery import Candidate, LanInterface, discover, parse_announce

PEER = "010203040506"
ANNOUNCE = (
    b"ANNOUNCE * HTTP/1.1\r\nNS: scp.device\r\nDT: 8\r\n"
    b"PDID: 010203040506\r\nDSP: 10151\r\nCSP: 10152\r\n"
    b"VIRTUAL_IP: 203.0.113.99\r\n\r\n"
)


def test_announce_uses_source_ip_and_rejects_unrelated_or_malformed_packets():
    iface = LanInterface("lan", "192.0.2.5", 24)
    assert parse_announce(ANNOUNCE, "192.0.2.99", iface) == Candidate(PEER, "192.0.2.99", 10152)
    for data, source in [
        (ANNOUNCE, "203.0.113.99"),
        (ANNOUNCE, "192.0.2.255"),
        (ANNOUNCE, "192.0.2.5"),
        (ANNOUNCE.replace(b"DT: 8", b"DT: 11"), "192.0.2.99"),
        (ANNOUNCE.replace(b"PDID: 010203040506", b"PDID: x"), "192.0.2.99"),
        (ANNOUNCE.replace(b"CSP: 10152", b"CSP: 70000"), "192.0.2.99"),
        (ANNOUNCE.replace(b"NS: scp.device", b"NS: log.broadcast"), "192.0.2.99"),
        (ANNOUNCE + b"PDID: AABBCCDDEEFF\r\n", "192.0.2.99"),
        (b"\xff" * 4097, "192.0.2.99"),
    ]:
        assert parse_announce(data, source, iface) is None


def test_subnet_larger_than_limit_sends_nothing(monkeypatch):
    factory = Mock()
    monkeypatch.setattr(discovery.socket, "socket", factory)
    assert (
        discover(
            {"device_id": PEER},
            PEER,
            [LanInterface("lan", "192.0.2.5", 16)],
            mode="subnet",
            timeout=0.01,
        )
        == []
    )
    factory.assert_not_called()


def test_adapter_selection_excludes_tunnels_and_limits_explicit_interface(monkeypatch):
    from types import SimpleNamespace as NS

    adapters = [
        NS(name=name, nice_name=name, ips=[NS(ip="192.0.2.5", network_prefix=24)])
        for name in ["en0", "utun0", "docker0", "awdl0"]
    ]
    monkeypatch.setattr(discovery.ifaddr, "get_adapters", lambda: adapters)
    assert discovery.lan_interfaces() == [LanInterface("en0", "192.0.2.5", 24)]
    assert discovery.lan_interfaces("en0") == [LanInterface("en0", "192.0.2.5", 24)]


def test_query_receives_unicast_on_advertised_ephemeral_port_and_filters_other_ids(monkeypatch):
    class LoopbackInterface(LanInterface):
        # Mac默认只有127.0.0.1，双方共用loopback；来源过滤另有独立测试。
        def contains(self, host):
            return host == "127.0.0.1"

    real_socket = socket.socket
    server = real_socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(2)
    requests, errors = [], []

    def phone():
        try:
            for _ in range(3):
                data, sender = server.recvfrom(4096)
                requests.append(data)
                port = int(
                    next(line[5:] for line in data.split(b"\r\n") if line.startswith(b"DSP: "))
                )
                server.sendto(ANNOUNCE.replace(b"010203040506", b"AABBCCDDEEFF"), (sender[0], port))
                server.sendto(ANNOUNCE, (sender[0], port))
        except Exception as exc:
            errors.append(exc)

    class Sender:
        def __init__(self, *args):
            self.socket = real_socket(*args)

        def __getattr__(self, name):
            return getattr(self.socket, name)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.socket.close()

        def sendto(self, data, target):
            assert target == ("239.255.255.250", 10150)
            return self.socket.sendto(data, server.getsockname())

    thread = threading.Thread(target=phone)
    thread.start()
    monkeypatch.setattr(discovery.socket, "socket", Sender)
    try:
        result = discover(
            {"device_id": "111213141516"},
            PEER,
            [LoopbackInterface("loopback-test", "127.0.0.1", 30)],
            timeout=0.1,
        )
    finally:
        thread.join(timeout=3)
        server.close()
    assert not errors
    assert len(requests) == 3
    assert requests[0].startswith(b"QUERY * HTTP/1.1\r\n")
    assert b"AD: \r\n" in requests[0]
    assert result == [Candidate(PEER, "127.0.0.1", 10152)]

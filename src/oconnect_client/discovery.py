"""原生 QUERY/ANNOUNCE 地址发现；发现结果仅是候选，授权由文件会话验证。"""

import ipaddress
import re
import select
import socket
import time
from contextlib import ExitStack
from dataclasses import dataclass

import ifaddr

GROUP = "239.255.255.250"
DISCOVERY_PORT = 10150
MAX_SUBNET_ADDRESSES = 256
VIRTUAL_PREFIXES = (
    "lo",
    "utun",
    "tun",
    "tap",
    "wg",
    "tailscale",
    "docker",
    "veth",
    "virbr",
    "br-",
    "bridge",
    "vmnet",
    "vbox",
    "awdl",
    "llw",
    "vethernet",
)


@dataclass(frozen=True)
class LanInterface:
    name: str
    address: str
    prefix: int

    @property
    def network(self):
        return ipaddress.IPv4Network((self.address, self.prefix), strict=False)

    def contains(self, host):
        try:
            address = ipaddress.IPv4Address(host)
        except (ValueError, TypeError):
            return False
        return (
            address in self.network
            and str(address) != self.address
            and address not in (self.network.network_address, self.network.broadcast_address)
        )


@dataclass(frozen=True)
class Candidate:
    peer_id: str
    host: str
    scp_port: int


def lan_interfaces(name=None):
    interfaces = []
    for adapter in ifaddr.get_adapters():
        labels = (adapter.name, adapter.nice_name)
        if name and name not in labels:
            continue
        # 即使手动指定，也只在真实 LAN 接口进行自动发现。
        if any(label.lower().startswith(VIRTUAL_PREFIXES) for label in labels):
            continue
        for ip in adapter.ips:
            if not isinstance(ip.ip, str):
                continue
            address = ipaddress.IPv4Address(ip.ip)
            if address.is_loopback or address.is_link_local or address.is_unspecified:
                continue
            iface = LanInterface(adapter.name, str(address), ip.network_prefix)
            if iface not in interfaces:
                interfaces.append(iface)
    if name and not interfaces:
        raise ValueError("指定网卡没有可用于发现的 LAN IPv4 地址")
    return interfaces


def build_query(identity, reply_port):
    device_id = identity["device_id"].upper()
    if not re.fullmatch(r"[0-9A-F]{12}", device_id) or not 1 <= reply_port <= 65535:
        raise ValueError("发现请求的设备 ID 或回复端口无效")
    return (
        'QUERY * HTTP/1.1\r\nMAN: "sdp/1"\r\nNS: scp.device\r\n'
        f"DSP: {reply_port}\r\nDMS: 0\r\nPDID: {device_id}\r\n"
        "AD: \r\nDT: 11\r\nDN: oconnect-client\r\n\r\n"
    ).encode("ascii")


def parse_announce(data, sender, interface):
    if len(data) > 4096 or not interface.contains(sender):
        return None
    try:
        lines = data.decode("utf-8").split("\r\n")
        if lines[0] != "ANNOUNCE * HTTP/1.1":
            return None
        fields = {}
        for line in lines[1:]:
            if not line:
                continue
            key, sep, value = line.partition(":")
            key = key.strip().upper()
            if not sep or key in fields:
                return None
            fields[key] = value.strip()
        peer_id = fields.get("PDID", "").upper()
        port = int(fields.get("CSP", "0"))
        if (
            fields.get("NS") != "scp.device"
            or fields.get("DT") != "8"
            or not re.fullmatch(r"[0-9A-F]{12}", peer_id)
            or not 1 <= port <= 65535
        ):
            return None
        return Candidate(peer_id, str(ipaddress.IPv4Address(sender)), port)
    except (UnicodeError, ValueError):
        return None


def discover(identity, peer_id, interfaces, *, mode="multicast", timeout=3, report=None):
    """三轮组播；或每个小子网逐地址发一次原生查询。每轮最多返回16个候选。"""
    if mode not in ("multicast", "subnet"):
        raise ValueError("未知发现方式")
    found = {}
    with ExitStack() as stack:
        active = {}
        for interface in interfaces:
            sock = None
            if mode == "subnet" and interface.network.num_addresses > MAX_SUBNET_ADDRESSES:
                if report:
                    report(f"跳过 {interface.name} 子网查询：地址范围超过256个")
                continue
            try:
                sock = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
                sock.bind((interface.address, 0))
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface.address)
                )
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                sock.setblocking(False)
                active[sock] = interface
                message = build_query(identity, sock.getsockname()[1])
                if mode == "multicast":
                    for attempt in range(3):
                        sock.sendto(message, (GROUP, DISCOVERY_PORT))
                        if attempt < 2:
                            time.sleep(0.2)
                else:
                    for host in interface.network.hosts():
                        if str(host) != interface.address:
                            sock.sendto(message, (str(host), DISCOVERY_PORT))
                            time.sleep(0.01)
            except OSError as error:
                active.pop(sock, None)
                if report:
                    report(f"{interface.name} 发现请求失败：{type(error).__name__}")
        deadline = time.monotonic() + timeout
        while active and time.monotonic() < deadline:
            readable, _, _ = select.select(
                list(active), [], [], min(0.25, max(0, deadline - time.monotonic()))
            )
            for sock in readable:
                try:
                    data, (sender, _) = sock.recvfrom(4097)
                except BlockingIOError:
                    continue
                except OSError:
                    active.pop(sock, None)
                    continue
                candidate = parse_announce(data, sender, active[sock])
                if candidate and candidate.peer_id == peer_id:
                    found[candidate.host] = candidate
                    if len(found) >= 16:
                        return list(found.values())
    return list(found.values())

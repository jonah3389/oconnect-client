"""新设备首次配对时接收手机反向 OAFP 控制连接。"""

import hashlib
import hmac
import os
import socket
import struct
import threading
import time

from .identity import build_banner


def mac(key, data):
    return hmac.new(key, data, hashlib.sha512).digest()[:8]


def exact(conn, n):
    out = b""
    while len(out) < n:
        data = conn.recv(n - len(out))
        if not data:
            raise EOFError("手机关闭反向控制连接")
        out += data
    return out


def recv(conn):
    return exact(conn, int.from_bytes(exact(conn, 2), "big"))


def send(conn, payload):
    conn.sendall(struct.pack(">H", len(payload)) + payload)


class OafpControlServer:
    def __init__(self, peer_host, identity, port=46888):
        self.peer_host, self.identity, self.port = peer_host, identity, port
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.authenticated = threading.Event()
        self.observations = []
        self.connections = []
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("0.0.0.0", port))
        self.listener.listen(3)
        self.listener.settimeout(0.5)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def set_peer(self, device, ksc, alias):
        self.device, self.ksc, self.alias = device, ksc, alias
        self.ready.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                conn, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if address[0] != self.peer_host:
                conn.close()
                continue
            self.connections.append(conn)
            worker = threading.Thread(target=self.handle, args=(conn,), daemon=True)
            worker.start()

    def handle(self, conn):
        try:
            conn.settimeout(10)
            if not self.ready.wait(10):
                raise TimeoutError("等待 SCP 密钥超时")
            conn.sendall(build_banner(self.identity))
            banner = recv(conn)
            if b"OAFP_" not in banner:
                raise ValueError("手机反连缺少 banner")
            request = recv(conn)
            if len(request) != 38 or request[:1] != b"\x10":
                raise ValueError("手机反连缺少认证 Request")
            rc = request[2:10]
            ts = int.from_bytes(request[10:18], "big")
            if request[26:32] != bytes.fromhex(self.device) or request[32:38] != self.alias:
                raise ValueError("手机反连身份或 alias 不符")
            if not hmac.compare_digest(request[18:26], mac(self.ksc, rc + struct.pack("<Q", ts))):
                raise ValueError("手机反连 MAC 不符")
            rs = os.urandom(8)
            now = int(time.time())
            send(
                conn,
                b"\x11\x01\x00"
                + rs
                + struct.pack(">Q", now)
                + mac(self.ksc, rc + rs + struct.pack("<Q", now)),
            )
            confirm = recv(conn)
            expected = b"\x12\x02\x00" + mac(self.ksc, rs + struct.pack("<Q", ts))
            if not hmac.compare_digest(confirm, expected):
                raise ValueError("手机反连 Confirm 不符")
            self.active_connection = conn
            self.challenges = rc + rs
            self.authenticated.set()
            self.observations.append("reverse_auth_ok")
            print("REVERSE_OAFP_AUTH_OK", flush=True)
            return
        except Exception as error:
            self.observations.append(type(error).__name__ + ": " + str(error))
            print("REVERSE_STOP", type(error).__name__, str(error), flush=True)
        finally:
            if (
                not self.authenticated.is_set()
                or getattr(self, "active_connection", None) is not conn
            ):
                conn.close()

    def adopt_connection(self, timeout=20):
        if not self.authenticated.wait(timeout):
            raise TimeoutError("手机没有完成反向 OAFP 认证")
        connection = self.active_connection
        if connection in self.connections:
            self.connections.remove(connection)
        return connection, self.challenges, self.ksc

    def close(self):
        self.stop_event.set()
        self.listener.close()
        for conn in self.connections:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        self.thread.join(timeout=1)

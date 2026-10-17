"""独立身份文件会话；首次配对与日常连接分离。"""

import contextlib
import hmac
import json
import os
import socket
import struct
import time
import uuid
from pathlib import Path

from .identity import agent_public_key_payload, build_banner, build_service_registry
from .media import download_media
from .oafp import decrypt_message, mac, parse_agents, receive, send, u16
from .ukey2 import IndependentSecureSession, StoredIdentitySession

PROFILE = b"app:oshare.mac.agent;"


class ConnectClient:
    def __init__(self, host, port=10152, timeout=12, *, identity_path, pairing=False):
        self.host, self.port, self.timeout = (host, port, timeout)
        self.identity_path = Path(identity_path)
        self.pairing = pairing
        self.secure = self.conn = None
        self.stage = "初始化"
        self.local_agent = 64872
        self.channel = 102
        self.remote_agent = None
        self.file_ready = False

    def __enter__(self):
        try:
            self.open()
            return self
        except BaseException as error:
            stage = self.stage
            self.close()
            raise RuntimeError(f"{stage}失败：{type(error).__name__}: {error}") from error

    def __exit__(self, *args):
        self.close()

    def open(self):
        self.stage = "本机身份认证"
        if not self.pairing:
            self.secure = StoredIdentitySession(self.host, self.identity_path)
            self._open_file_channel()
            return
        self.secure = IndependentSecureSession(self.host, self.identity_path, self.port)
        self.secure.establish()
        self.stage = "手机首次配对确认"
        self.secure.send_enc(b"interact hello", fl=49)
        _, reply = self.secure.recv_dec(self.timeout)
        if reply != b"accept":
            raise RuntimeError("手机未接受配对交互，请检查首次配对确认状态")
        time.sleep(0.3)
        self.conn, self.challenges, self.file_ksc = self.secure.control_server.adopt_connection(
            self.timeout
        )
        self._discover_file_service()

    def _open_file_channel(self):
        self.stage = "OAFP 认证"
        self.conn = socket.create_connection((self.host, 46888), timeout=self.timeout)
        self.conn.settimeout(self.timeout)
        banner = receive(self.conn)
        if b"OAFP_" not in banner:
            raise ValueError("手机没有返回 OAFP banner")
        self.conn.sendall(build_banner(self.secure.identity))
        rc = os.urandom(8)
        timestamp = int(time.time())
        key = self.secure.ksc
        device_id = self.secure.key_id.removeprefix("0_")
        if len(device_id) != 12:
            raise ValueError("本机身份未包含预期的 6 字节设备 ID")
        request = (
            b"\x10\x02"
            + rc
            + struct.pack(">Q", timestamp)
            + mac(key, rc + struct.pack("<Q", timestamp))[:8]
            + bytes.fromhex(device_id)
            + self.secure.ksc_alias
        )
        send(self.conn, request)
        response = receive(self.conn)
        if len(response) != 27 or response[:1] != b"\x11" or response[2] != 0:
            raise RuntimeError(f"OAFP 认证失败，返回头 {response[:3].hex()}")
        rs, server_ts = (response[3:11], int.from_bytes(response[11:19], "big"))
        expected = mac(key, rc + rs + struct.pack("<Q", server_ts))[:8]
        if not hmac.compare_digest(expected, response[19:27]):
            raise ValueError("OAFP 服务端 MAC 校验失败")
        send(self.conn, b"\x12\x02\x00" + mac(key, rs + struct.pack("<Q", timestamp))[:8])
        self.challenges = rc + rs
        self.file_ksc = key
        self._discover_file_service()

    def _discover_file_service(self):
        self.stage = "文件服务发现"
        send(
            self.conn,
            bytes.fromhex("000801020000000002") + b"app:unite.oplus.onet;app:oshare.mac.agent;",
        )
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            payload = receive(self.conn)
            if payload.startswith(b"\x00\x08\x02"):
                found = parse_agents(payload)
                self.remote_agent = next(
                    (
                        a["agent_id"]
                        for a in found
                        if a["profile"] == PROFILE[:-1].decode() and a["role"] == 4
                    )
                )
                break
        if self.remote_agent is None:
            raise RuntimeError("未发现手机文件服务")
        self.stage = "文件通道协商"
        registry = build_service_registry()
        agent_key_payload = agent_public_key_payload(self.secure.identity)
        send(self.conn, registry)
        address = u16(self.remote_agent) + u16(self.local_agent) + PROFILE
        send(
            self.conn,
            b"\x00\x04\x01" + address + u16(1) + u16(self.channel) + u16(104) + b"\x00\x01\x00",
        )
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            payload = receive(self.conn)
            if payload.startswith(b"\x00\x04\x05"):
                send(self.conn, b"\x00\x04\x06" + address + agent_key_payload)
            elif payload.startswith(b"\x00\x04\x02"):
                if payload[-5:] != b"\x00\x00\x01" + u16(self.channel):
                    raise RuntimeError("手机拒绝文件通道")
                break
        else:
            raise TimeoutError("文件通道协商超时")
        self.stage = "文件 API 初始化"
        self.write_object({"api": "init.capability.sync", "body": {"fileMgrVersion": 2000}})
        self.call("file.root", {})
        self.secure.mark_paired()

    def read_object(self, timeout=None):
        self.conn.settimeout(timeout or self.timeout)
        while True:
            payload = receive(self.conn)
            if int.from_bytes(payload[:2], "big") == self.channel << 2:
                obj = decrypt_message(self.file_ksc, self.challenges, payload)
                if obj.get("api") == "init.connected":
                    self.file_ready = True
                return obj

    def write_object(self, obj):
        send(
            self.conn,
            u16(self.channel << 2)
            + b"\x00"
            + json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode(),
        )

    def call(self, api, body, timeout=20):
        session_id = uuid.uuid4().hex
        self.write_object(
            dict(
                api=api,
                header={"clientVersion": "1.0.0", "apilevel": "1"},
                body=body,
                sessionId=session_id,
            )
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            obj = self.read_object(max(0.1, deadline - time.monotonic()))
            if obj.get("sessionId") == session_id:
                if obj.get("statusCode", 0) != 0 or obj.get("result", {}).get("code", 200) != 200:
                    raise RuntimeError(f"{api} 返回失败：{obj.get('statusDesc')}")
                return obj
        raise TimeoutError(f"{api} 未返回响应")

    def list_watch(self):
        rows, page = ([], 1)
        while True:
            obj = self.call(
                "file.category.query",
                dict(categoryId=2, pageNo=page, pageSize=500, sortOrder="DEFAULT"),
            )
            data = obj["result"]["data"]
            batch = data["result"]
            rows.extend(batch)
            if len(rows) >= data["total"] or not batch:
                break
            page += 1
            if page > 100:
                raise ValueError("音频列表分页超过上限")
        return [r for r in rows if "/Music/Recordings/Watch Recordings/" in r.get("filePath", "")]

    def download(self, row, destination):
        return download_media(self, row, destination)

    def close(self):
        if self.conn:
            if self.remote_agent is not None:
                with contextlib.suppress(Exception):
                    send(
                        self.conn,
                        b"\x00\x04\x03" + u16(self.remote_agent) + u16(self.local_agent) + PROFILE,
                    )
                    self.conn.settimeout(2)
                    deadline = time.monotonic() + 2
                    while time.monotonic() < deadline:
                        if receive(self.conn).startswith(b"\x00\x04\x04"):
                            break
                    time.sleep(0.3)
            self.conn.close()
        if self.secure and hasattr(self.secure, "control_server"):
            self.secure.control_server.close()
        if self.secure and self.secure.s:
            self.secure.s.close()

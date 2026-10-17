"""OAFP 流帧、服务描述与加密响应解析。"""

import gzip
import hashlib
import hmac
import json
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def u16(n):
    return struct.pack(">H", n)


def mac(key, message):
    return hmac.new(key, message, hashlib.sha512).digest()


def exact(conn, size):
    result = bytearray()
    while len(result) < size:
        chunk = conn.recv(size - len(result))
        if not chunk:
            raise EOFError("对端关闭连接")
        result.extend(chunk)
    return bytes(result)


def receive(conn):
    length = int.from_bytes(exact(conn, 2), "big")
    if not length:
        raise ValueError("空 OAFP 帧")
    return exact(conn, length)


def send(conn, payload):
    conn.sendall(u16(len(payload)) + payload)


def parse_agents(payload):
    """CAPEX v3，字段顺序来自手机 ServiceExpFrameUtils。"""
    position = 10

    def number():
        nonlocal position
        if position + 2 > len(payload):
            raise ValueError("服务描述截断")
        value = int.from_bytes(payload[position : position + 2], "big")
        position += 2
        return value

    def text():
        nonlocal position
        end = payload.index(b";", position)
        value = payload[position:end].decode()
        position = end + 1
        return value

    result = []
    for _ in range(int.from_bytes(payload[8:10], "big")):
        container, _signature = text(), text()
        for _ in range(number()):
            agent_id, profile, version = number(), text(), number()
            role = payload[position]
            position += 1
            flags = number()
            result.append(
                dict(
                    container=container,
                    agent_id=agent_id,
                    profile=profile,
                    version=version,
                    role=role,
                    flags=flags,
                )
            )
    if position != len(payload):
        raise ValueError("服务描述存在未解析字节")
    return result


def decrypt_message(ksc, challenges, payload):
    if len(payload) < 3:
        raise ValueError("业务帧截断")
    if payload[2] == 4:
        if len(payload) < 27:
            raise ValueError("加密帧截断")
        material = mac(ksc, challenges + payload[3:11])
        clear = AESGCM(material[:16]).decrypt(material[16:28], payload[11:], material[28:48])
    elif payload[2] == 0:
        clear = payload[3:]
    else:
        raise ValueError("未知业务帧标志")
    if clear.startswith(b"\x1f\x8b"):
        clear = gzip.decompress(clear)
    return json.loads(clear)

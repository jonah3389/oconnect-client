"""SCP 最小传输与 protobuf 编解码；不读取任何官方客户端状态。"""

import socket
import struct


def varint(value):
    if value < 0:
        raise ValueError("不支持负整数")
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def pb(field, wire, value):
    tag = varint(field << 3 | wire)
    if wire == 0:
        return tag + varint(value)
    if wire == 2:
        return tag + varint(len(value)) + value
    raise ValueError("仅支持 varint 与 bytes 字段")


def read_varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("protobuf 整数截断")
        b = data[offset]
        offset += 1
        value |= (b & 127) << shift
        if not b & 128:
            return value, offset
    raise ValueError("protobuf 整数过长")


def fields(data):
    result = {}
    offset = 0
    while offset < len(data):
        tag, offset = read_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise ValueError("protobuf 字段编号无效")
        if wire == 0:
            value, offset = read_varint(data, offset)
        elif wire == 2:
            length, offset = read_varint(data, offset)
            if offset + length > len(data):
                raise ValueError("protobuf 字段截断")
            value = data[offset : offset + length]
            offset += length
        else:
            raise ValueError("不支持的 protobuf wire type")
        result.setdefault(number, []).append(value)
    return result


def one(data, field):
    values = fields(data).get(field, [])
    if len(values) != 1:
        raise ValueError(f"字段 {field} 缺失或重复")
    return values[0]


def frame(payload, fl=0x30, typ=0):
    return (
        bytes([0x40, fl])
        + struct.pack(">HHH", len(payload), typ, 0)
        + b"\x7b\x08\xad\x12"
        + payload
    )


def wrap(message_type, payload):
    return pb(1, 0, message_type) + pb(2, 2, payload)


class ScpSocket:
    def __init__(self, host, port=10152, timeout=10):
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.socket.settimeout(timeout)

    def exact(self, length):
        result = bytearray()
        while len(result) < length:
            chunk = self.socket.recv(length - len(result))
            if not chunk:
                raise EOFError("手机关闭 SCP 连接")
            result.extend(chunk)
        return bytes(result)

    def receive(self):
        head = self.exact(12)
        if head[0] != 0x40 or head[8:12] != b"\x7b\x08\xad\x12":
            raise ValueError("SCP 帧头版本不受支持")
        return head[1], self.exact(int.from_bytes(head[2:4], "big"))

    def send(self, payload, fl=0x30, typ=0):
        self.socket.sendall(frame(payload, fl, typ))

    def close(self):
        self.socket.close()

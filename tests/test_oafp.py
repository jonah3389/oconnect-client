"""协议边界与真实故障回归；不连接手机、不读取账号密钥。"""

import gzip
import json
import socket
import struct
import unittest

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from oconnect_client.oafp import decrypt_message, mac, parse_agents, receive, send


class ChunkedSocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def recv(self, size):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > size:
            self.chunks.insert(0, chunk[size:])
        return chunk[:size]


class ProtocolTests(unittest.TestCase):
    def test_tcp_fragmented_and_coalesced_frames(self):
        conn = ChunkedSocket([b"\x00", b"\x02\x14", b"\x02\x00\x03abc"])
        self.assertEqual(receive(conn), b"\x14\x02")
        self.assertEqual(receive(conn), b"abc")

    def test_tcp_truncation_is_failure(self):
        with self.assertRaises(EOFError):
            receive(ChunkedSocket([b"\x00\x03ab"]))

    def test_auth_message_has_big_endian_length(self):
        a, b = socket.socketpair()
        try:
            send(a, b"\x10\x02" + bytes(36))
            self.assertEqual(b.recv(100), b"\x00&\x10\x02" + bytes(36))
        finally:
            a.close()
            b.close()

    def test_encrypted_gzip_response_and_tampering(self):
        key, qcqs = (bytes(range(16)), bytes(range(16, 32)))
        counter = struct.pack(">Q", 1)
        material = mac(key, qcqs + counter)
        obj = {"api": "file.root", "statusCode": 0, "result": {"code": 200}}
        clear = gzip.compress(json.dumps(obj).encode())
        cipher = AESGCM(material[:16]).encrypt(material[16:28], clear, material[28:48])
        packet = b"\x01\x98\x04" + counter + cipher
        self.assertEqual(decrypt_message(key, qcqs, packet), obj)
        with self.assertRaises(InvalidTag):
            decrypt_message(key, qcqs, packet[:-1] + bytes([packet[-1] ^ 1]))

    def test_capex_dynamic_agent_id(self):
        packet = (
            bytes.fromhex("00080203000000000001")
            + b"com.coloros.oshare;;\x00\x01"
            + b"\x124app:oshare.mac.agent;\x01\x00\x04\x00\n"
        )
        parsed = parse_agents(packet)
        self.assertEqual(parsed[0]["agent_id"], 4660)
        self.assertEqual(parsed[0]["profile"], "app:oshare.mac.agent")
        with self.assertRaises(ValueError):
            parse_agents(packet + b"junk")


if __name__ == "__main__":
    unittest.main()

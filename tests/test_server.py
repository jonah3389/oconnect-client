import contextlib
import io
import socket
import struct
import tempfile
import unittest
from pathlib import Path

from oconnect_client.identity import create_identity
from oconnect_client.server import OafpControlServer, mac, recv, send


class ServerTests(unittest.TestCase):
    def test_reverse_auth_connection_is_handed_over_without_second_connection(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            identity = create_identity(Path(directory) / "identity.json")
            server = OafpControlServer("127.0.0.1", identity, port=0)
            host, port = server.listener.getsockname()
            key, alias, device = bytes(range(16)), b"ABCDEF", "010203040506"
            server.set_peer(device, key, alias)
            client = socket.create_connection(("127.0.0.1", port), timeout=2)
            adopted = None
            try:
                self.assertIn(b"OAFP_", recv(client))
                send(client, b"\x02\x00\x0aOAFP_TEST")
                rc, ts = b"abcdefgh", 123456
                request = (
                    b"\x10\x02"
                    + rc
                    + struct.pack(">Q", ts)
                    + mac(key, rc + struct.pack("<Q", ts))
                    + bytes.fromhex(device)
                    + alias
                )
                send(client, request)
                response = recv(client)
                rs, server_ts = response[3:11], int.from_bytes(response[11:19], "big")
                self.assertEqual(response[19:27], mac(key, rc + rs + struct.pack("<Q", server_ts)))
                send(client, b"\x12\x02\x00" + mac(key, rs + struct.pack("<Q", ts)))
                adopted, challenges, file_key = server.adopt_connection(2)
                self.assertEqual(challenges, rc + rs)
                self.assertEqual(file_key, key)
                send(client, b"next-message")
                self.assertEqual(recv(adopted), b"next-message")
                self.assertNotIn(adopted, server.connections)
            finally:
                client.close()
                if adopted:
                    adopted.close()
                server.close()


if __name__ == "__main__":
    unittest.main()

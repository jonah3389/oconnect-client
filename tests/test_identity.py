import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from oconnect_client.identity import build_banner, create_identity, load_identity, summary
from oconnect_client.scp import fields, one, pb, read_varint, varint, wrap
from oconnect_client.ukey2 import D2D_SALT, Ukey2Client, public_key_proto


class IdentityTests(unittest.TestCase):
    def test_new_devices_have_distinct_keys_and_do_not_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            a = Path(directory) / "a.json"
            b = Path(directory) / "b.json"
            first = create_identity(a)
            second = create_identity(b)
            self.assertNotEqual(first["device_id"], second["device_id"])
            self.assertNotEqual(first["public_key"], second["public_key"])
            self.assertEqual(load_identity(a), first)
            self.assertEqual(a.stat().st_mode & 0o777, 0o600)
            self.assertEqual(summary(first)["pairing_status"], "unpaired")
            self.assertNotIn("private_key", summary(first))
            with self.assertRaises(FileExistsError):
                create_identity(a)
            self.assertEqual(load_identity(a), first)

    def test_banner_has_unique_identity_and_does_not_claim_dynamic_channels(self):
        with tempfile.TemporaryDirectory() as directory:
            first = create_identity(Path(directory) / "a.json", name="独立电脑 A")
            second = create_identity(Path(directory) / "b.json", name="独立电脑 B")
            a, b = build_banner(first), build_banner(second)
            self.assertNotEqual(a[5:41], b[5:41])
            self.assertEqual(int.from_bytes(a[:2], "big"), len(a) - 2)
            self.assertIn(bytes.fromhex("0b020d000e"), a)
            self.assertNotIn(bytes.fromhex("0b020d010e"), a)

    def test_mismatched_public_key_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.json"
            identity = create_identity(path)
            identity["public_key"] = base64.b64encode(bytes(65)).decode()
            path.write_text(json.dumps(identity))
            with self.assertRaisesRegex(ValueError, "公私钥不匹配"):
                load_identity(path)


class WireTests(unittest.TestCase):
    def test_variable_length_and_duplicate_fields(self):
        data = pb(1, 0, 300) + pb(2, 2, b"x" * 256) + pb(1, 0, 301)
        self.assertEqual(fields(data)[1], [300, 301])
        self.assertEqual(len(one(data, 2)), 256)
        with self.assertRaises(ValueError):
            one(data, 1)
        for raw in (b"\x08\x80", b"\x12\x05ab", b"\x00", b"\x0d\x00"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                fields(raw)

    def test_varint_huge_and_negative_rejected(self):
        with self.assertRaises(ValueError):
            varint(-1)
        with self.assertRaises(ValueError):
            read_varint(b"\x80" * 11, 0)


class UkeyTests(unittest.TestCase):
    def test_client_finish_committed_and_modification_invalidates(self):
        handshake = Ukey2Client()
        self.assertEqual(one(handshake.client_init, 1), 2)
        init = one(handshake.client_init, 2)
        commitment = one(init, 3)
        self.assertEqual(one(commitment, 1), 100)
        self.assertEqual(one(commitment, 2), hashlib.sha512(handshake.finish).digest())
        self.assertNotEqual(one(commitment, 2), hashlib.sha512(handshake.finish + b"x").digest())
        self.assertEqual(one(one(handshake.finish, 2), 2), handshake.iv)

    def test_server_parsing_derivation_matches_standard_hkdf(self):
        handshake = Ukey2Client()
        server = ec.derive_private_key(7, ec.SECP256R1())
        raw = wrap(
            3,
            pb(1, 0, 1)
            + pb(2, 2, bytes(32))
            + pb(3, 0, 100)
            + pb(4, 2, public_key_proto(server.public_key())),
        )
        finish = handshake.process_server_init(raw)
        shared = hashlib.sha256(
            server.exchange(ec.ECDH(), handshake.ephemeral.public_key())
        ).digest()
        next_key = HKDF(hashes.SHA256(), 32, b"UKEY2 v1 next", handshake.client_init + raw).derive(
            shared
        )
        expected = HKDF(hashes.SHA256(), 32, D2D_SALT, b"client").derive(next_key)
        self.assertEqual(handshake.session_key, expected)
        self.assertEqual(handshake.nonce, handshake.iv[:12])
        self.assertEqual(finish, handshake.finish)
        with self.assertRaises(ValueError):
            handshake.process_server_init(wrap(4, b"x"))


if __name__ == "__main__":
    unittest.main()

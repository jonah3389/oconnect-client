import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oconnect_client.media import download_media, parse_content_range


class RangeTests(unittest.TestCase):
    def test_official_first_and_following_ranges(self):
        self.assertEqual(parse_content_range("bytes 0-1024/10315242", 0, 10315242), 1025)
        self.assertEqual(
            parse_content_range("bytes 1025-5243904/10315242", 1025, 10315242), 5242880
        )

    def test_wrong_start_total_and_bounds_rejected(self):
        for header in (
            "bytes 1-1024/2000",
            "bytes 0-1024/3000",
            "bytes 0-2000/2000",
            "bytes 0-5242880/9999999",
            "bytes */2000",
            None,
        ):
            with self.subTest(header=header), self.assertRaises(ValueError):
                parse_content_range(header, 0, 2000)

    def test_short_http_body_never_publishes_audio(self):
        class Client:
            host = "192.0.2.1"

            def call(self, *args, **kwargs):
                return {
                    "result": {
                        "port": 49897,
                        "caCert": "CA",
                        "clientCert": "CERT",
                        "clientPriKey": "KEY",
                    }
                }

        class Context:
            def load_verify_locations(self, **kwargs):
                pass

            def load_cert_chain(self, *args):
                pass

        class Response:
            status = 206

            def getheader(self, name):
                return "bytes 0-9/10"

            def read(self, n):
                return b"short"

        class Connection:
            def request(self, *args, **kwargs):
                pass

            def getresponse(self):
                return Response()

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recording.mp3"
            with (
                patch("oconnect_client.media.ssl.SSLContext", return_value=Context()),
                patch(
                    "oconnect_client.media.http.client.HTTPSConnection", return_value=Connection()
                ),
            ):
                with self.assertRaisesRegex(ValueError, "字节数"):
                    download_media(
                        Client(),
                        {"fileId": "1", "filePath": "/recording.mp3", "fileSize": 10},
                        output,
                    )
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_reject_zero_size_and_invalid_port(self):
        class Client:
            def call(self, *a, **kw):
                return {"result": {"port": 0}}

        with self.assertRaisesRegex(ValueError, "无效媒体端口"):
            download_media(
                Client(), {"fileId": "1", "filePath": "/x", "fileSize": 10}, Path("/unused")
            )


class RetryTests(unittest.TestCase):
    def test_disconnect_retries_only_unfinished_range(self):
        import hashlib

        ranges = []

        class Client:
            host = "192.0.2.1"

            def call(self, *a, **kw):
                return {
                    "result": {
                        "port": 49897,
                        "caCert": "CA",
                        "clientCert": "CERT",
                        "clientPriKey": "KEY",
                    }
                }

        class Context:
            def load_verify_locations(self, **kw):
                pass

            def load_cert_chain(self, *args):
                pass

        class Connection:
            count = 0

            def __init__(self, *a, **kw):
                self.index = Connection.count
                Connection.count += 1

            def request(self, *a, **kw):
                ranges.append(kw["headers"]["Range"])

            def getresponse(self):
                if self.index == 1:
                    raise ConnectionResetError("test interruption")
                return self

            status = 206

            def getheader(self, name):
                return "bytes 0-3/8" if self.index == 0 else "bytes 4-7/8"

            def read(self, n):
                return b"abcd" if self.index == 0 else b"efgh"

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "a.mp3"
            with (
                patch("oconnect_client.media.ssl.SSLContext", return_value=Context()),
                patch("oconnect_client.media.http.client.HTTPSConnection", Connection),
                patch("oconnect_client.media.time.sleep"),
            ):
                digest = download_media(
                    Client(), {"fileId": "1", "filePath": "/a.mp3", "fileSize": 8}, target
                )
            self.assertEqual(target.read_bytes(), b"abcdefgh")
            self.assertEqual(digest, hashlib.sha256(b"abcdefgh").hexdigest())
            self.assertEqual(ranges, ["bytes=0-", "bytes=4-", "bytes=4-"])


class IncrementalTests(unittest.TestCase):
    def test_repeated_cycle_skips_only_verified_local_content(self):
        import contextlib
        import hashlib
        import io

        from oconnect_client.sync import sync_recordings

        row = {
            "filePath": "/storage/emulated/0/Music/Recordings/Watch Recordings/Test/a.mp3",
            "fileSize": 6,
            "fileId": "7",
            "dataModified": 1,
        }

        class Client:
            calls = 0

            def download(self, row, target):
                self.calls += 1
                if target.exists():
                    raise FileExistsError("发现同名内容不一致")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"abcdef")
                return hashlib.sha256(b"abcdef").hexdigest()

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            state = Path(directory) / "state"
            state.mkdir()
            output = Path(directory) / "audio"
            client = Client()
            first = sync_recordings(client, [row], output, state)
            second = sync_recordings(client, [row], output, state)
            self.assertEqual((first["downloaded"], second["skipped"], client.calls), (1, 1, 1))
            (output / "Test/a.mp3").write_bytes(b"broken")
            with self.assertRaises(FileExistsError):
                sync_recordings(client, [row], output, state)
            self.assertEqual(client.calls, 2)


class WatchTests(unittest.TestCase):
    def test_poll_reconnects_after_failure_without_adb(self):
        import contextlib
        import io
        import sys

        import oconnect_client.cli as module

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def list_watch(self):
                return []

        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            argv = [
                "oconnect-client",
                "--identity",
                str(Path(directory) / "identity.json"),
                "--host",
                "192.0.2.1",
                "--state-dir",
                directory,
                "watch",
                "--interval",
                "5",
                "--cycles",
                "2",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    module, "ConnectClient", side_effect=[ConnectionError("offline"), Client()]
                ) as factory,
                patch.object(module.time, "sleep") as sleep,
            ):
                module.main()
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(sleep.call_args_list, [unittest.mock.call(5.0)])
            self.assertTrue(
                all(
                    not call.kwargs["pairing"] and call.kwargs["identity_path"] is not None
                    for call in factory.call_args_list
                )
            )


if __name__ == "__main__":
    unittest.main()

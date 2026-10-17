import json
from unittest.mock import Mock

from oconnect_client.client import ConnectClient


def test_watch_listing_reads_directory_without_media_index():
    root = "/storage/emulated/0/Music/Recordings/Watch Recordings"
    row = {"filePath": root + "/Test/new.mp3", "fileSize": 42, "fileFormat": "mp3"}
    directory = {"filePath": root + "/Test", "fileType": "files", "fileFormat": ""}
    client = ConnectClient("192.0.2.1", identity_path="unused")
    client.call = Mock(return_value={"result": {"result": json.dumps({root: [directory, row]})}})
    assert client.list_watch() == [row]
    client.call.assert_called_once_with(
        "file.list.search", {"filePath": [root], "folder": False, "transfer": True}
    )


def test_call_accepts_directory_api_success_status():
    client = ConnectClient("192.0.2.1", identity_path="unused")
    sent = []
    client.write_object = sent.append
    client.read_object = lambda timeout: {
        "sessionId": sent[0]["sessionId"],
        "statusCode": 200,
        "statusDesc": "OK",
        "result": {"result": "{}"},
    }
    assert client.call("file.list.search", {})["statusCode"] == 200

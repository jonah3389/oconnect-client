"""仅在使用者显式配置后连接已经配对的手机。"""

import os
from pathlib import Path

import pytest

from oconnect_client.client import ConnectClient
from oconnect_client.storage import hash_file


@pytest.mark.live
def test_download_one_recording_with_existing_identity():
    if os.environ.get("OCONNECT_LIVE_TEST") != "1":
        pytest.skip("未显式启用实机测试")
    host = os.environ.get("OCONNECT_TEST_HOST", "")
    if not host or host == "192.0.2.1":
        pytest.fail("请显式设置真实手机地址，不要使用文档测试地址")
    identity = Path(os.environ["OCONNECT_TEST_IDENTITY"]).expanduser()
    output = Path(os.environ["OCONNECT_TEST_OUTPUT"]).expanduser()
    with ConnectClient(host, identity_path=identity) as client:
        rows = client.list_watch()
        assert rows, "手机没有可用于测试的手表录音"
        row = min(rows, key=lambda row: row["fileSize"])
        path = output / f"verified_{row['fileId']}.mp3"
        digest = client.download(row, path)
        assert path.stat().st_size == int(row["fileSize"])
        assert hash_file(path) == digest

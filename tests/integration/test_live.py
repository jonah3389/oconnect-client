"""仅在使用者显式配置后连接已经配对的手机。"""

import hashlib
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
        name = hashlib.sha256(row["filePath"].encode()).hexdigest()[:16]
        path = output / f"verified_{name}.mp3"
        digest = client.download(row, path)
        assert path.stat().st_size == int(row["fileSize"])
        assert hash_file(path) == digest


@pytest.mark.live
def test_roaming_discovery_cache_and_subnet_fallback(tmp_path, monkeypatch):
    """显式实机：注入失效缓存和组播无应答，验证三层路径及身份文件不变。"""
    import json
    from datetime import datetime, timezone

    import oconnect_client.connection as module
    from oconnect_client.identity import load_identity, select_peer
    from oconnect_client.storage import atomic_json
    from oconnect_client.sync import sync_recordings

    if os.environ.get("OCONNECT_LIVE_TEST") != "1":
        pytest.skip("未显式启用实机测试")
    stale_host = os.environ.get("OCONNECT_TEST_STALE_HOST")
    if not stale_host:
        pytest.skip("换址验证需要显式设置已失效地址 OCONNECT_TEST_STALE_HOST")
    identity_path = Path(os.environ["OCONNECT_TEST_IDENTITY"]).expanduser()
    output = Path(os.environ["OCONNECT_TEST_OUTPUT"]).expanduser() / "roaming"
    state = tmp_path / "state"
    state.mkdir()
    identity = load_identity(identity_path)
    peer_id, _ = select_peer(identity, os.environ.get("OCONNECT_TEST_PEER"))
    before = identity_path.read_bytes()
    messages = []
    evidence = {"started_at": datetime.now(timezone.utc).isoformat(), "phases": []}

    def stale_cache():
        atomic_json(
            state / "addresses.json",
            {
                "schema_version": 1,
                "identities": {identity["device_id"]: {peer_id: {"host": stale_host}}},
            },
        )

    options = dict(
        peer_id=peer_id, interface=os.environ.get("OCONNECT_TEST_INTERFACE"), report=messages.append
    )
    try:
        stale_cache()
        with module.connect_paired(identity_path, state, **options) as client:
            rows = client.list_watch()
            assert rows
            row = min(rows, key=lambda r: r["fileSize"])
            first = sync_recordings(client, [row], output, state)
            assert first["downloaded"] == 1
            found_host = client.host
        assert any("缓存/指定地址连接未完成" in m for m in messages), messages
        assert any(("原生组播认证成功" in m or "受限子网单播认证成功" in m) for m in messages), (
            messages
        )
        evidence["phases"].append(
            {
                "name": "stale_cache_recovery",
                "messages": messages[:],
                "host": found_host,
                "downloaded": first["downloaded"],
            }
        )
        messages.clear()
        with module.connect_paired(identity_path, state, **options) as client:
            second = sync_recordings(client, [row], output, state)
            assert client.host == found_host
            assert second["skipped"] == 1
            assert second["downloaded"] == 0
        assert any("缓存/指定地址认证成功" in m for m in messages), messages
        assert not any("开始" in m for m in messages), messages
        evidence["phases"].append(
            {
                "name": "authenticated_cache",
                "messages": messages[:],
                "downloaded": second["downloaded"],
                "skipped": second["skipped"],
            }
        )
        messages.clear()
        # 故障只注入“组播无应答”；子网UDP、身份认证与文件查询均为实机执行。
        real_discover = module.discover

        def without_multicast(*args, **kwargs):
            if kwargs["mode"] == "multicast":
                return []
            return real_discover(*args, **kwargs)

        monkeypatch.setattr(module, "discover", without_multicast)
        stale_cache()
        with module.connect_paired(identity_path, state, **options) as client:
            assert client.list_watch()
            assert client.host == found_host
        assert any("受限子网单播认证成功" in m for m in messages), messages
        evidence["phases"].append(
            {"name": "forced_multicast_no_response", "messages": messages[:], "authenticated": True}
        )
        assert identity_path.read_bytes() == before
        evidence["identity_unchanged"] = True
    finally:
        output.mkdir(parents=True, exist_ok=True)
        (output / "live_evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2)
        )

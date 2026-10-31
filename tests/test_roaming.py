"""地址改变后仍使用明确的配对身份，不把IP当作授权依据。"""

import json

import pytest

from oconnect_client.identity import create_identity, save_identity
from oconnect_client.ukey2 import StoredIdentitySession

PEER = "010203040506"


def paired_identity(tmp_path):
    path = tmp_path / "identity.json"
    identity = create_identity(path)
    identity["peers"][PEER] = dict(host="192.0.2.10", local_ksc="01" * 16, local_alias="02" * 6)
    save_identity(path, identity)
    return path


def test_changed_ip_uses_existing_pairing_without_rewriting_identity(tmp_path):
    path = paired_identity(tmp_path)
    before = path.read_bytes()
    session = StoredIdentitySession("192.0.2.99", path, peer_id=PEER)
    assert session.ksc == bytes([1]) * 16
    assert session.peer_id == PEER
    assert path.read_bytes() == before


def test_multiple_phones_require_explicit_peer_even_when_old_host_matches(tmp_path):
    path = paired_identity(tmp_path)
    identity = json.loads(path.read_text())
    identity["peers"]["111213141516"] = dict(identity["peers"][PEER])
    save_identity(path, identity)
    with pytest.raises(ValueError, match="多台"):
        StoredIdentitySession("192.0.2.10", path)
    with pytest.raises(ValueError, match="未配对"):
        StoredIdentitySession("192.0.2.10", path, peer_id="AABBCCDDEEFF")


@pytest.fixture
def resolver_env(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import oconnect_client.connection as module
    from oconnect_client.discovery import Candidate, LanInterface

    path = paired_identity(tmp_path)
    identity = json.loads(path.read_text())
    state = tmp_path / "state"
    state.mkdir()
    cache = state / "addresses.json"
    local_id = identity["device_id"]
    cache.write_text(
        json.dumps({"schema_version": 1, "identities": {local_id: {PEER: {"host": "192.0.2.20"}}}})
    )
    monkeypatch.setattr(
        module, "lan_interfaces", lambda name: [LanInterface("lan", "192.0.2.5", 24)]
    )
    attempts, failures, clients = [], set(), []

    class Client:
        def __init__(self, host, **kwargs):
            self.host, self.peer_id = host, kwargs["peer_id"]
            self.closed = False
            self.conn = Mock()
            attempts.append(host)
            clients.append(self)

        def __enter__(self):
            if self.host in failures:
                self.close()
                raise ValueError("测试：对方无法证明持有配对密钥")
            return self

        def close(self):
            self.closed = True

    discovery = Mock(return_value=[])
    monkeypatch.setattr(module, "ConnectClient", Client)
    monkeypatch.setattr(module, "discover", discovery)
    return SimpleNamespace(
        module=module,
        path=path,
        state=state,
        cache=cache,
        local_id=local_id,
        attempts=attempts,
        failures=failures,
        clients=clients,
        discovery=discovery,
        candidate=Candidate(PEER, "192.0.2.99", 10152),
    )


def test_authenticated_cache_avoids_discovery_and_keeps_identity(resolver_env):
    e = resolver_env
    before = e.path.read_bytes()
    with e.module.connect_paired(e.path, e.state) as client:
        assert client.host == "192.0.2.20"
        assert client.peer_id == PEER
    assert e.attempts == ["192.0.2.20"]
    e.discovery.assert_not_called()
    assert client.closed
    assert e.path.read_bytes() == before


def test_stale_cache_discovers_new_ip_and_caches_only_after_authentication(resolver_env):
    e = resolver_env
    e.failures.update(["192.0.2.20", "192.0.2.10"])
    e.discovery.return_value = [e.candidate]
    before = e.path.read_bytes()
    with e.module.connect_paired(e.path, e.state) as client:
        assert client.host == "192.0.2.99"
        entry = json.loads(e.cache.read_text())["identities"][e.local_id][PEER]
        assert entry["host"] == "192.0.2.99"
        assert entry["authenticated_at"]
    assert e.attempts == ["192.0.2.20", "192.0.2.99"]
    assert [c.kwargs["mode"] for c in e.discovery.call_args_list] == ["multicast"]
    assert all(c.closed for c in e.clients)
    assert e.path.read_bytes() == before


def test_no_multicast_response_uses_subnet_and_can_disable_it(resolver_env):
    e = resolver_env
    e.failures.update(["192.0.2.20", "192.0.2.10"])
    e.discovery.side_effect = [[], [e.candidate]]
    with e.module.connect_paired(e.path, e.state) as client:
        assert client.host == "192.0.2.99"
    assert [c.kwargs["mode"] for c in e.discovery.call_args_list] == ["multicast", "subnet"]
    e.discovery.reset_mock(side_effect=True)
    e.discovery.return_value = []
    e.failures.add("192.0.2.99")
    with pytest.raises(ConnectionError):
        with e.module.connect_paired(e.path, e.state, subnet=False):
            pytest.fail("认证失败却返回客户端")
    assert [c.kwargs["mode"] for c in e.discovery.call_args_list] == ["multicast"]


def test_failed_authentication_does_not_replace_cache(resolver_env):
    e = resolver_env
    e.failures.update(["192.0.2.20", "192.0.2.10", "192.0.2.99"])
    e.discovery.return_value = [e.candidate]
    before = e.cache.read_bytes()
    with pytest.raises(ConnectionError):
        with e.module.connect_paired(e.path, e.state):
            pytest.fail("未经认证的发现地址不能用于同步")
    assert e.cache.read_bytes() == before
    assert all(c.closed for c in e.clients)


def test_download_failure_does_not_restart_discovery_or_repeat_operation(resolver_env):
    e = resolver_env
    with pytest.raises(RuntimeError, match="下载失败"):
        with e.module.connect_paired(e.path, e.state):
            raise RuntimeError("下载失败")
    assert e.attempts == ["192.0.2.20"]
    e.discovery.assert_not_called()
    assert e.clients[0].closed


@pytest.mark.parametrize("remote_id", [PEER, "111213141516"])
def test_explicit_repair_reuses_keys_by_device_id_and_rejects_different_phone(
    tmp_path, monkeypatch, remote_id
):
    from unittest.mock import Mock

    import oconnect_client.server as server_module
    import oconnect_client.ukey2 as module
    from oconnect_client.scp import pb, wrap

    path = paired_identity(tmp_path)
    before = path.read_bytes()
    server = Mock()
    monkeypatch.setattr(server_module, "OafpControlServer", lambda *args: server)
    connection, handshake = Mock(), Mock(session_key=bytes(32), nonce=bytes(12))
    monkeypatch.setattr(module, "negotiate_ukey2", lambda *args: (connection, handshake))
    session = module.IndependentSecureSession("192.0.2.99", path, peer_id=PEER)
    session.send_enc = Mock()
    session.recv_dec = lambda: (
        0,
        wrap(2, pb(1, 2, remote_id.encode()) + pb(2, 2, bytes([3]) * 16) + pb(3, 2, b"ABCDEF")),
    )
    if remote_id == PEER:
        assert session.establish() == PEER
        assert session.ksc == bytes([1]) * 16
        assert session.ksc_alias == bytes([2]) * 6
        server.set_peer.assert_called_once_with(PEER, bytes([3]) * 16, b"ABCDEF")
    else:
        with pytest.raises(ValueError, match="设备 ID"):
            session.establish()
        server.set_peer.assert_not_called()
    assert path.read_bytes() == before

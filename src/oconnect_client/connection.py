"""缓存、原生组播、受限子网查询依次尝试；只缓存完成认证的地址。"""

import ipaddress
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .client import ConnectClient
from .discovery import discover, lan_interfaces
from .identity import load_identity, select_peer
from .storage import atomic_json


def _read_cache(path):
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
        if cache.get("schema_version") == 1 and isinstance(cache.get("identities"), dict):
            return cache
    except (OSError, ValueError, AttributeError):
        pass
    return {"schema_version": 1, "identities": {}}


def _cached_host(cache, local_id, peer_id):
    try:
        return cache["identities"][local_id][peer_id]["host"]
    except (KeyError, TypeError):
        return None


@contextmanager
def connect_paired(
    identity_path,
    state_dir,
    *,
    peer_id=None,
    host=None,
    interface=None,
    cached_timeout=2,
    discovery_timeout=3,
    timeout=12,
    subnet=True,
    report=None,
):
    """返回已认证的文件客户端；调用方错误不触发换址或重新下载。"""
    identity = load_identity(identity_path)
    peer_id, peer = select_peer(identity, peer_id)
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache_path = state_dir / "addresses.json"
    cache = _read_cache(cache_path)
    local_id = identity["device_id"]
    interfaces = lan_interfaces(interface)
    errors = []

    def announce(message):
        if report:
            report(message)

    def candidates():
        hints = [host, _cached_host(cache, local_id, peer_id) or peer.get("host")]
        tried = set()
        for hint in hints:
            try:
                address = str(ipaddress.IPv4Address(hint))
            except (ValueError, TypeError):
                continue
            if address in tried:
                continue
            if hint != host and not any(nif.contains(address) for nif in interfaces):
                continue
            tried.add(address)
            yield address, "缓存/指定地址", cached_timeout
        for mode, label in [("multicast", "原生组播"), ("subnet", "受限子网单播")]:
            if mode == "subnet" and not subnet:
                continue
            announce(f"开始{label}发现已配对手机 {peer_id}")
            for candidate in discover(
                identity, peer_id, interfaces, mode=mode, timeout=discovery_timeout, report=announce
            ):
                yield candidate.host, label, timeout

    for address, source, attempt_timeout in candidates():
        announce(f"{source}：尝试 {address}，验证原配对身份")
        client = ConnectClient(
            address, timeout=attempt_timeout, identity_path=identity_path, peer_id=peer_id
        )
        try:
            client.__enter__()
        except Exception as error:
            # ConnectClient.__enter__负责关闭失败会话；不把发现包当作授权。
            errors.append(f"{source} {address}: {error}")
            announce(f"{source}连接未完成：{error}")
            continue
        try:
            client.timeout = timeout
            client.conn.settimeout(timeout)
            saved = _read_cache(cache_path)
            peers = saved["identities"].get(local_id)
            if not isinstance(peers, dict):
                peers = {}
                saved["identities"][local_id] = peers
            peers[peer_id] = {
                "host": address,
                "authenticated_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                atomic_json(cache_path, saved)
            except OSError as error:
                announce(f"认证成功，地址缓存写入失败：{type(error).__name__}")
            announce(f"{source}认证成功：{address}")
            yield client
        finally:
            client.close()
        return
    detail = "; ".join(errors[-3:]) if errors else "未收到目标手机的原生发现回复"
    raise ConnectionError(f"未能连接已配对手机 {peer_id}：{detail}；保留原配对关系，下轮重试")

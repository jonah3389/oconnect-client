"""OPPO 官方媒体 HTTPS 通道；配置来自已认证的 video.request 响应。"""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import ssl
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode


def parse_content_range(header, offset, total):
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", header or "")
    if not match:
        raise ValueError("媒体响应缺少有效的 Content-Range")
    start, end, received_total = map(int, match.groups())
    if start != offset or received_total != total or end < start or end >= total:
        raise ValueError("媒体响应范围与请求或文件大小不一致")
    if end - start + 1 > 5 * 1024 * 1024:
        raise ValueError("媒体分段超过官方协议的 5 MiB 上限")
    return end - start + 1


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_media(client, row, destination, *, delay_between_ranges=0):
    """原子保存原始录音；Range 每次校验，短暂断线从已完成的段继续。"""
    # 目录查询没有媒体库 fileId；服务只要求非空 ID，用于本次路径映射。
    video_id = str(row.get("fileId") or uuid.uuid4().hex)
    reply = client.call("video.request", {"videoId": video_id, "videoPath": row["filePath"]})
    config = reply["result"]
    if "data" in config:
        config = config["data"]
    port = int(config["port"])
    if not 0 < port < 65536:
        raise ValueError("手机返回了无效媒体端口")
    total = int(row["fileSize"])
    if total <= 0:
        raise ValueError("录音大小无效")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".oconnect_", dir=destination.parent) as temporary:
        tmp = Path(temporary)
        cert_path, key_path = tmp / "client.pem", tmp / "client.key"
        for path, content in (
            (cert_path, config["clientCert"]),
            (key_path, config["clientPriKey"]),
        ):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as file:
                file.write(content)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # 服务使用临时证书，CA 来自已验签的文件会话；不依赖公共 CA 或 DNS 名称。
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(cadata=config["caCert"])
        ctx.load_cert_chain(cert_path, key_path)
        staged = tmp / "recording"
        offset = 0
        digest = hashlib.sha256()
        with staged.open("wb") as output:
            while offset < total:
                for attempt in range(3):
                    connection = http.client.HTTPSConnection(
                        client.host, port, context=ctx, timeout=30
                    )
                    try:
                        connection.request(
                            "GET",
                            "/video?" + urlencode({"id": video_id}),
                            headers={"Range": f"bytes={offset}-"},
                        )
                        response = connection.getresponse()
                        if response.status != 206:
                            raise ValueError(f"媒体下载 HTTP 状态 {response.status}，预期 206")
                        size = parse_content_range(
                            response.getheader("Content-Range"), offset, total
                        )
                        data = response.read(size + 1)
                        if len(data) != size:
                            raise ValueError("媒体分段字节数与 Content-Range 不一致")
                        break
                    except (
                        ConnectionError,
                        TimeoutError,
                        http.client.IncompleteRead,
                        http.client.RemoteDisconnected,
                    ):
                        if attempt == 2:
                            raise
                        time.sleep(0.5 * (attempt + 1))
                    finally:
                        connection.close()
                output.write(data)
                digest.update(data)
                offset += len(data)
                if delay_between_ranges and offset < total:
                    time.sleep(delay_between_ranges)
        if offset != total or staged.stat().st_size != total:
            raise ValueError("媒体下载后的文件大小不符")
        hexdigest = digest.hexdigest()
        if destination.exists():
            if sha256_file(destination) != hexdigest:
                raise FileExistsError(f"同名录音内容不同，未覆盖：{destination}")
        else:
            os.chmod(staged, 0o600)
            staged.replace(destination)
    return hexdigest

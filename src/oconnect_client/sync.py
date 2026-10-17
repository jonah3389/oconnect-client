"""仅对手表录音执行本地增量采集。"""

import json
from pathlib import Path

from .storage import atomic_json, hash_file


def sync_recordings(client, rows, output, state_dir, limit=0):
    manifest_path = state_dir / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    downloaded = skipped = 0
    for row in rows:
        name = Path(row["filePath"]).name
        if (
            not name
            or name in (".", "..")
            or Path(name).suffix.lower()
            not in (".mp3", ".m4a", ".qta", ".wav", ".aac", ".ogg", ".flac")
        ):
            raise ValueError("不支持的录音文件名")
        relative = Path(row["filePath"].split("/Watch Recordings/", 1)[1])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("远端路径越界")
        target = output / relative
        key = row["filePath"]
        fingerprint = [row["fileSize"], row.get("dataModified"), row.get("lastModifyDate")]
        old = manifest.get(key, {})
        if (
            old.get("fingerprint") == fingerprint
            and target.is_file()
            and target.stat().st_size == int(row["fileSize"])
            and hash_file(target) == old.get("sha256")
        ):
            skipped += 1
            continue
        if limit and downloaded >= limit:
            break
        digest = client.download(row, target)
        manifest[key] = dict(fingerprint=fingerprint, sha256=digest, output=str(target.resolve()))
        atomic_json(manifest_path, manifest)
        downloaded += 1
        print(
            f"已下载 {downloaded} 条；本条 {row['fileSize']} 字节，大小与 SHA-256 已校验",
            flush=True,
        )
    result = dict(found=len(rows), downloaded=downloaded, skipped=skipped)
    print(f"同步完成：发现 {len(rows)} 条，下载 {downloaded} 条，跳过 {skipped} 条。", flush=True)
    return result

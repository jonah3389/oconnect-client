"""内容指纹、原子检查点与单实例锁。"""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def hash_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, obj):
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False, encoding="utf-8"
    ) as file:
        temp = Path(file.name)
        json.dump(obj, file, ensure_ascii=False, indent=2)
        file.write("\n")
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def lock_instance(file):
    if os.name == "nt":
        import msvcrt

        file.seek(0, 2)
        if file.tell() == 0:
            file.write(b"0")
            file.flush()
        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise BlockingIOError("已有同步进程") from error
    else:
        import fcntl

        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)

"""验证安装入口、独立身份初始化和旧脚本入口。"""

import json
import subprocess
import sys
from pathlib import Path

from oconnect_client.identity import load_identity

ROOT = Path(__file__).resolve().parents[1]


def test_init_from_another_directory_and_refuse_overwrite(tmp_path):
    identity = tmp_path / "private" / "identity.json"
    command = [
        sys.executable,
        "-m",
        "oconnect_client",
        "--identity",
        str(identity),
        "init",
        "--name",
        "测试电脑",
    ]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    shown = json.loads(result.stdout)
    assert shown["pairing_status"] == "unpaired"
    assert shown["device_name"] == "测试电脑"
    assert "private_key" not in shown
    before = identity.read_bytes()
    repeated = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True)
    assert repeated.returncode == 1
    assert identity.read_bytes() == before
    assert load_identity(identity)["device_id"] == shown["device_id"]


def test_legacy_script_help_from_another_directory(tmp_path):
    for script in ("oconnect_client.py", "independent_identity.py"):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), "--help"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "--identity" in result.stdout

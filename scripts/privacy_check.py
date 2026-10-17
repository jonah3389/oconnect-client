"""扫描可发布项目文件；忽略 Git、虚拟环境、构建物与私有运行数据。"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".runtime",
    "private",
    "recordings",
    "state",
    "logs",
}
PATTERNS = {
    "本机用户绝对路径": re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/"),
    "局域网真实地址": re.compile(
        r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
    ),
    "私钥内容": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "私有密码管理引用": re.compile(r"op\x3a//[A-Za-z0-9]"),
    "邮箱": re.compile(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}


def violations(root=ROOT):
    findings = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED or part.endswith(".egg-info") for part in relative.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pattern in PATTERNS.items():
                matches = pattern.findall(line)
                if label == "邮箱":
                    matches = [
                        m
                        for m in matches
                        if m.rsplit("@", 1)[1] not in {"example.com", "example.org", "example.net"}
                    ]
                if matches:
                    findings.append((str(relative), lineno, label))
    return findings


def main():
    findings = violations()
    for path, line, label in findings:
        print(f"{path}:{line}: {label}（内容已隐藏）")
    if findings:
        print(f"隐私检查失败：{len(findings)} 项。")
        return 1
    print("隐私检查通过：未发现真实用户路径、局域网地址、私钥、私有引用或非示例邮箱。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

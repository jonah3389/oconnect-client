"""生成本机独立身份。仅生成身份不代表手机已配对或云端已注册。"""

import argparse
import base64
import hashlib
import json
import os
import secrets
import struct
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa


def create_identity(path, name="Python Connect"):
    path = Path(path)
    if not name or len(name.encode("utf-8")) > 255 or any(ord(c) < 32 for c in name):
        raise ValueError("设备名称必须为非空文本，UTF-8 长度不超过 255 字节，不能包含控制字符")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = ec.generate_private_key(ec.SECP256R1())
    identity = dict(
        schema_version=1,
        auth_mode="ukey2",
        device_id=secrets.token_hex(6).upper(),
        device_name=name,
        device_type=11,
        model_id="93CA67",
        pairing_status="unpaired",
        private_key=base64.b64encode(
            key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        ).decode(),
        public_key=base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
        ).decode(),
        peers={},
    )
    agent = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    identity["agent_private_key"] = base64.b64encode(
        agent.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    ).decode()
    identity["agent_public_key"] = base64.b64encode(
        agent.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).decode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(identity, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return identity


def load_identity(path):
    identity = json.loads(Path(path).read_text(encoding="utf-8"))
    if identity.get("schema_version") != 1:
        raise ValueError("不支持的身份配置版本")
    raw = bytes.fromhex(identity["device_id"])
    if len(raw) != 6:
        raise ValueError("设备 ID 必须为 6 字节")
    key = serialization.load_der_private_key(
        base64.b64decode(identity["private_key"]), password=None
    )
    expected = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    if expected != base64.b64decode(identity["public_key"]):
        raise ValueError("身份公私钥不匹配")
    if identity.get("agent_private_key") or identity.get("agent_public_key"):
        agent = serialization.load_der_private_key(
            base64.b64decode(identity["agent_private_key"]), password=None
        )
        public = agent.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        if public != base64.b64decode(identity["agent_public_key"]):
            raise ValueError("业务公私钥不匹配")
    return identity


def summary(identity):
    return {
        k: identity[k]
        for k in ("schema_version", "auth_mode", "device_id", "device_name", "pairing_status")
    }


def build_banner(identity):
    name = identity["device_name"].encode("utf-8")
    if not 1 <= len(name) <= 255:
        raise ValueError("设备名 UTF-8 长度须为 1–255 字节")
    instance = hashlib.sha256(base64.b64decode(identity["public_key"])).hexdigest()[:18].upper()
    peer = ("OAFP_M" + "0" * 12 + instance).encode()
    # DynamicConnection(tag 13)=0：只承诺已实现的默认消息通道，不宣告额外 streaming/filetransfer 端口。
    capabilities = bytes.fromhex("200d01001000000303fe04271002fffb05000a0b020d000e000000000000")

    def tlv(tag, value):
        return bytes([tag, len(value)]) + value

    payload = (
        b"\x01\x00\x02"
        + peer
        + capabilities
        + tlv(8, name)
        + tlv(7, b"Python")
        + tlv(6, b"oconnect_client")
        + bytes.fromhex("0a0b0c02")
    )
    return struct.pack(">H", len(payload)) + payload


def agent_public_key_payload(identity):
    public = base64.b64decode(identity["agent_public_key"])
    return struct.pack(">HH", 2, len(public)) + public


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "show"))
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--name", default="Python Connect")
    args = parser.parse_args()
    identity = (
        create_identity(args.identity, args.name)
        if args.command == "init"
        else load_identity(args.identity)
    )
    print(json.dumps(summary(identity), ensure_ascii=False, indent=2))


# 保持配置更新为原子操作，避免程序中断留下半份私钥或配对记录。
def save_identity(path, identity):
    import tempfile

    path = Path(path)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False, encoding="utf-8"
    ) as file:
        temporary = Path(file.name)
        json.dump(identity, file, ensure_ascii=False, indent=2)
        file.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_agent_keys(path):
    identity = load_identity(path)
    if not identity.get("agent_public_key"):
        agent = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        identity["agent_private_key"] = base64.b64encode(
            agent.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        ).decode()
        identity["agent_public_key"] = base64.b64encode(
            agent.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ).decode()
        save_identity(path, identity)
    return identity


def build_service_registry():
    # OAFP 本地业务端点编号只在当前设备内使用；不包含任何配对身份或私钥。
    return bytes.fromhex(
        "0008020200000000000246696c655472616e7366657252656365697665723b3b0002614c6170703a756e6974652e6f706c75732e6f6e65743b010001000ac7836170703a756e6974652e6f706c75732e6f6e65743b010000000a4d61634368616e6e656c536572766963653b3b0001fd686170703a6f73686172652e6d61632e6167656e743b010001000a"
    )


if __name__ == "__main__":
    main()

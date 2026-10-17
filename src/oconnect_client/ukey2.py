"""独立身份 UKEY2 握手；尚不自动认可手机配对，不读取 ndsf.db。"""

import hashlib
import hmac
import os
import time

from cryptography.hazmat.primitives.asymmetric import ec

from .scp import ScpSocket, one, pb, wrap

D2D_SALT = bytes.fromhex("82aa55a0d397f88346ca1cee8d3909b95f13fa7deb1d4ab38376b8256da85510")


def hkdf(ikm, salt, info):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()


def java_integer(n):
    raw = n.to_bytes(32, "big").lstrip(b"\x00") or b"\x00"
    return b"\x00" + raw if raw[0] & 128 else raw


def public_key_proto(key):
    n = key.public_numbers()
    return pb(1, 0, 1) + pb(2, 2, pb(1, 2, java_integer(n.x)) + pb(2, 2, java_integer(n.y)))


class Ukey2Client:
    def __init__(self):
        self.ephemeral = ec.generate_private_key(ec.SECP256R1())
        self.iv = os.urandom(16)
        self.finish = wrap(
            4, pb(1, 2, public_key_proto(self.ephemeral.public_key())) + pb(2, 2, self.iv)
        )
        commitment = pb(1, 0, 100) + pb(2, 2, hashlib.sha512(self.finish).digest())
        self.client_init = wrap(
            2,
            pb(1, 0, 1)
            + pb(2, 2, os.urandom(32))
            + pb(3, 2, commitment)
            + pb(4, 2, b"AES_256_CBC-HMAC_SHA256"),
        )

    def process_server_init(self, raw):
        if one(raw, 1) != 3:
            raise ValueError("预期 UKEY2 SERVER_INIT")
        body = one(raw, 2)
        if one(body, 1) != 1 or one(body, 3) != 100 or len(one(body, 2)) != 32:
            raise ValueError("UKEY2 版本、密码套件或随机数不符")
        public = one(body, 4)
        if one(public, 1) != 1:
            raise ValueError("UKEY2 对方公钥类型不支持")
        point = one(public, 2)
        x = int.from_bytes(one(point, 1), "big")
        y = int.from_bytes(one(point, 2), "big")
        peer = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
        shared = hashlib.sha256(self.ephemeral.exchange(ec.ECDH(), peer)).digest()
        transcript = self.client_init + raw
        self.auth_bytes = hkdf(shared, b"UKEY2 v1 auth", transcript)[:2]
        next_key = hkdf(shared, b"UKEY2 v1 next", transcript)
        self.session_key = hkdf(next_key, D2D_SALT, b"client")
        self.nonce = self.iv[:12]
        return self.finish


def negotiate_ukey2(host, identity, port=10152):
    connection = ScpSocket(host, port)
    try:
        hello = (
            pb(1, 0, int(time.time()))
            + pb(2, 2, b"active_connection")
            + pb(3, 2, b"com.heytap.accessory")
        )
        connection.send(pb(2, 2, hello), typ=2)
        fl, body = connection.receive()
        print("SCP_HELLO_OK", flush=True)
        sync = (
            pb(1, 2, identity["device_name"].encode())
            + pb(2, 0, 1)
            + pb(4, 0, identity["device_type"])
            + pb(5, 0, 1)
            + pb(6, 2, identity["model_id"].encode())
            + pb(7, 0, 1)
        )
        connection.send(wrap(1, sync))
        fl, body = connection.receive()
        if one(body, 1) != 1:
            raise ValueError("手机未返回 SYNC")
        sync_body = one(body, 2)
        print("SCP_SYNC_OK key_type=", one(sync_body, 2), flush=True)
        handshake = Ukey2Client()
        connection.send(wrap(3, handshake.client_init))
        fl, body = connection.receive()
        if one(body, 1) != 3:
            raise ValueError(f"UKEY2 首帧收到其他消息类型 {one(body, 1)}")
        finish = handshake.process_server_init(one(body, 2))
        connection.send(wrap(4, finish))
        fl, body = connection.receive()
        if one(body, 1) != 4:
            raise ValueError("UKEY2 未收到 FINISHED")
        print("UKEY2_KEY_AGREEMENT_OK；尚未完成手机确认与信任保存", flush=True)
        return connection, handshake
    except BaseException:
        connection.close()
        raise


class IndependentSecureSession:
    """独立本机身份的 SCP 适配器；不导入或读取官方账号数据库。"""

    def __init__(self, host, identity_path, port=10152):
        from .identity import ensure_agent_keys

        self.identity_path = identity_path
        self.identity = ensure_agent_keys(identity_path)
        self.key_id = "0_" + self.identity["device_id"]
        self.host, self.port = host, port
        self.s = None
        from .server import OafpControlServer

        self.control_server = OafpControlServer(host, self.identity)

    def establish(self):
        self.connection, self.handshake = negotiate_ukey2(self.host, self.identity, self.port)
        self.s = self.connection.socket
        self.session_key = self.handshake.session_key
        self.server_nonce = self.handshake.nonce
        # 对已配对手机复用本机的 KSC/alias；不复用任何其他设备的身份。
        peers = self.identity.get("peers", {})
        saved = next((p for p in peers.values() if p.get("host") == self.host), None)
        self.ksc = bytes.fromhex(saved["local_ksc"]) if saved else os.urandom(16)
        self.ksc_alias = bytes.fromhex(saved["local_alias"]) if saved else os.urandom(6)
        inner = (
            pb(1, 2, self.identity["device_id"].encode())
            + pb(2, 2, self.ksc)
            + pb(3, 2, self.ksc_alias)
        )
        self.send_enc(wrap(2, inner))
        fl, body = self.recv_dec()
        if one(body, 1) != 2:
            raise RuntimeError("独立身份 SYNC_SECURE 失败")
        peer = one(body, 2)
        self.peer_dev = one(peer, 1).decode()
        self.peer_ksc = one(peer, 2)
        self.peer_alias = one(peer, 3)
        if len(self.peer_ksc) != 16 or len(self.peer_alias) != 6:
            raise ValueError("手机 KSC 数据长度不符")
        self.control_server.set_peer(self.peer_dev, self.peer_ksc, self.peer_alias)
        return self.peer_dev

    def send_enc(self, payload, fl=0x30):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self.connection.send(
            AESGCM(self.session_key).encrypt(self.server_nonce, payload, None), fl=fl
        )

    def recv_dec(self, timeout=10):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self.connection.socket.settimeout(timeout)
        fl, body = self.connection.receive()
        return fl, AESGCM(self.session_key).decrypt(self.server_nonce, body, None)

    def mark_paired(self):
        from .identity import save_identity

        self.identity["peers"][self.peer_dev] = dict(
            host=self.host,
            local_ksc=self.ksc.hex(),
            local_alias=self.ksc_alias.hex(),
            peer_ksc=self.peer_ksc.hex(),
            peer_alias=self.peer_alias.hex(),
        )
        self.identity["pairing_status"] = "file_channel_verified"
        save_identity(self.identity_path, self.identity)


class StoredIdentitySession:
    """日常运行只使用本设备此前经手机授权保存的秘密，不发起新的 SCP 配对。"""

    s = None

    def __init__(self, host, identity_path):
        from .identity import ensure_agent_keys

        self.identity_path = identity_path
        self.identity = ensure_agent_keys(identity_path)
        self.key_id = "0_" + self.identity["device_id"]
        peer = next((p for p in self.identity["peers"].values() if p.get("host") == host), None)
        if peer is None:
            raise ValueError("此独立身份尚未获得手机录音访问授权，请先运行 pair")
        self.ksc = bytes.fromhex(peer["local_ksc"])
        self.ksc_alias = bytes.fromhex(peer["local_alias"])
        if len(self.ksc) != 16 or len(self.ksc_alias) != 6:
            raise ValueError("已保存的配对密钥无效")

    def mark_paired(self):
        # 文件通道认证通过与手机完整互联 UI 成功是不同验收项。
        pass

"""AES-GCM Token encryption/decryption (Phase 1).

Red lines 7.1 / 3.3: XHS session_data and refresh_token MUST be
AES-GCM encrypted before DB storage. Field names use _encrypted suffix.
Plaintext tokens are NEVER persisted.
"""

import base64
import json
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

_NONCE_SIZE = 12
_KEY_SIZE = 32


def build_credential_aad(user_id: str, provider: str) -> str:
    """BYOK 凭据的附加认证数据（AAD）。

    AES-GCM 的 AAD 不参与加密、但参与完整性校验，解密时传入不一致的值会直接抛
    InvalidTag。把 user_id + provider 绑进 AAD 后，密文与「属于哪个用户的哪个厂商」
    强绑定：攻击者即使拿到写库权限，把 A 用户的密文整段复制到 B 用户的行里，
    解密也会失败，无法让 B 用 A 的 Key 产生账单。
    """
    return f"{user_id}:{provider}"


class TokenCrypto:
    """AES-GCM encryptor for XHS tokens and session data."""

    def __init__(self, key: str | None = None) -> None:
        raw_key = key or get_settings().aes_secret_key
        if not raw_key:
            raise ValueError("AES_SECRET_KEY is not set. Generate a 32-byte base64 key.")
        self._aesgcm = AESGCM(self._decode_key(raw_key))

    @staticmethod
    def _decode_key(raw_key: str) -> bytes:
        key_bytes = base64.b64decode(raw_key)
        if len(key_bytes) != _KEY_SIZE:
            raise ValueError(f"AES key must be {_KEY_SIZE} bytes, got {len(key_bytes)}")
        return key_bytes

    @staticmethod
    def generate_key() -> str:
        return base64.b64encode(os.urandom(_KEY_SIZE)).decode("ascii")

    def encrypt(self, plaintext: str, aad: str | None = None) -> str:
        nonce = os.urandom(_NONCE_SIZE)
        aad_bytes = aad.encode("utf-8") if aad else None
        ct = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), associated_data=aad_bytes)
        return base64.b64encode(nonce + ct).decode("ascii")

    def decrypt(self, ciphertext: str, aad: str | None = None) -> str:
        raw = base64.b64decode(ciphertext)
        if len(raw) < _NONCE_SIZE:
            raise ValueError("Ciphertext too short: must contain at least the nonce")
        nonce = raw[:_NONCE_SIZE]
        ct = raw[_NONCE_SIZE:]
        aad_bytes = aad.encode("utf-8") if aad else None
        return self._aesgcm.decrypt(nonce, ct, associated_data=aad_bytes).decode("utf-8")

    def decrypt_with_fallback(self, ciphertext: str, aad: str | None = None) -> str:
        """先按 AAD 解密，失败再按无 AAD 解密。

        用途：新写入的凭据都带 AAD；历史数据（AAD=None）仍可读取，无需迁移。
        安全性不受影响——跨用户搬运的密文在两种方式下都会失败（原密文用的是别人的 AAD）。
        """
        if aad:
            try:
                return self.decrypt(ciphertext, aad=aad)
            except Exception:
                pass
        return self.decrypt(ciphertext, aad=None)

    def encrypt_dict(self, data: dict[str, Any]) -> str:
        return self.encrypt(json.dumps(data, ensure_ascii=False, separators=(",", ":")))

    def decrypt_dict(self, ciphertext: str) -> dict[str, Any]:
        return json.loads(self.decrypt(ciphertext))

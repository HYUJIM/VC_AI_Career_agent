"""관리형(custodial) 홀더 키 보관.

- 사용자별 Ed25519 키를 BE가 만들고, 개인키는 AES-256-GCM으로 암호화해 holder_keys 테이블에 저장한다.
- 암호화 키(마스터 키)는 비공개 .env의 HOLDER_KEY_ENCRYPTION_KEY(32바이트, base64)에서만 읽는다.
- AAD(추가 인증 데이터)에 user_id와 DID를 묶어, 암호문을 다른 사용자 행으로 옮겨도 복호화되지 않게 한다.
- 개인키 평문은 이 모듈 밖으로 내보내지 않는다. 밖에서는 sign()과 공개 정보만 쓴다.
"""
import base64
import binascii
import hashlib
import os
from dataclasses import dataclass
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from app.domain.did_key import did_key_from_public_key, public_key_to_multibase


class KeyCustodyError(Exception):
    pass


def parse_master_key(value: str) -> bytes:
    """base64(표준/URL-safe) 32바이트 키. 형식이 틀리면 서버 시작 시 바로 실패하게 ValueError."""
    text = value.strip()
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.replace("+", "-").replace("/", "_"))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("HOLDER_KEY_ENCRYPTION_KEY must be base64") from exc
    if len(raw) != 32:
        raise ValueError("HOLDER_KEY_ENCRYPTION_KEY must decode to 32 bytes")
    return raw


@dataclass(frozen=True)
class StoredHolderKey:
    """holder_keys 행에 저장할 값. 개인키는 암호문으로만 들고 있다."""

    did: str
    public_key_multibase: str
    encrypted_private_key: bytes
    nonce: bytes
    kek_id: str


class KeyCustody:
    key_type = "Ed25519"

    def __init__(self, master_key: bytes):
        if len(master_key) != 32:
            raise ValueError("master key must be 32 bytes")
        self._aead = AESGCM(master_key)
        # 어떤 마스터 키로 암호화했는지 구분하는 지문 (키 교체 대비). 키 자체는 노출하지 않는다.
        self.kek_id = hashlib.sha256(b"holder-kek:" + master_key).hexdigest()[:16]

    @staticmethod
    def _aad(user_id: str, did: str) -> bytes:
        return f"holder-key|{user_id}|{did}".encode("utf-8")

    def generate(self, user_id: str) -> StoredHolderKey:
        private_key = Ed25519PrivateKey.generate()
        public_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        private_raw = private_key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        did = did_key_from_public_key(public_raw)
        nonce = os.urandom(12)
        ciphertext = self._aead.encrypt(nonce, private_raw, self._aad(user_id, did))
        return StoredHolderKey(
            did=did,
            public_key_multibase=public_key_to_multibase(public_raw),
            encrypted_private_key=ciphertext,
            nonce=nonce,
            kek_id=self.kek_id,
        )

    def _load(self, user_id: str, stored: StoredHolderKey) -> Ed25519PrivateKey:
        if stored.kek_id != self.kek_id:
            raise KeyCustodyError("holder key was encrypted with a different master key")
        try:
            private_raw = self._aead.decrypt(stored.nonce, stored.encrypted_private_key,
                                             self._aad(user_id, stored.did))
        except InvalidTag as exc:
            raise KeyCustodyError("holder key decryption failed") from exc
        private_key = Ed25519PrivateKey.from_private_bytes(private_raw)
        public_raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        if did_key_from_public_key(public_raw) != stored.did:
            raise KeyCustodyError("holder key does not match DID")
        return private_key

    def sign(self, user_id: str, stored: StoredHolderKey, message: bytes) -> bytes:
        """홀더 키로 Ed25519 서명. (신원 자격증명 수령·제출 단계에서 사용 예정)"""
        return self._load(user_id, stored).sign(message)


def get_key_custody() -> Optional[KeyCustody]:
    """FastAPI 의존성. 마스터 키가 설정되지 않았으면 None (전환 기간: 가짜 DID로 가입)."""
    from app.core.config import get_settings

    key = get_settings().holder_key_encryption_key
    return KeyCustody(key) if key else None

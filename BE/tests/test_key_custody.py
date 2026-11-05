"""did:key 인코딩과 관리형 홀더 키 보관 (DB/네트워크 없음)."""
import dataclasses
import os

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.domain.did_key import (b58decode, b58encode, did_key_from_public_key, is_ed25519_did_key,
                                public_key_from_did_key)
from app.services.key_custody import KeyCustody, KeyCustodyError, parse_master_key

# W3C CCG did:key 명세의 Ed25519 예시 (공개 표준 테스트 벡터)
SPEC_DID = "did:key:z6MkiTBz1ymuepAQ4HEHYSF1H8quG5GLVVQR3djdX3mDooWp"
SPEC_PUBLIC_KEY_BASE58 = "4zvwRjXUKGfvwnParsHAS3HuSVzV5cA4McphgmoCtajS"

MASTER = bytes(range(32))


def test_did_key_matches_spec_vector():
    public_key = b58decode(SPEC_PUBLIC_KEY_BASE58)
    assert did_key_from_public_key(public_key) == SPEC_DID
    assert public_key_from_did_key(SPEC_DID) == public_key


def test_base58_roundtrip_with_leading_zeros():
    for data in (b"", b"\x00", b"\x00\x00\x01\x02", os.urandom(40)):
        assert b58decode(b58encode(data)) == data


def test_rejects_non_ed25519_or_mock_dids():
    assert is_ed25519_did_key(SPEC_DID)
    assert not is_ed25519_did_key("did:key:z6MkMock0123456789ab")  # 기존 임시 DID
    assert not is_ed25519_did_key("did:web:example.org")


def test_generate_produces_real_did_and_encrypted_key():
    custody = KeyCustody(MASTER)
    stored = custody.generate("user-1")
    assert stored.did.startswith("did:key:z6Mk")
    assert is_ed25519_did_key(stored.did)
    assert stored.public_key_multibase == stored.did[len("did:key:"):]
    assert len(stored.nonce) == 12
    assert len(stored.encrypted_private_key) == 32 + 16  # 개인키 32바이트 + GCM 태그


def test_sign_verifies_with_public_key_from_did():
    custody = KeyCustody(MASTER)
    stored = custody.generate("user-1")
    signature = custody.sign("user-1", stored, b"hello")
    public_key = Ed25519PublicKey.from_public_bytes(public_key_from_did_key(stored.did))
    public_key.verify(signature, b"hello")
    with pytest.raises(InvalidSignature):
        public_key.verify(signature, b"tampered")


def test_ciphertext_bound_to_user_id():
    custody = KeyCustody(MASTER)
    stored = custody.generate("user-1")
    with pytest.raises(KeyCustodyError):
        custody.sign("user-2", stored, b"x")  # 다른 사용자 행으로 옮긴 암호문은 복호화되지 않는다


def test_ciphertext_bound_to_did():
    custody = KeyCustody(MASTER)
    stored = custody.generate("user-1")
    swapped = dataclasses.replace(stored, did=custody.generate("user-1").did)
    with pytest.raises(KeyCustodyError):
        custody.sign("user-1", swapped, b"x")


def test_different_master_key_is_rejected():
    stored = KeyCustody(MASTER).generate("user-1")
    with pytest.raises(KeyCustodyError, match="different master key"):
        KeyCustody(bytes(32)).sign("user-1", stored, b"x")


def test_tampered_ciphertext_is_rejected():
    custody = KeyCustody(MASTER)
    stored = custody.generate("user-1")
    broken = dataclasses.replace(stored, encrypted_private_key=bytes([stored.encrypted_private_key[0] ^ 1])
                                 + stored.encrypted_private_key[1:])
    with pytest.raises(KeyCustodyError):
        custody.sign("user-1", broken, b"x")


def test_parse_master_key():
    import base64

    assert parse_master_key(base64.b64encode(MASTER).decode()) == MASTER
    assert parse_master_key(base64.urlsafe_b64encode(MASTER).decode().rstrip("=")) == MASTER
    with pytest.raises(ValueError):
        parse_master_key(base64.b64encode(b"short").decode())
    with pytest.raises(ValueError):
        parse_master_key("not base64 !!!")

"""did:key (Ed25519) 인코딩/디코딩.

W3C CCG did:key 명세: did:key:z + base58btc(multicodec ed25519-pub(0xed 0x01) + 32바이트 공개키)
"""

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}
_ED25519_PUB_PREFIX = b"\xed\x01"
_PREFIX = "did:key:z"


def b58encode(data: bytes) -> str:
    num = int.from_bytes(data, "big")
    out = ""
    while num:
        num, rem = divmod(num, 58)
        out = _ALPHABET[rem] + out
    leading_zeros = len(data) - len(data.lstrip(b"\x00"))
    return "1" * leading_zeros + out


def b58decode(text: str) -> bytes:
    num = 0
    for ch in text:
        if ch not in _INDEX:
            raise ValueError("invalid base58 character")
        num = num * 58 + _INDEX[ch]
    body = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    leading_ones = len(text) - len(text.lstrip("1"))
    return b"\x00" * leading_ones + body


def public_key_to_multibase(public_key: bytes) -> str:
    if len(public_key) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return "z" + b58encode(_ED25519_PUB_PREFIX + public_key)


def did_key_from_public_key(public_key: bytes) -> str:
    return "did:key:" + public_key_to_multibase(public_key)


def public_key_from_did_key(did: str) -> bytes:
    if not did.startswith(_PREFIX):
        raise ValueError("not a base58btc did:key")
    decoded = b58decode(did[len(_PREFIX):])
    if not decoded.startswith(_ED25519_PUB_PREFIX) or len(decoded) != 34:
        raise ValueError("not an Ed25519 did:key")
    return decoded[2:]


def is_ed25519_did_key(did: str) -> bool:
    try:
        public_key_from_did_key(did)
    except ValueError:
        return False
    return True

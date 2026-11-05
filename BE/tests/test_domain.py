"""검증 결과 판정 로직 (DB/네트워크 없음)."""
import uuid
from datetime import datetime, timezone

from app.domain.credential import ProviderBadgeResult, decode_jwt_payload, evaluate_badge, to_record_id
from tests.conftest import make_vc_jwt

DID = "did:key:z6MkTestHolder"
NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)


def provider(token: str, **overrides) -> ProviderBadgeResult:
    vc = decode_jwt_payload(token)["vc"]
    base = dict(valid=True, signature_ok=True, issuer_trusted=True, credential_status="VALID",
                recipient_did=DID, credential_id=vc["id"], issuer_name="예시대학교",
                achievement_name=None, issued_at=None, raw={})
    base.update(overrides)
    return ProviderBadgeResult(**base)


def checks_of(evaluation) -> dict:
    return {c.name: c.result for c in evaluation.checks}


def test_verified_badge():
    token = make_vc_jwt(DID)
    ev = evaluate_badge(provider(token), token, NOW)
    assert ev.status == "verified" and ev.verified
    assert ev.recipient_did == DID
    assert ev.achievement_name == "테스트 수료 배지"
    assert ev.issuer_trust_level == "accredited"
    assert checks_of(ev) == {
        "schema": "pass", "recipient": "pass", "signature": "pass", "issuer_trust": "pass",
        "revocation": "pass", "expiration": "skip", "did_resolution": "skip", "anchor_match": "skip",
    }


def test_revoked_badge():
    token = make_vc_jwt(DID)
    ev = evaluate_badge(provider(token, valid=False, credential_status="REVOKED"), token, NOW)
    assert ev.status == "revoked" and not ev.verified
    assert checks_of(ev)["revocation"] == "fail"


def test_expired_badge_even_if_provider_says_valid():
    token = make_vc_jwt(DID, valid_until="2026-01-01T00:00:00Z")
    ev = evaluate_badge(provider(token), token, NOW)
    assert ev.status == "expired"
    assert checks_of(ev)["expiration"] == "fail"


def test_untrusted_issuer_is_invalid():
    token = make_vc_jwt(DID)
    ev = evaluate_badge(provider(token, issuer_trusted=False), token, NOW)
    assert ev.status == "invalid"
    assert ev.issuer_trust_level == "unknown"


def test_bad_signature_is_invalid():
    token = make_vc_jwt(DID)
    ev = evaluate_badge(provider(token, valid=False, signature_ok=False), token, NOW)
    assert ev.status == "invalid"
    assert checks_of(ev)["signature"] == "fail"


def test_unparseable_token_is_invalid_and_has_no_vc():
    ev = evaluate_badge(provider(make_vc_jwt(DID), recipient_did=None), "not-a-jwt", NOW)
    assert ev.status == "invalid"
    assert ev.vc == {}
    assert checks_of(ev)["schema"] == "fail"


def test_recipient_falls_back_to_credential_subject():
    token = make_vc_jwt(DID)
    ev = evaluate_badge(provider(token, recipient_did=None), token, NOW)
    assert ev.recipient_did == DID


def test_record_id_from_urn_uuid_and_non_uuid():
    value = uuid.uuid4()
    assert to_record_id(f"urn:uuid:{value}") == str(value)
    # 예전 코드는 UUID가 아닌 ID에서 DB 오류가 났다. 이제 결정적 UUIDv5로 바꾼다.
    assert to_record_id("https://example.org/badges/1") == to_record_id("https://example.org/badges/1")
    uuid.UUID(to_record_id("https://example.org/badges/1"))


def test_issued_at_uses_vc_valid_from():
    token = make_vc_jwt(DID)
    ev = evaluate_badge(provider(token), token, NOW)
    assert ev.issued_at == datetime(2026, 10, 2, 3, 34, 21, tzinfo=timezone.utc)

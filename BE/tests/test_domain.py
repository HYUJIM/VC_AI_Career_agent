"""검증 결과 판정 로직 (DB/네트워크 없음)."""
import base64
import json
import uuid
from datetime import datetime, timezone

import pytest

from app.domain.credential import (
    ProviderBadgeResult, decode_jwt_payload, evaluate_badge, is_issuer_confirmed, to_record_id,
)
from tests.conftest import make_vc_jwt

DID = "did:key:z6MkTestHolder"
NOW = datetime(2026, 11, 5, tzinfo=timezone.utc)
EARLIEST = datetime(1, 1, 2, tzinfo=timezone.utc)  # credential._EARLIEST
LATEST = datetime(9999, 12, 30, tzinfo=timezone.utc)  # credential._LATEST
MAX_DEPTH = 64  # credential.MAX_JSON_DEPTH


def make_raw_jwt(payload_json: str) -> str:
    """페이로드 JSON 원문을 그대로 넣은 서명 없는 테스트용 토큰."""
    header = base64.urlsafe_b64encode(b'{"alg":"EdDSA","typ":"JWT"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(payload_json.encode("utf-8", "surrogatepass")).decode().rstrip("=")
    return f"{header}.{payload}.c2ln"


def make_vc_jwt_with(recipient_did: str, *, vc: dict | None = None, top: dict | None = None,
                     raw: str | None = None) -> str:
    """make_vc_jwt 페이로드의 vc·최상위 필드를 덮어쓴 토큰. raw를 주면 문자열 값 "RAW"를 그 JSON 원문으로 바꾼다
    (NaN·1e400처럼 json.dumps로 만들 수 없는 값)."""
    encoded = make_vc_jwt(recipient_did).split(".")[1]
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    payload["vc"].update(vc or {})
    payload.update(top or {})
    text = json.dumps(payload)
    return make_raw_jwt(text if raw is None else text.replace('"RAW"', raw))


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


@pytest.mark.parametrize("status", [1, ["VALID"], {"status": "VALID"}])
def test_non_string_credential_status_is_not_valid_and_not_500(status):
    # 협력사 응답의 상태 값이 문자열이 아니면 VALID가 아닌 상태로 보고 검증 실패로 판정한다 (예외로 500이 나지 않게).
    token = make_vc_jwt(DID)
    ev = evaluate_badge(provider(token, valid=True, credential_status=status), token, NOW)
    assert not ev.verified and ev.status == "invalid"
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


# ── 저장해도 되는 원본인지: 검증기가 발급자 서명·신뢰를 확인한 경우만 ─────────

def test_issuer_confirmed_only_when_verifier_confirms_signature_and_trust():
    token = make_vc_jwt(DID)
    expired = make_vc_jwt(DID, valid_until="2026-01-01T00:00:00Z")

    def confirmed(tok, **overrides) -> bool:
        result = provider(tok, **overrides)
        return is_issuer_confirmed(result, evaluate_badge(result, tok, NOW))

    assert confirmed(token)
    assert confirmed(token, valid=False, credential_status="REVOKED")
    assert confirmed(expired)
    assert not confirmed(token, valid=False, signature_ok=False)
    assert not confirmed(token, valid=False, issuer_trusted=False)
    assert not confirmed(token, valid=False, credential_status="SUSPENDED")
    assert not confirmed(token, valid=False)  # 검증기가 받아들이지 않음 (invalid)
    # 폐기·만료 판정이 서명 판정보다 먼저라서, 서명이 틀리거나 서명·신뢰 결과가 없는 토큰도 revoked/expired가 된다.
    assert not confirmed(token, valid=False, signature_ok=False, credential_status="REVOKED")
    assert not confirmed(token, valid=False, issuer_trusted=None, credential_status="REVOKED")
    assert not confirmed(expired, valid=False, signature_ok=None, issuer_trusted=None, credential_status=None)
    assert not confirmed(expired, issuer_trusted=False)  # 자기 키로 서명한(신뢰되지 않은) 만료 토큰


# ── 형식이 틀린 페이로드: 예외(500) 대신 검증 실패 ─────────────────────────

@pytest.mark.parametrize("token", [
    make_vc_jwt_with(DID, vc={"validUntil": "RAW"}, raw="NaN"),
    make_vc_jwt_with(DID, vc={"validUntil": "RAW"}, raw="-Infinity"),
    make_vc_jwt_with(DID, vc={"score": "RAW"}, raw="1e400"),  # 숫자 리터럴이 float 범위를 넘음 (= inf)
    make_raw_jwt("[" * 100_000 + "]" * 100_000),  # json.loads RecursionError
    # 해석은 되지만 상한(64단계)보다 깊다: 페이로드(1) → vc(2) → extra 목록 63겹 = 65단계
    make_vc_jwt_with(DID, vc={"extra": "RAW"}, raw="[" * 63 + "]" * 63),
    make_vc_jwt_with(DID, vc={"extra": "RAW"}, raw="[" * 500 + "]" * 500),
    make_vc_jwt_with(DID, vc={"name": "a\x00b"}),  # NUL: DB TEXT·JSONB에 넣을 수 없음
    make_vc_jwt_with(DID, vc={"note": "\ud800"}),  # 짝 없는 서로게이트: UTF-8 응답·JSONB에 넣을 수 없음
    make_vc_jwt_with(DID, vc={"\udfff": "key"}),  # dict 키도 검사한다
    make_raw_jwt('{"vc": {"credentialSubject": {"id": "did:key:z6Mk\udc80"}}}'),  # UTF-8이 아닌 바이트
], ids=["nan", "-infinity", "1e400", "deep-nesting", "too-deep-65", "too-deep-502", "nul", "lone-surrogate",
        "surrogate-key", "invalid-utf8"])
def test_unstorable_payload_is_unreadable_and_invalid(token):
    assert decode_jwt_payload(token) is None
    ev = evaluate_badge(provider(make_vc_jwt(DID), recipient_did=None), token, NOW)
    assert ev.status == "invalid" and ev.vc == {} and ev.recipient_did is None
    assert not is_issuer_confirmed(provider(make_vc_jwt(DID), recipient_did=None), ev)


def nested_json(depth: int, kind: str) -> str:
    """dict·list가 depth단계 중첩된 JSON 원문 (가장 바깥 = 1단계). mixed는 dict와 list를 번갈아 쓴다."""
    text = ""
    for level in reversed(range(depth)):
        if kind == "dict" or (kind == "mixed" and level % 2 == 0):
            text = '{"a": ' + text + "}" if text else "{}"
        else:
            text = "[" + text + "]"
    return text


@pytest.mark.parametrize("kind", ["list", "dict", "mixed"])
def test_nesting_is_read_up_to_the_depth_limit_counting_dict_and_list_levels(kind):
    # 페이로드(1단계) 바로 아래 extra가 n단계면 전체 n+1단계. 상한(64단계)까지는 지금처럼 읽고, 넘으면 해석하지 않는다.
    from app.domain import credential

    at_limit = make_vc_jwt_with(DID, top={"extra": "RAW"}, raw=nested_json(MAX_DEPTH - 1, kind))
    over = make_vc_jwt_with(DID, top={"extra": "RAW"}, raw=nested_json(MAX_DEPTH, kind))
    assert decode_jwt_payload(at_limit)["extra"] == json.loads(nested_json(MAX_DEPTH - 1, kind))
    assert evaluate_badge(provider(at_limit), at_limit, NOW).status == "verified"
    assert decode_jwt_payload(over) is None
    assert evaluate_badge(provider(make_vc_jwt(DID)), over, NOW).status == "invalid"
    assert credential.MAX_JSON_DEPTH == MAX_DEPTH


def test_moderately_nested_payload_is_unchanged():
    # 40단계 (페이로드 → vc → extra 목록 38겹): 지금처럼 그대로 읽고 verified로 판정한다.
    token = make_vc_jwt_with(DID, vc={"extra": "RAW"}, raw="[" * 38 + "]" * 38)
    ev = evaluate_badge(provider(token), token, NOW)
    assert ev.status == "verified" and ev.vc["extra"] == json.loads("[" * 38 + "]" * 38)


def test_paired_surrogate_escape_and_finite_numbers_are_still_accepted():
    token = make_vc_jwt_with(DID, vc={"name": "배지 \U0001F680", "score": "RAW"}, raw="1.5e3")  # 🚀 는 이스케이프 쌍
    vc = decode_jwt_payload(token)["vc"]
    assert vc["name"] == "배지 \U0001F680" and vc["score"] == 1500.0


@pytest.mark.parametrize("exp, expected_at, status", [
    (10**20, LATEST, "verified"),  # 너무 먼 미래: 끝값 (만료 아님)
    (10**18, LATEST, "verified"),
    (253402300800, LATEST, "verified"),  # 연도 10000
    (-10**18, EARLIEST, "expired"),  # 너무 먼 과거: 끝값 (만료일 없음으로 바뀌지 않음)
    (-62135596801, EARLIEST, "expired"),  # 연도 0
    (True, datetime(1970, 1, 1, 0, 0, 1, tzinfo=timezone.utc), "expired"),  # 지금과 같은 숫자 해석 유지
])
def test_out_of_range_numeric_expiry_is_clamped(exp, expected_at, status):
    token = make_vc_jwt_with(DID, top={"exp": exp})
    ev = evaluate_badge(provider(token), token, NOW)
    assert ev.expires_at == expected_at and ev.status == status


def test_dates_are_clamped_to_a_range_the_db_can_read_back():
    token = make_vc_jwt_with(DID, vc={"validUntil": "9999-12-31T23:59:59-14:00",
                                      "validFrom": "0001-01-01T00:00:00+14:00"})
    ev = evaluate_badge(provider(token), token, NOW)
    assert ev.expires_at == LATEST and ev.issued_at == EARLIEST and ev.status == "verified"


def test_in_range_dates_keep_the_issuer_offset():
    token = make_vc_jwt_with(DID, vc={"validUntil": "2030-01-01T00:00:00+09:00"})
    ev = evaluate_badge(provider(token), token, NOW)
    assert ev.expires_at.isoformat() == "2030-01-01T00:00:00+09:00"


def test_non_string_names_and_recipient_fall_back():
    token = make_vc_jwt_with(DID, vc={"issuer": {"id": "https://issuer.example.org/demo", "name": ["x"]},
                                      "credentialSubject": {"id": 12345, "achievement": {"name": {"x": 1}}}})
    ev = evaluate_badge(provider(token, recipient_did=None, issuer_name=None), token, NOW)
    assert ev.achievement_name == "Unknown Badge" and ev.issuer_name == "Unknown Issuer"
    assert ev.recipient_did is None and checks_of(ev)["recipient"] == "fail"


def test_unusable_verifier_values_are_ignored():
    token = make_vc_jwt(DID)
    ev = evaluate_badge(provider(token, issued_at=float("nan"), recipient_did="\ud800", issuer_name=7), token, NOW)
    assert ev.issued_at == datetime(2026, 10, 2, 3, 34, 21, tzinfo=timezone.utc)  # validFrom으로 대신
    assert ev.recipient_did == DID and ev.issuer_name == "예시대학교"
    assert evaluate_badge(provider(token, issued_at=1e20), token, NOW).issued_at == LATEST
    assert evaluate_badge(provider(token, issued_at=float("-inf")), token, NOW).issued_at == EARLIEST
    ev = evaluate_badge(provider(token, recipient_did="", achievement_name="a\x00"), token, NOW)
    assert ev.recipient_did == DID and ev.achievement_name == "테스트 수료 배지"


def test_values_longer_than_the_db_columns_fit_varchar_255():
    long_id = "https://issuer.example.org/badges/" + "a" * 300
    token = make_vc_jwt_with(DID, vc={"id": long_id, "issuer": {"id": "https://i.example", "name": "기" * 300},
                                      "credentialSubject": {"id": DID, "achievement": {"name": "배" * 300}}})
    ev = evaluate_badge(provider(token, credential_id=None, issuer_name=None), token, NOW)
    assert ev.achievement_name == "배" * 255 and ev.issuer_name == "기" * 255
    assert ev.external_id.startswith("sha256:") and len(ev.external_id) == 71
    # 같은 원본이면 같은 ID (재검증 가드가 레코드를 다시 찾을 수 있어야 한다)
    assert evaluate_badge(provider(token, credential_id=None, issuer_name=None), token, NOW).record_id == ev.record_id
    # 255자 이하인 ID는 그대로 쓴다
    short = make_vc_jwt_with(DID, vc={"id": "https://issuer.example.org/badges/" + "a" * 200})
    assert evaluate_badge(provider(short, credential_id=None), short, NOW).external_id.endswith("a" * 200)


@pytest.mark.parametrize("credential_id", ["urn:x:\ud800", "urn:x:\x00"])
def test_unstorable_credential_id_from_verifier_is_hashed(credential_id):
    token = make_vc_jwt(DID)
    ev = evaluate_badge(provider(token, credential_id=credential_id), token, NOW)  # 예전: uuid5 인코딩 오류·DB 오류
    assert ev.external_id.startswith("sha256:")
    uuid.UUID(ev.record_id)

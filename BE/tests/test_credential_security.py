"""자격증명 신뢰 보안 테스트 (실제 PostgreSQL + 포트를 직접 구현한 가짜 발급·검증기, 가상 데이터).

- 배지 검증(/verify): 검증기가 발급자 서명·신뢰를 확인한 배지만, 로그인 사용자 본인에게 발급된 것만 저장한다.
  확인되지 않은 토큰은 DB를 건드리지 않고 저장하지 않는다 (남의 레코드 덮어쓰기·ID 선점·소유 여부 확인 차단).
  토큰 크기·문자 제한, 형식이 틀린 페이로드는 500이 아닌 검증 실패.
- 신원 VP: 검증기가 돌려준 홀더 DID가 로그인 사용자 본인 DID여야 한다.
- 모의 발급: 로그인 사용자 토큰으로 본인 DID에만, 허용된 템플릿만, 사용자별 24시간 한도 안에서.
"""
import asyncio
import hashlib
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import psycopg
import pytest

from app.domain.credential import PresentationResult
from app.integrations.ports import ProviderError
from tests.conftest import make_vc_jwt
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
    fake_dx.set_badge_result("garbage", valid=False, recipient_did="\ud800")
    res = verify(client, user, "garbage")
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
def test_identity_vp_holder_must_be_the_caller(client, register, fake_dx, monkeypatch, holder_did):
    user = register()

    async def presentation(vp_token):
        return PresentationResult(verified=True, holder_did=holder_did, claims={"name": "다른학생"}, raw={})

    monkeypatch.setattr(fake_dx, "verify_presentation", presentation)
    res = client.post("/api/v1/verify", json={"type": "identity", "token": "vp"}, headers=user["headers"])
    assert res.status_code == 403 and res.json() == {"detail": "Forbidden: presentation holder is not you"}
    assert "다른학생" not in res.text  # 다른 사람(또는 누구인지 모르는) VP의 클레임을 돌려주지 않는다


def test_identity_vp_of_the_caller_is_unchanged(client, register, fake_dx, monkeypatch):
    user = register()
    # FakeProvider는 vpToken 값을 홀더 DID로 돌려준다.
    res = client.post("/api/v1/verify", json={"type": "identity", "token": user["did"]}, headers=user["headers"])
    assert res.status_code == 200
    assert res.json() == {"success": True, "message": "Identity verified.",
                          "data": {"verified": True, "holder_did": user["did"], "claims": {"name": "가상학생"}}}

    async def rejected(vp_token):
        return PresentationResult(verified=False, holder_did=user["did"], claims={}, raw={})

    monkeypatch.setattr(fake_dx, "verify_presentation", rejected)
    res = client.post("/api/v1/verify", json={"type": "identity", "token": "vp"}, headers=user["headers"])
    assert res.status_code == 200 and res.json()["success"] is False


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
        assert second.result(timeout=10) is None  # 앞 요청이 커밋한 기록까지 세어 한도에서 멈춘다
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

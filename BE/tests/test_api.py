"""API 통합 테스트 (실제 PostgreSQL + 포트를 직접 구현한 가짜 발급·검증기, 가상 데이터)."""
import base64
import logging
import uuid

from app.integrations.ports import ProviderError
from tests.conftest import make_vc_jwt

SERVICE = {"X-Service-Key": "test-service-key"}


def allowed_origin() -> str:
    """CORS_ORIGINS에 들어 있는 FE 출처 하나 (다른 출처에서 띄운 FE를 흉내 낸다)."""
    from fastapi.middleware.cors import CORSMiddleware

    from main import app

    [cors] = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    return cors.kwargs["allow_origins"][0]


# ── 회원가입 / 로그인 / 토큰 ─────────────────────────────────────────

def test_register_returns_token_and_keeps_fe_contract(client):
    email = f"Reg-{uuid.uuid4().hex[:8]}@Test.com"
    res = client.post("/api/v1/users/register", json={"name": "홍길동", "email": email, "password": "password1234"})
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True
    assert set(body["data"]) == {"id", "name", "email", "did"}  # FE UserProfile 계약 유지
    assert body["data"]["email"] == email.lower()
    assert body["token_type"] == "bearer" and body["access_token"] and body["expires_in"] > 0


def test_register_duplicate_email_is_400(client, register):
    user = register()
    res = client.post("/api/v1/users/register",
                      json={"name": "다른", "email": user["email"].upper(), "password": "password1234"})
    assert res.status_code == 400
    assert res.json() == {"detail": "Email already registered"}


def test_login_and_me(client, register):
    user = register("로그인")
    res = client.post("/api/v1/users/login", json={"email": user["email"], "password": "password1234"})
    assert res.status_code == 200
    token = res.json()["access_token"]

    me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["did"] == user["did"]


def test_login_wrong_password_is_401(client, register):
    user = register()
    res = client.post("/api/v1/users/login", json={"email": user["email"], "password": "wrong-password"})
    assert res.status_code == 401
    assert res.json() == {"detail": "Invalid email or password"}


def test_seed_account_can_login(client):
    res = client.post("/api/v1/users/login", json={"email": "user001@example.com", "password": "test1234"})
    assert res.status_code == 200


def test_me_requires_token(client):
    assert client.get("/api/v1/users/me").status_code == 401


def test_invalid_and_tampered_tokens_are_401(client, register):
    user = register()
    assert client.get("/api/v1/users/me", headers={"Authorization": "Bearer garbage"}).status_code == 401
    # 서명(HS256, 32바이트)의 한 비트를 뒤집어 정규 base64url로 다시 붙인다.
    # 끝 두 글자만 바꾸면 'aY'→'aa'처럼 같은 서명으로 디코딩될 수 있다 (PyJWT 버전에 따라 약 1/1024).
    signing_input, signature = user["token"].rsplit(".", 1)
    raw = bytearray(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
    raw[0] ^= 0x01
    tampered = f"{signing_input}.{base64.urlsafe_b64encode(bytes(raw)).decode().rstrip('=')}"
    res = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {tampered}"})
    assert res.status_code == 401
    assert res.json() == {"detail": "Invalid token"}


# ── careers 접근 제어 ─────────────────────────────────────────────────

def test_careers_owner_can_read_other_user_cannot(client, register):
    alice, bob = register("앨리스"), register("밥")
    own = client.get(f"/api/v1/subjects/{alice['did']}/careers", headers=alice["headers"])
    assert own.status_code == 200
    assert own.json()["user_profile"]["did"] == alice["did"]

    other = client.get(f"/api/v1/subjects/{alice['did']}/careers", headers=bob["headers"])
    assert other.status_code == 403


def test_careers_requires_token(client, register):
    # 예전 전환 기간(AUTH_REQUIRED=false)에는 토큰 없이도 DID만 알면 이메일·자격증명 전체를 읽을 수 있었다.
    user = register()
    res = client.get(f"/api/v1/subjects/{user['did']}/careers")
    assert res.status_code == 401
    assert res.json() == {"detail": "Authentication required"}
    assert res.headers["www-authenticate"] == "Bearer"
    assert client.get(f"/api/v1/subjects/{user['did']}/careers", headers=user["headers"]).status_code == 200


def test_careers_service_key(client, register):
    user = register()
    assert client.get(f"/api/v1/subjects/{user['did']}/careers", headers=SERVICE).status_code == 200
    bad = client.get(f"/api/v1/subjects/{user['did']}/careers", headers={"X-Service-Key": "wrong"})
    assert bad.status_code == 401


def test_careers_unknown_did_is_404(client):
    # 사용자 토큰은 본인 DID만 읽으므로(남의 DID는 403, 없어진 사용자의 토큰은 401) 서비스 키로 확인한다.
    res = client.get("/api/v1/subjects/did:key:z6MkNobody/careers", headers=SERVICE)
    assert res.status_code == 404
    assert res.json() == {"detail": "User not found or not verified yet."}


# ── 배지 검증 / 저장 ─────────────────────────────────────────────────

def test_verify_badge_stores_original_token_and_checks(client, register, db):
    user = register()
    token = make_vc_jwt(user["did"])
    res = client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"])
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True and body["data"]["status"] == "verified"
    data = body["data"]
    assert data["valid"] is True and data["achievement_name"] == "테스트 수료 배지" and data["issuer_trusted"] is True
    assert "provider" not in data  # 외부 검증기 응답 원문은 내보내지 않는다
    assert len(data["checks"]) == 8
    body["record_id"] = data["record_id"]

    row = db.execute("SELECT * FROM credentials WHERE record_id = %s", (body["record_id"],)).fetchone()
    assert row["raw_token"] == token  # 서명된 원본 보관 (실행 시점 재검증용)
    assert row["last_verified_at"] is not None
    assert row["raw_data"]["credentialSubject"]["id"] == user["did"]

    checks = {r["check_name"]: r["result"] for r in
              db.execute("SELECT check_name, result FROM verification_checks WHERE record_id = %s",
                         (body["record_id"],)).fetchall()}
    assert checks["signature"] == "pass" and checks["revocation"] == "pass" and checks["recipient"] == "pass"
    assert len(checks) == 8

    careers = client.get(f"/api/v1/subjects/{user['did']}/careers", headers=user["headers"]).json()
    assert [c["record_id"] for c in careers["verified_credentials"]] == [body["record_id"]]
    assert set(careers["verified_credentials"][0]) == {"record_id", "issuer", "achievement_name", "status",
                                                      "issued_at", "raw_data"}  # FE 계약 유지


def test_verify_same_badge_twice_updates_in_place(client, register, db):
    user = register()
    token = make_vc_jwt(user["did"])
    first = client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"]).json()["data"]
    second = client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"]).json()["data"]
    assert first["record_id"] == second["record_id"]
    count = db.execute("SELECT count(*) AS n FROM verification_checks WHERE record_id = %s",
                       (first["record_id"],)).fetchone()["n"]
    assert count == 8


def test_verify_revoked_badge_is_saved_as_revoked(client, register, fake_dx):
    user = register()
    token = make_vc_jwt(user["did"])
    fake_dx.set_badge_result(token, valid=False, credential_status="REVOKED")
    body = client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=user["headers"]).json()
    assert body["success"] is False
    assert body["data"]["status"] == "revoked"


def test_verify_badge_for_someone_else_is_403(client, register):
    alice, bob = register(), register()
    token = make_vc_jwt(alice["did"])
    res = client.post("/api/v1/verify", json={"type": "badge", "token": token}, headers=bob["headers"])
    assert res.status_code == 403


def test_verify_badge_unregistered_recipient_is_403_not_auto_created(client, register, db):
    # 회귀: 예전 코드는 test@example.com 임시 사용자를 자동 생성했고, 두 번째부터 이메일 중복으로 500이 났다.
    user = register()
    did = f"did:key:z6MkUnregistered{uuid.uuid4().hex[:6]}"
    res = client.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(did)}, headers=user["headers"])
    assert res.status_code == 403
    assert res.json() == {"detail": "Forbidden: credential was issued to a different DID"}
    assert db.execute("SELECT 1 FROM users WHERE subject_did = %s", (did,)).fetchone() is None


def test_verify_badge_without_token_is_401_and_saves_nothing(client, register, fake_dx, db):
    # 예전 전환 기간에는 토큰 없이도 수신자 DID의 지갑에 저장됐다 (남의 지갑에 배지를 넣을 수 있었음).
    user = register()
    fake_dx.error = AssertionError("verifier must not be called")  # 인증 전에는 협력사 API를 부르지 않는다
    res = client.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(user["did"])})
    assert res.status_code == 401 and res.json() == {"detail": "Authentication required"}
    assert res.headers["www-authenticate"] == "Bearer"
    assert db.execute("SELECT count(*) AS n FROM credentials WHERE user_id = %s", (user["id"],)).fetchone()["n"] == 0


def test_verify_unparseable_token_is_not_saved(client, register, fake_dx):
    user = register()
    fake_dx.set_badge_result("garbage", valid=False)
    body = client.post("/api/v1/verify", json={"type": "badge", "token": "garbage"}, headers=user["headers"]).json()
    assert body["success"] is False and body["data"]["record_id"] is None


def test_verify_invalid_type_is_400(client, register):
    user = register()
    res = client.post("/api/v1/verify", json={"type": "diploma", "token": "x"}, headers=user["headers"])
    assert res.status_code == 400


def test_verify_with_service_key_is_403(client, register):
    user = register()
    res = client.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(user["did"])}, headers=SERVICE)
    assert res.status_code == 403


def test_verify_identity_does_not_create_users(client, register, db):
    user = register()
    before = db.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
    # FakeProvider는 vpToken 값을 holderDid로 돌려준다.
    res = client.post("/api/v1/verify", json={"type": "identity", "token": user["did"]}, headers=user["headers"])
    assert res.status_code == 200 and res.json()["success"] is True
    assert res.json()["data"] == {"verified": True, "holder_did": user["did"], "claims": {"name": "가상학생"}}
    assert db.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == before

    other = client.post("/api/v1/verify", json={"type": "identity", "token": "did:key:z6MkSomeoneElse"},
                        headers=user["headers"])
    assert other.status_code == 403


def test_dxworks_error_is_502_with_generic_message(client, register, fake_dx):
    user = register()
    origin = allowed_origin()
    fake_dx.error = ProviderError("DXWorks 오류 (INTERNAL): upstream down", upstream_status=500)
    res = client.post("/api/v1/verify", json={"type": "badge", "token": make_vc_jwt(user["did"])},
                      headers={**user["headers"], "Origin": origin})
    assert res.status_code == 502
    # 협력사 오류 문구는 운영 로그에만 남기고, 응답에는 고정 문구와 기계용 code만 싣는다 (main.py).
    assert res.json() == {"detail": "Credential service error", "code": "PROVIDER_ERROR"}
    assert res.headers["access-control-allow-origin"] == origin


def test_unexpected_error_is_json_500_with_cors_header(client, register, fake_dx, caplog):
    from fastapi.middleware.cors import CORSMiddleware

    from main import app

    user = register()
    origin = allowed_origin()
    fake_dx.error = RuntimeError("boom: SELECT secret FROM internals")  # ProviderError가 아니라 처리되지 않는 예외
    body = {"type": "badge", "token": make_vc_jwt(user["did"])}
    with caplog.at_level(logging.ERROR, logger="vc-be"):
        res = client.post("/api/v1/verify", json=body, headers={**user["headers"], "Origin": origin})
    assert res.status_code == 500 and res.json() == {"detail": "Internal server error"}
    # 다른 출처의 FE도 응답을 읽을 수 있어야 네트워크 오류가 아닌 서버 오류로 보여 준다.
    assert res.headers["access-control-allow-origin"] == origin
    assert "boom" not in res.text and "SELECT" not in res.text  # 내부 예외 메시지는 응답에 싣지 않는다
    [record] = [r for r in caplog.records if r.name == "vc-be" and r.getMessage() == "Unhandled error"]
    assert record.exc_info is not None

    same_origin = client.post("/api/v1/verify", json=body, headers=user["headers"])
    assert same_origin.status_code == 500 and same_origin.json() == {"detail": "Internal server error"}
    # exception_handler(Exception/500)는 CORS 바깥에서 실행되므로 쓰지 않고, CORS가 가장 바깥 미들웨어여야 한다.
    assert Exception not in app.exception_handlers and 500 not in app.exception_handlers
    assert app.user_middleware[0].cls is CORSMiddleware


# ── 모의 발급 ────────────────────────────────────────────────────────

def test_mock_issue_to_own_did(client, register, fake_dx):
    user = register()
    res = client.post("/api/mock-issuer/issue", json={"templateId": "tpl", "holderDid": user["did"]},
                      headers=user["headers"])
    assert res.status_code == 200
    assert res.json()["vcJwt"].count(".") == 2
    assert fake_dx.issued == [("tpl", user["did"])]


def test_mock_issue_without_template_uses_server_default(client, register, fake_dx):
    user = register()
    res = client.post("/api/mock-issuer/issue", json={"holderDid": user["did"]}, headers=user["headers"])
    assert res.status_code == 200
    assert fake_dx.issued == [("tpl-default", user["did"])]


def test_mock_issue_without_any_template_is_503(client, register, fake_dx, monkeypatch):
    from app.core.config import get_settings

    user = register()
    monkeypatch.setenv("DXWORKS_BADGE_TEMPLATE_ID", "")
    get_settings.cache_clear()
    res = client.post("/api/mock-issuer/issue", json={"holderDid": user["did"], "templateId": "  "},
                      headers=user["headers"])
    assert res.status_code == 503
    assert fake_dx.issued == []


def test_mock_issue_to_other_did_is_403(client, register, fake_dx):
    alice, bob = register(), register()
    res = client.post("/api/mock-issuer/issue", json={"templateId": "tpl", "holderDid": alice["did"]},
                      headers=bob["headers"])
    assert res.status_code == 403
    assert fake_dx.issued == []


def test_mock_issue_needs_a_valid_user_token(client, register, fake_dx, db):
    # 회사 API 키로 발급하는 API: 토큰 없는 요청·서비스 키·없어진 계정의 토큰은 모두 401이고 협력사 API를 부르지 않는다.
    user = register()
    body = {"templateId": "tpl", "holderDid": user["did"]}
    for headers in ({}, SERVICE):
        res = client.post("/api/mock-issuer/issue", json=body, headers=headers)
        assert res.status_code == 401 and res.json() == {"detail": "Authentication required"}
    db.execute("DELETE FROM users WHERE id = %s", (user["id"],))  # 토큰은 서명·만료가 유효한 채로 계정만 없어진 경우
    res = client.post("/api/mock-issuer/issue", json=body, headers=user["headers"])
    assert res.status_code == 401 and res.json() == {"detail": "Invalid token"}
    assert fake_dx.issued == []


def test_mock_issue_disabled(client, register, monkeypatch):
    from app.core.config import get_settings

    user = register()
    monkeypatch.setenv("MOCK_ISSUER_ENABLED", "false")
    get_settings.cache_clear()
    res = client.post("/api/mock-issuer/issue", json={"templateId": "tpl", "holderDid": user["did"]},
                      headers=user["headers"])
    assert res.status_code == 404


def test_issue_then_verify_end_to_end(client, register):
    """FE 발급 화면 흐름: 발급 → 받은 vcJwt로 검증·저장 → careers에 보임."""
    user = register()
    vc_jwt = client.post("/api/mock-issuer/issue", json={"templateId": "tpl", "holderDid": user["did"]},
                         headers=user["headers"]).json()["vcJwt"]
    verified = client.post("/api/v1/verify", json={"type": "badge", "token": vc_jwt}, headers=user["headers"]).json()
    assert verified["success"] is True
    careers = client.get(f"/api/v1/subjects/{user['did']}/careers", headers=user["headers"]).json()
    assert careers["verified_credentials"][0]["status"] == "verified"


# ── 마이그레이션 호환성 ──────────────────────────────────────────────

def test_seed_data_loaded_with_new_columns_null(db):
    row = db.execute("SELECT count(*) AS n, count(raw_token) AS with_token FROM credentials "
                     "WHERE source_provider = 'mock_seeder'").fetchone()
    assert row["n"] > 400  # 시드가 새 스키마에서도 그대로 들어간다
    assert row["with_token"] == 0  # 시드 데이터는 서명 원본이 없다 (재검증 불가)


# ── 관리형 홀더 키 / 진짜 did:key ─────────────────────────────────────

def test_register_issues_real_did_key_with_encrypted_holder_key(client, register, db):
    from app.domain.did_key import is_ed25519_did_key

    user = register()
    assert is_ed25519_did_key(user["did"])  # 임시 DID(z6MkMock...)가 아닌 진짜 Ed25519 did:key

    row = db.execute("SELECT * FROM holder_keys WHERE user_id = %s", (user["id"],)).fetchone()
    assert row["did"] == user["did"]
    assert len(bytes(row["encrypted_private_key"])) == 48 and len(bytes(row["nonce"])) == 12

    me = client.get("/api/v1/users/me", headers=user["headers"]).json()
    assert me["wallet"] == {"custody": "managed", "key_type": "Ed25519"}
    assert set(me["data"]) == {"id", "name", "email", "did"}  # FE UserProfile 계약 유지


def test_stored_holder_key_signs_for_its_did(client, register):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from app.domain.did_key import public_key_from_did_key
    from app.services.database import db_service
    from app.services.key_custody import get_key_custody

    user = register()
    stored = db_service.get_holder_key(user["id"])
    signature = get_key_custody().sign(user["id"], stored, b"challenge")
    Ed25519PublicKey.from_public_bytes(public_key_from_did_key(user["did"])).verify(signature, b"challenge")


def test_existing_seed_user_keeps_mock_did_without_key(client):
    login = client.post("/api/v1/users/login", json={"email": "user001@example.com", "password": "test1234"}).json()
    assert login["data"]["did"].startswith("did:key:z6MkMock")  # 기존 DID 유지
    me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {login['access_token']}"}).json()
    assert me["wallet"] == {"custody": "none", "key_type": None}


def test_register_without_master_key_falls_back_to_mock_did(client, db, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("HOLDER_KEY_ENCRYPTION_KEY", "")
    get_settings.cache_clear()
    email = f"nokey-{uuid.uuid4().hex[:8]}@test.com"
    body = client.post("/api/v1/users/register",
                       json={"name": "키없음", "email": email, "password": "password1234"}).json()
    assert body["data"]["did"].startswith("did:key:z6MkMock")
    assert db.execute("SELECT 1 FROM holder_keys WHERE user_id = %s", (body["data"]["id"],)).fetchone() is None


def test_duplicate_email_does_not_leave_orphan_holder_key(client, register, db):
    user = register()
    before = db.execute("SELECT count(*) AS n FROM holder_keys").fetchone()["n"]
    res = client.post("/api/v1/users/register", json={"name": "중복", "email": user["email"], "password": "password1234"})
    assert res.status_code == 400
    assert db.execute("SELECT count(*) AS n FROM holder_keys").fetchone()["n"] == before

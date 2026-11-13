"""Google OAuth 2.0 어댑터 및 인증 API 통합 테스트 (MockTransport / FakeOAuthProvider + 실제 PostgreSQL)."""
import json
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest

from app.core.auth import ALGORITHM
from app.core.config import get_settings
from app.domain.did_key import is_ed25519_did_key
from app.integrations import get_google_oauth_provider
from app.integrations.oauth import GoogleOAuthAdapter
from app.integrations.ports import OAuthCodeError, OAuthUserInfo, ProviderError


class FakeGoogleOAuthProvider:
    provider_name = "google"

    def __init__(self):
        self.next_user: OAuthUserInfo | None = None
        self.error: Exception | None = None
        self.calls: list[dict] = []

    def build_authorize_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        code_challenge: str | None = None,
        code_challenge_method: str | None = None,
    ) -> str:
        from urllib.parse import urlencode

        params = {
            "client_id": "test-google-client-id",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = code_challenge_method or "S256"
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> OAuthUserInfo:
        self.calls.append({"code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier})
        if self.error:
            raise self.error
        assert self.next_user is not None
        return self.next_user


@pytest.fixture
def fake_google():
    return FakeGoogleOAuthProvider()


@pytest.fixture
def oauth_client(client, fake_google, monkeypatch):
    from main import app

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-google-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-google-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:5173/auth/google/callback")
    get_settings.cache_clear()
    app.dependency_overrides[get_google_oauth_provider] = lambda: fake_google
    yield client
    app.dependency_overrides.pop(get_google_oauth_provider, None)
    get_settings.cache_clear()


# ── 어댑터 단위 테스트 (httpx MockTransport) ────────────────────────────

def _google_adapter(handler, client_id="cid", client_secret="csec"):
    return GoogleOAuthAdapter(
        client_id=client_id,
        client_secret=client_secret,
        transport=httpx.MockTransport(handler),
    )


def test_adapter_build_authorize_url():
    adapter = _google_adapter(lambda r: httpx.Response(500))
    url = adapter.build_authorize_url(
        state="st-123",
        redirect_uri="http://localhost:5173/auth/google/callback",
        code_challenge="chal",
        code_challenge_method="S256",
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.netloc == "accounts.google.com"
    assert qs["client_id"] == ["cid"]
    assert qs["redirect_uri"] == ["http://localhost:5173/auth/google/callback"]
    assert qs["state"] == ["st-123"]
    assert qs["code_challenge"] == ["chal"]
    assert qs["code_challenge_method"] == ["S256"]


async def test_adapter_exchange_code_fetches_token_and_userinfo():
    seen = {}

    def handler(request: httpx.Request):
        if str(request.url) == "https://oauth2.googleapis.com/token":
            seen["token_form"] = parse_qs(request.content.decode())
            return httpx.Response(200, json={"access_token": "goog-at-1", "token_type": "Bearer"})
        if str(request.url) == "https://openidconnect.googleapis.com/v1/userinfo":
            seen["userinfo_auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={
                "sub": "google-sub-1",
                "email": "Alice@Example.com",
                "email_verified": True,
                "name": "",
            })
        return httpx.Response(404)

    info = await _google_adapter(handler).exchange_code(
        code="auth-code-1",
        redirect_uri="http://localhost:5173/auth/google/callback",
        code_verifier="verifier-1",
    )
    assert seen["token_form"]["code"] == ["auth-code-1"]
    assert seen["token_form"]["code_verifier"] == ["verifier-1"]
    assert seen["userinfo_auth"] == "Bearer goog-at-1"
    assert info == OAuthUserInfo(
        provider="google",
        provider_user_id="google-sub-1",
        email="alice@example.com",
        email_verified=True,
        name="alice",
    )


async def test_adapter_invalid_grant_raises_oauth_code_error():
    def handler(request: httpx.Request):
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Code was already redeemed."})

    with pytest.raises(OAuthCodeError):
        await _google_adapter(handler).exchange_code(code="used", redirect_uri="http://localhost:5173/cb")


async def test_adapter_missing_credentials_is_unavailable():
    adapter = _google_adapter(lambda r: httpx.Response(500), client_id="", client_secret="")
    with pytest.raises(ProviderError) as exc:
        adapter.build_authorize_url(state="s", redirect_uri="http://localhost:5173/cb")
    assert exc.value.unavailable is True


async def test_adapter_network_error_raises_provider_error():
    def handler(request: httpx.Request):
        raise httpx.ConnectError("network down")

    with pytest.raises(ProviderError) as exc:
        await _google_adapter(handler).exchange_code(code="c", redirect_uri="http://localhost:5173/cb")
    assert exc.value.unavailable is False


# ── API + DB 통합 테스트 ───────────────────────────────────────────────

def test_oauth_unconfigured_returns_503(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    get_settings.cache_clear()
    res = client.get("/api/v1/users/oauth/google/authorize")
    assert res.status_code == 503


def test_oauth_authorize_returns_url_and_supports_redirect(oauth_client):
    res = oauth_client.get("/api/v1/users/oauth/google/authorize")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True and body["provider"] == "google"
    assert body["expires_in"] == 600
    assert body["state"] in body["authorization_url"]

    redir = oauth_client.get("/api/v1/users/oauth/google/authorize?redirect=true", follow_redirects=False)
    assert redir.status_code == 302
    assert redir.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth?")


def test_oauth_authorize_validates_redirect_uri_and_pkce(oauth_client, monkeypatch):
    bad_redir = oauth_client.get(
        "/api/v1/users/oauth/google/authorize",
        params={"redirect_uri": "https://evil.example/cb"},
    )
    assert bad_redir.status_code == 400

    bad_pkce = oauth_client.get(
        "/api/v1/users/oauth/google/authorize",
        params={"code_challenge": "abc", "code_challenge_method": "plain"},
    )
    assert bad_pkce.status_code == 400

    monkeypatch.setenv("GOOGLE_ALLOWED_REDIRECT_URIS", "http://localhost:3000/api/v1/users/oauth/google/callback")
    get_settings.cache_clear()
    ok = oauth_client.get(
        "/api/v1/users/oauth/google/authorize",
        params={"redirect_uri": "http://localhost:3000/api/v1/users/oauth/google/callback"},
    )
    assert ok.status_code == 200


def test_oauth_creates_new_user_with_managed_did_and_logs_in(oauth_client, fake_google, db):
    state = oauth_client.get("/api/v1/users/oauth/google/authorize").json()["state"]
    email = f"goog-{uuid.uuid4().hex[:8]}@gmail.com"
    sub = f"sub-{uuid.uuid4().hex[:8]}"
    fake_google.next_user = OAuthUserInfo(
        provider="google", provider_user_id=sub, email=email, email_verified=True, name="구글학생"
    )

    res = oauth_client.post(
        "/api/v1/users/oauth/google/callback",
        json={"code": "code-1", "state": state, "code_verifier": "pkce-v"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True
    assert set(body["data"]) == {"id", "name", "email", "did"}
    assert body["data"]["email"] == email
    assert is_ed25519_did_key(body["data"]["did"])
    assert fake_google.calls[-1]["code_verifier"] == "pkce-v"

    # DB 상태 확인: password_hash는 NULL, holder_keys와 oauth_accounts 저장됨
    user_row = db.execute("SELECT * FROM users WHERE id = %s", (body["data"]["id"],)).fetchone()
    assert user_row["password_hash"] is None
    assert db.execute("SELECT 1 FROM holder_keys WHERE user_id = %s", (user_row["id"],)).fetchone() is not None
    oa = db.execute("SELECT * FROM oauth_accounts WHERE user_id = %s", (user_row["id"],)).fetchone()
    assert oa["provider"] == "google" and oa["provider_user_id"] == sub

    # /me 에서 wallet 및 oauth_providers 확인
    me = oauth_client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {body['access_token']}"}).json()
    assert me["wallet"] == {"custody": "managed", "key_type": "Ed25519"}
    assert me["oauth_providers"] == ["google"]

    # OAuth 전용 사용자는 비밀번호 로그인 불가
    pw_login = oauth_client.post("/api/v1/users/login", json={"email": email, "password": "password1234"})
    assert pw_login.status_code == 401

    # 같은 Google sub로 GET 콜백 재로그인 시(이메일이 변경돼도) 동일 계정 유지
    state2 = oauth_client.get("/api/v1/users/oauth/google/authorize").json()["state"]
    new_email = f"renamed-{uuid.uuid4().hex[:8]}@gmail.com"
    fake_google.next_user = OAuthUserInfo(
        provider="google", provider_user_id=sub, email=new_email, email_verified=True, name="구글학생"
    )
    relogin = oauth_client.get("/api/v1/users/oauth/google/callback", params={"code": "code-2", "state": state2})
    assert relogin.status_code == 200
    assert relogin.json()["data"]["id"] == body["data"]["id"]
    assert relogin.json()["data"]["did"] == body["data"]["did"]


def test_oauth_links_existing_email_user_when_email_verified(oauth_client, fake_google, register, db):
    existing = register("기존유저")
    state = oauth_client.get("/api/v1/users/oauth/google/authorize").json()["state"]
    sub = f"sub-link-{uuid.uuid4().hex[:8]}"
    fake_google.next_user = OAuthUserInfo(
        provider="google",
        provider_user_id=sub,
        email=existing["email"].lower(),
        email_verified=True,
        name="구글이름",
    )

    res = oauth_client.post("/api/v1/users/oauth/google/callback", json={"code": "code-link", "state": state})
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["id"] == existing["id"]
    assert data["did"] == existing["did"]  # 기존 DID 유지

    # 기존 비밀번호 로그인과 /me oauth_providers 모두 정상 동작
    pw_login = oauth_client.post("/api/v1/users/login", json={"email": existing["email"], "password": "password1234"})
    assert pw_login.status_code == 200
    me = oauth_client.get("/api/v1/users/me", headers=existing["headers"]).json()
    assert me["oauth_providers"] == ["google"]

    # 같은 이메일에 다른 Google sub로 로그인 시도는 409 충돌
    state2 = oauth_client.get("/api/v1/users/oauth/google/authorize").json()["state"]
    fake_google.next_user = OAuthUserInfo(
        provider="google",
        provider_user_id=f"other-sub-{uuid.uuid4().hex[:8]}",
        email=existing["email"].lower(),
        email_verified=True,
        name="다른구글",
    )
    conflict = oauth_client.post("/api/v1/users/oauth/google/callback", json={"code": "code-c", "state": state2})
    assert conflict.status_code == 409


def test_oauth_rejects_unverified_email_and_bad_states(oauth_client, fake_google, db):
    state = oauth_client.get("/api/v1/users/oauth/google/authorize").json()["state"]
    email = f"unverified-{uuid.uuid4().hex[:8]}@gmail.com"
    fake_google.next_user = OAuthUserInfo(
        provider="google", provider_user_id="sub-unv", email=email, email_verified=False, name="미인증"
    )
    unverified = oauth_client.post("/api/v1/users/oauth/google/callback", json={"code": "c1", "state": state})
    assert unverified.status_code == 400
    assert db.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone() is None

    # 변조된 state
    assert oauth_client.post(
        "/api/v1/users/oauth/google/callback", json={"code": "c1", "state": "tampered.jwt.token"}
    ).status_code == 400

    # 만료된 state
    expired_state = jwt.encode(
        {
            "type": "oauth_state",
            "provider": "google",
            "redirect_uri": "http://localhost:5173/auth/google/callback",
            "nonce": "n1",
            "exp": datetime.now(timezone.utc) - timedelta(seconds=10),
        },
        get_settings().jwt_secret,
        algorithm=ALGORITHM,
    )
    assert oauth_client.post(
        "/api/v1/users/oauth/google/callback", json={"code": "c1", "state": expired_state}
    ).status_code == 400

    # redirect_uri 불일치
    assert oauth_client.post(
        "/api/v1/users/oauth/google/callback",
        json={"code": "c1", "state": state, "redirect_uri": "http://localhost:5173/other"},
    ).status_code == 400

    # GET 콜백 에러/누락
    assert oauth_client.get("/api/v1/users/oauth/google/callback?error=access_denied").status_code == 400
    assert oauth_client.get("/api/v1/users/oauth/google/callback").status_code == 400

    # 잘못된 OAuth code -> 400, 외부 통신 오류 -> 502
    fake_google.error = OAuthCodeError("Invalid or expired OAuth authorization code")
    assert oauth_client.post("/api/v1/users/oauth/google/callback", json={"code": "bad", "state": state}).status_code == 400

    fake_google.error = ProviderError("Google OAuth 연결 실패: ConnectError")
    assert oauth_client.post("/api/v1/users/oauth/google/callback", json={"code": "c1", "state": state}).status_code == 502

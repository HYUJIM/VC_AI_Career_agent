"""Google OAuth 2.0 어댑터 및 인증 API 통합 테스트 (MockTransport / FakeOAuthProvider + 실제 PostgreSQL).

Google에는 한 번도 요청하지 않는다: 어댑터는 httpx.MockTransport로, API는 FakeGoogleOAuthProvider(또는 MockTransport를 단
실제 어댑터)로 시험한다. 값은 모두 가상의 값이다. GitHub·네이버 어댑터와 제공자 사이의 계정 연동 규칙은
tests/test_oauth_providers.py에서 본다. 경로 시험 중 제공자와 관계없는 것은 세 제공자(google·github·naver)로 함께 돈다.
- 계정 연동 규칙: 비밀번호가 있는 계정에는 OAuth 로그인을 붙이지 않는다 (409). 이미 연동된 계정은 그대로이고, 비밀번호 없는
  계정에는 그 계정의 이메일이 인증된 제공자(Google·GitHub) 연동으로 확인된 주소일 때만 붙인다 (아니면 409).
- 제공자로 내보내기 전의 입력 한도(code·state·redirect_uri 2,048자, PKCE 형식)와 제공자별 프로세스 전체의 토큰 교환 한도(429).
- 어댑터: 2xx만 결과, 프록시 환경변수·리다이렉트 사용 안 함, 프로필 값(sub·email·name) 검사·정리, Google 전용 오류 문구.
- GET 콜백은 development 전용(production 404), 감사 로그(vc-be.audit) event=oauth_login … provider=<제공자>.
"""
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import psycopg
import pytest

from app.api import oauth as oauth_api
from app.core.auth import ALGORITHM
from app.core.config import get_settings
from app.domain.did_key import is_ed25519_did_key
from app.integrations import get_github_oauth_provider, get_google_oauth_provider, get_naver_oauth_provider
from app.integrations import oauth as oauth_adapter
from app.integrations.oauth import GoogleOAuthAdapter
from app.integrations.ports import OAuthCodeError, OAuthUserInfo, ProviderError
from app.services.database import OAUTH_OTHER_METHOD_ACCOUNT, OAUTH_PASSWORD_ACCOUNT, OAuthAccountConflict, db_service
from tests.conftest import TEST_DB_NAME, TEST_DB_PASSWORD

BASE = "/api/v1/users/oauth/google"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
# RFC 7636 부록 B의 예시 쌍 (43자). FE도 43자 code_verifier·S256 code_challenge를 보낸다.
PKCE_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
PKCE_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
TOO_MANY = {"detail": "Too many attempts. Try again later."}
GOOGLE_NOT_CONFIGURED = {"detail": "Google OAuth is not configured", "code": "PROVIDER_NOT_CONFIGURED"}
GOOGLE_ERROR = {"detail": "Google OAuth error", "code": "PROVIDER_ERROR"}
PROVIDERS = ("google", "github", "naver")
LABELS = {"google": "Google", "github": "GitHub", "naver": "Naver"}
DEPENDENCIES = {"google": get_google_oauth_provider, "github": get_github_oauth_provider,
                "naver": get_naver_oauth_provider}
# 가짜 제공자가 만드는 인가 주소 (실제 주소 모양만 흉내 낸다. 요청은 보내지 않는다)
AUTH_URLS = {"google": "https://accounts.google.com/o/oauth2/v2/auth",
             "github": "https://github.com/login/oauth/authorize", "naver": "https://nid.naver.com/oauth2.0/authorize"}
CALLBACKS = {name: f"http://localhost:5173/auth/{name}/callback" for name in PROVIDERS}
# 제공자마다 쓰는 OAuth 설정 (가상 값)
PROVIDER_ENV = {name: {f"{name.upper()}_CLIENT_ID": f"test-{name}-client-id",
                       f"{name.upper()}_CLIENT_SECRET": f"test-{name}-secret",
                       f"{name.upper()}_REDIRECT_URI": CALLBACKS[name]} for name in PROVIDERS}


class FakeOAuthProvider:
    """OAuthProvider 포트를 직접 구현한 가짜 (제공자 이름만 다르다). 외부 API 형식을 모른다.

    calls에는 code·redirect_uri·code_verifier를, states에는 넘겨받은 state를 따로 남긴다."""

    def __init__(self, provider_name: str = "google"):
        self.provider_name = provider_name
        self.next_user: OAuthUserInfo | None = None
        self.error: Exception | None = None
        self.calls: list[dict] = []
        self.states: list[str | None] = []

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
            "client_id": f"test-{self.provider_name}-client-id",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = code_challenge_method or "S256"
        return f"{AUTH_URLS[self.provider_name]}?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
        state: str | None = None,
    ) -> OAuthUserInfo:
        self.calls.append({"code": code, "redirect_uri": redirect_uri, "code_verifier": code_verifier})
        self.states.append(state)
        if self.error:
            raise self.error
        assert self.next_user is not None
        return self.next_user


class FakeGoogleOAuthProvider(FakeOAuthProvider):
    def __init__(self):
        super().__init__("google")


def provide(provider):
    """app.dependency_overrides에 넣을, provider를 돌려주는 인자 없는 의존성."""
    return lambda: provider


@pytest.fixture
def fake_google():
    return FakeGoogleOAuthProvider()


@pytest.fixture
def fakes(fake_google):
    """제공자 이름 → 가짜 제공자. Google 것은 fake_google과 같은 객체다."""
    return {"google": fake_google, "github": FakeOAuthProvider("github"), "naver": FakeOAuthProvider("naver")}


@pytest.fixture
def oauth_client(client, fakes, monkeypatch):
    """세 제공자를 모두 설정하고(가상 값) 가짜 제공자를 끼운 클라이언트."""
    from main import app

    for values in PROVIDER_ENV.values():
        for name, value in values.items():
            monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    for name, fake in fakes.items():
        # 기본값 인자를 둔 lambda는 FastAPI가 쿼리 매개변수로 읽으므로 인자 없는 함수로 감싼다.
        app.dependency_overrides[DEPENDENCIES[name]] = provide(fake)
    yield client
    for dependency in DEPENDENCIES.values():
        app.dependency_overrides.pop(dependency, None)
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

@pytest.mark.parametrize("provider", PROVIDERS)
def test_oauth_unconfigured_returns_503(client, monkeypatch, caplog, provider):
    # 실제 어댑터(가짜를 끼우지 않음)가 설정이 없음을 알린다: 요청은 나가지 않는다.
    monkeypatch.setenv(f"{provider.upper()}_CLIENT_ID", "")
    monkeypatch.setenv(f"{provider.upper()}_CLIENT_SECRET", "")
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="vc-be"):
        res = client.get(f"/api/v1/users/oauth/{provider}/authorize")
    assert res.status_code == 503
    # 회귀: Google 설정이 없는데 발급·검증 인프라 문구(Credential service is not configured)로 답해 FE가 배지 서비스
    # 안내를 보여 줬다. 상태 코드·code·로그 줄('Provider error' 한 줄)은 그대로이고, 문구는 제공자 이름을 쓴다.
    assert res.json() == {"detail": f"{LABELS[provider]} OAuth is not configured", "code": "PROVIDER_NOT_CONFIGURED"}
    if provider == "google":
        assert res.json() == GOOGLE_NOT_CONFIGURED
    assert [r.getMessage() for r in caplog.records if r.name == "vc-be"] == [
        f"Provider error: {LABELS[provider]} OAuth is not configured (upstream=None)"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_oauth_authorize_returns_url_and_supports_redirect(oauth_client, provider):
    res = oauth_client.get(f"/api/v1/users/oauth/{provider}/authorize")
    assert res.status_code == 200
    body = res.json()
    assert body["success"] is True and body["provider"] == provider
    assert body["expires_in"] == 600
    assert body["state"] in body["authorization_url"]
    assert jwt.decode(body["state"], get_settings().jwt_secret, algorithms=[ALGORITHM])["provider"] == provider
    query = parse_qs(urlparse(body["authorization_url"]).query)
    assert query["redirect_uri"] == [CALLBACKS[provider]]  # 그 제공자의 기본 콜백 주소

    redir = oauth_client.get(f"/api/v1/users/oauth/{provider}/authorize?redirect=true", follow_redirects=False)
    assert redir.status_code == 302
    assert redir.headers["location"].startswith(AUTH_URLS[provider] + "?")


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


@pytest.mark.parametrize("provider", ["github", "naver"])
def test_github_and_naver_accept_exactly_their_own_redirect_uri(oauth_client, monkeypatch, provider):
    # GitHub·네이버는 *_REDIRECT_URI 하나만 받는다: Google의 허용 목록·다른 제공자의 콜백 주소는 쓰지 않는다.
    monkeypatch.setenv("GOOGLE_ALLOWED_REDIRECT_URIS", f"http://localhost:3000/auth/{provider}/callback")
    get_settings.cache_clear()
    base = f"/api/v1/users/oauth/{provider}/authorize"
    ok = oauth_client.get(base, params={"redirect_uri": CALLBACKS[provider]})
    assert ok.status_code == 200, ok.text
    for other in (f"http://localhost:3000/auth/{provider}/callback", CALLBACKS["google"], CALLBACKS[provider] + "/",
                  "https://evil.example/cb"):
        res = oauth_client.get(base, params={"redirect_uri": other})
        assert res.status_code == 400 and res.json() == {"detail": "Invalid redirect_uri"}, other
    # 콜백 주소를 비운 서버: redirect_uri를 주든 안 주든 503 (제공자 이름이 든 문구). 허용 목록이 비었으므로
    # FE가 보낸 주소와 비교하지 않는다 (400이면 FE가 '허용되지 않은 로그인 경로'로 보여 준다).
    monkeypatch.setenv(f"{provider.upper()}_REDIRECT_URI", "")
    get_settings.cache_clear()
    for params in ({}, {"redirect_uri": CALLBACKS[provider]}):
        res = oauth_client.get(base, params=params)
        assert res.status_code == 503 and res.json() == {
            "detail": f"{LABELS[provider]} OAuth redirect_uri is not configured"}
    # Google은 그대로다 (다른 제공자 설정의 영향을 받지 않는다).
    assert oauth_client.get("/api/v1/users/oauth/google/authorize").status_code == 200


def test_oauth_creates_new_user_with_managed_did_and_logs_in(oauth_client, fake_google, db):
    state = oauth_client.get("/api/v1/users/oauth/google/authorize").json()["state"]
    email = f"goog-{uuid.uuid4().hex[:8]}@gmail.com"
    sub = f"sub-{uuid.uuid4().hex[:8]}"
    fake_google.next_user = OAuthUserInfo(
        provider="google", provider_user_id=sub, email=email, email_verified=True, name="구글학생"
    )

    res = oauth_client.post(
        "/api/v1/users/oauth/google/callback",
        json={"code": "code-1", "state": state, "code_verifier": PKCE_VERIFIER},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["success"] is True
    # FE(OAuthCallbackPage)가 message의 '회원가입'·'연동' 포함 여부로 환영 안내를 고른다.
    assert body["message"] == "Google OAuth 회원가입 및 로그인 완료"
    assert "회원가입" in body["message"] and "연동" not in body["message"]
    assert set(body["data"]) == {"id", "name", "email", "did"}
    assert body["data"]["email"] == email
    assert is_ed25519_did_key(body["data"]["did"])
    assert fake_google.calls[-1]["code_verifier"] == PKCE_VERIFIER

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
    assert relogin.json()["message"] == "Google OAuth 로그인 성공"  # '회원가입'도 '연동'도 없다
    assert "회원가입" not in relogin.json()["message"] and "연동" not in relogin.json()["message"]


def insert_passwordless_user(db, email: str, name: str = "비밀번호없음", *, attested_by: str | None = None) -> dict:
    """비밀번호 없는 기존 계정을 직접 넣는다 (password_hash NULL).

    attested_by가 None이면 어느 OAuth 연동도 없는 계정(API로는 만들 수 없는 예전 사용자)이고, 'github' 등을 주면 그 제공자로
    만든 계정처럼 같은 이메일의 그 제공자 연동 행도 함께 넣는다 (이메일이 그 연동으로 확인된 계정)."""
    user_id = str(uuid.uuid4())
    did = f"did:key:z6MkOAuthTest{uuid.uuid4().hex[:12]}"
    db.execute("INSERT INTO users (id, email, name, subject_did, password_hash) VALUES (%s, %s, %s, %s, NULL)",
               (user_id, email, name, did))
    if attested_by is not None:
        db.execute("INSERT INTO oauth_accounts (user_id, provider, provider_user_id, email) VALUES (%s, %s, %s, %s)",
                   (user_id, attested_by, f"{attested_by}-{uuid.uuid4().hex[:10]}", email))
    return {"id": user_id, "email": email, "did": did}


def social_login(client, fake: FakeOAuthProvider, *, provider_user_id: str, email: str, name: str = "이름",
                 verified: bool | None = None):
    """fake의 제공자로 authorize → POST callback. verified를 주지 않으면 Naver만 인증되지 않은 이메일이다."""
    provider = fake.provider_name
    state = client.get(f"/api/v1/users/oauth/{provider}/authorize").json()["state"]
    fake.next_user = OAuthUserInfo(provider=provider, provider_user_id=provider_user_id, email=email,
                                   email_verified=provider != "naver" if verified is None else verified, name=name)
    return client.post(f"/api/v1/users/oauth/{provider}/callback",
                       json={"code": f"code-{uuid.uuid4().hex[:8]}", "state": state})


def google_login(client, fake_google, *, sub: str, email: str, name: str = "구글이름", verified: bool = True):
    return social_login(client, fake_google, provider_user_id=sub, email=email, name=name, verified=verified)


def audit_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "vc-be.audit"]


def test_oauth_links_existing_email_user_when_email_verified(oauth_client, fake_google, db):
    # 같은 (Google이 인증한) 이메일의 기존 계정에 비밀번호가 없고, 그 계정의 이메일이 인증된 제공자 연동(여기서는 GitHub으로
    # 만든 계정)으로 확인된 주소일 때 그 계정에 연동한다.
    existing = insert_passwordless_user(db, f"legacy-{uuid.uuid4().hex[:8]}@test.com", attested_by="github")
    sub = f"sub-link-{uuid.uuid4().hex[:8]}"

    res = google_login(oauth_client, fake_google, sub=sub, email=existing["email"])
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["id"] == existing["id"]
    assert data["did"] == existing["did"]  # 기존 DID 유지
    assert res.json()["message"] == "기존 계정에 Google 계정 연동 및 로그인 성공"
    assert "연동" in res.json()["message"] and "회원가입" not in res.json()["message"]  # FE가 이 두 낱말로 안내를 고른다

    me = oauth_client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {res.json()['access_token']}"}).json()
    assert me["oauth_providers"] == ["github", "google"]  # 제공자 이름 순
    # 다시 로그인하면 연동이 아니라 그냥 로그인이다.
    again = google_login(oauth_client, fake_google, sub=sub, email=existing["email"])
    assert again.status_code == 200 and again.json()["message"] == "Google OAuth 로그인 성공"

    # 같은 이메일에 다른 Google sub로 로그인 시도는 409 충돌
    conflict = google_login(oauth_client, fake_google, sub=f"other-sub-{uuid.uuid4().hex[:8]}",
                            email=existing["email"], name="다른구글")
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "User is already linked to another account for this provider"}
    assert db.execute("SELECT count(*) AS n FROM oauth_accounts WHERE user_id = %s",
                      (existing["id"],)).fetchone()["n"] == 2  # GitHub + 처음 연동한 Google


def test_google_does_not_link_a_passwordless_account_without_a_verified_provider_link(oauth_client, fake_google, db,
                                                                                    caplog):
    """어느 OAuth 연동도 없는 비밀번호 없는 계정(API로는 만들 수 없는 예전 계정)의 이메일은 확인된 주소가 아니다:
    Google이 그 이메일을 인증했어도 붙이지 않고 409 OAUTH_OTHER_METHOD_ACCOUNT로 답한다 (새 계정도 만들지 않는다)."""
    legacy = insert_passwordless_user(db, f"unattested-{uuid.uuid4().hex[:8]}@test.com")
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = google_login(oauth_client, fake_google, sub=f"sub-{uuid.uuid4().hex[:8]}", email=legacy["email"])
    assert res.status_code == 409 and res.json() == {"detail": OAUTH_OTHER_METHOD_ACCOUNT}
    assert OAUTH_OTHER_METHOD_ACCOUNT == ("This email is already used by another sign-in method."
                                          " Log in with that method.")  # FE가 이 문구로 안내를 고른다
    assert audit_lines(caplog) == [f"event=oauth_login outcome=refused_unverified_link user={legacy['id']}"
                                   " provider=google"]
    assert db.execute("SELECT count(*) AS n FROM oauth_accounts WHERE user_id = %s",
                      (legacy["id"],)).fetchone()["n"] == 0
    assert db.execute("SELECT count(*) AS n FROM users WHERE lower(email) = %s",
                      (legacy["email"],)).fetchone()["n"] == 1


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
    denied = oauth_client.get("/api/v1/users/oauth/google/callback?error=access_denied")
    assert denied.status_code == 400 and denied.json() == {"detail": "OAuth authorization failed: access_denied"}
    assert oauth_client.get("/api/v1/users/oauth/google/callback").status_code == 400

    # 잘못된 OAuth code -> 400, 외부 통신 오류 -> 502
    fake_google.error = OAuthCodeError("Invalid or expired OAuth authorization code")
    assert oauth_client.post("/api/v1/users/oauth/google/callback", json={"code": "bad", "state": state}).status_code == 400

    fake_google.error = ProviderError("Google OAuth 연결 실패: ConnectError", service="google_oauth")
    failed = oauth_client.post("/api/v1/users/oauth/google/callback", json={"code": "c1", "state": state})
    assert failed.status_code == 502 and failed.json() == GOOGLE_ERROR


# ── 계정 연동 규칙: 비밀번호가 있는 계정에는 Google 로그인을 붙이지 않는다 ────────────

def test_google_login_never_attaches_to_a_password_account(oauth_client, fake_google, db, caplog):
    """계정 선점 회귀: 남의 주소로 먼저 가입해 둔 계정에, Google로 로그인한 그 주소의 진짜 주인을 넣지 않는다.

    고치기 전에는 200 '기존 계정에 Google 계정 연동 및 로그인 성공'이었고, 먼저 가입한 사람의 비밀번호와 연동 전에 받은
    토큰이 그대로 살아 있어 주인이 그 뒤에 저장한 자격증명을 계속 볼 수 있었다 (주인이 내보낼 방법도 없었다).
    """
    victim_email = f"victim-{uuid.uuid4().hex[:8]}@gmail.com"
    victim_sub = f"victim-sub-{uuid.uuid4().hex[:8]}"
    # 1) 공격자: 피해자의 주소로 비밀번호 가입 (가입은 이메일 소유를 확인하지 않는다)
    reg = oauth_client.post("/api/v1/users/register",
                            json={"name": "attacker", "email": victim_email, "password": "attacker-pw-123"})
    assert reg.status_code == 200, reg.text
    attacker = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    account_id, did = reg.json()["data"]["id"], reg.json()["data"]["did"]

    # 2) 피해자: 같은 주소(Google이 인증함)로 Google 로그인. 대문자 표기로 와도, 다시 해도 같다.
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        for email in (victim_email, victim_email.upper()):
            res = google_login(oauth_client, fake_google, sub=victim_sub, email=email, name="Real Victim")
            assert res.status_code == 409, res.text
            assert res.json() == {"detail": OAUTH_PASSWORD_ACCOUNT}
            assert "access_token" not in res.text  # 그 계정의 토큰을 주지 않는다
    # FE가 이 문구로 안내를 고른다 (FE/src/lib/apiClient.ts). 글자 하나도 바꾸지 않는다.
    assert OAUTH_PASSWORD_ACCOUNT == "This email is registered with a password. Log in with your email and password."
    # 감사 로그: 거절한 시도마다 한 줄, 그 기존 계정의 id와 제공자 이름만 (이메일·이름·sub 없음)
    assert audit_lines(caplog) == [
        f"event=oauth_login outcome=refused_password_account user={account_id} provider=google"] * 2

    # 3) 연동도 새 계정도 생기지 않았고 계정은 그대로다.
    assert db.execute("SELECT count(*) AS n FROM oauth_accounts WHERE user_id = %s OR provider_user_id = %s",
                      (account_id, victim_sub)).fetchone()["n"] == 0
    rows = db.execute("SELECT id, name, password_hash IS NOT NULL AS has_pw, token_version FROM users"
                      " WHERE lower(email) = %s", (victim_email,)).fetchall()
    assert [(str(r["id"]), r["name"], r["has_pw"], r["token_version"]) for r in rows] == [
        (account_id, "attacker", True, 0)]

    # 4) 공격자의 세션에는 새로 보이는 것이 없다: 피해자는 그 계정에 들어온 적이 없다.
    me = oauth_client.get("/api/v1/users/me", headers=attacker)
    assert me.status_code == 200 and me.json()["oauth_providers"] == []
    careers = oauth_client.get(f"/api/v1/subjects/{did}/careers", headers=attacker)
    assert careers.status_code == 200 and careers.json()["verified_credentials"] == []


def test_password_account_linked_earlier_still_logs_in_with_that_google_account(oauth_client, fake_google, register,
                                                                                db, caplog):
    # 이미 연동된 계정은 (provider, sub)로 찾으므로 비밀번호가 있어도 예전처럼 로그인된다.
    existing = register("예전연동")
    sub = f"sub-old-{uuid.uuid4().hex[:8]}"
    db.execute("INSERT INTO oauth_accounts (user_id, provider, provider_user_id, email) VALUES (%s, 'google', %s, %s)",
               (existing["id"], sub, existing["email"]))
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = google_login(oauth_client, fake_google, sub=sub, email=existing["email"])
    assert res.status_code == 200 and res.json()["data"]["id"] == existing["id"]
    assert res.json()["message"] == "Google OAuth 로그인 성공"
    assert audit_lines(caplog) == [f"event=oauth_login outcome=login user={existing['id']} provider=google"]
    # 그 계정에 다른 Google 계정으로 오면 예전처럼 '이미 다른 계정이 연동됨' 409다.
    other = google_login(oauth_client, fake_google, sub=f"{sub}-other", email=existing["email"])
    assert other.status_code == 409
    assert other.json() == {"detail": "User is already linked to another account for this provider"}


def test_oauth_login_audit_lines_carry_the_outcome_and_user_id_only(oauth_client, fake_google, db, caplog):
    email = f"audit-{uuid.uuid4().hex[:8]}@gmail.com"
    sub = f"sub-audit-{uuid.uuid4().hex[:8]}"
    legacy = insert_passwordless_user(db, f"audit-legacy-{uuid.uuid4().hex[:8]}@test.com", name="감사로그",
                                      attested_by="github")
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        created = google_login(oauth_client, fake_google, sub=sub, email=email, name="감사 대상")
        again = google_login(oauth_client, fake_google, sub=sub, email=email, name="감사 대상")
        linked = google_login(oauth_client, fake_google, sub=f"{sub}-legacy", email=legacy["email"])
        # 로그인으로 끝나지 않은 요청(인증되지 않은 이메일 400)은 남기지 않는다.
        unverified = google_login(oauth_client, fake_google, sub=f"{sub}-unverified",
                                  email=f"unverified-{uuid.uuid4().hex[:8]}@gmail.com", verified=False)
    assert [r.status_code for r in (created, again, linked, unverified)] == [200, 200, 200, 400]
    user_id = created.json()["data"]["id"]
    assert audit_lines(caplog) == [
        f"event=oauth_login outcome=created user={user_id} provider=google",
        f"event=oauth_login outcome=login user={user_id} provider=google",
        f"event=oauth_login outcome=linked user={legacy['id']} provider=google",
    ]
    assert all(r.levelno == logging.INFO for r in caplog.records if r.name == "vc-be.audit")
    text = " ".join(audit_lines(caplog))
    for private in (email, legacy["email"], sub, "감사", created.json()["data"]["did"], created.json()["access_token"]):
        assert private not in text


# ── 동시 요청: 유일 제약에 걸리면 한 번 다시 본다 ───────────────────────────────

def _open_connection():
    return psycopg.connect(host=os.environ["DB_HOST"], port=int(os.environ["DB_PORT"]), user=os.environ["DB_USER"],
                           password=TEST_DB_PASSWORD, dbname=TEST_DB_NAME)


def _no_new_identity(user_id: str):
    raise AssertionError("must not create a new user")


def _race(db, first_inserts: list[tuple[str, tuple]], **call) -> dict:
    """먼저 온 요청의 INSERT를 커밋하지 않은 채 두고(다른 탭의 같은 로그인이 진행 중), 두 번째 요청을 스레드에서 실행한다.

    두 번째가 유일 인덱스의 커밋되지 않은 행을 기다리기 시작하면 첫 요청을 커밋한다. 두 번째의 결과를
    {"result": (user, created, linked)} 또는 {"error": 예외}로 돌려준다.
    """
    outcome: dict = {}

    def second():
        try:
            outcome["result"] = db_service.find_or_create_oauth_user(**call)
        except Exception as exc:  # noqa: BLE001  (어떤 예외로 끝났는지가 시험 대상이다)
            outcome["error"] = exc

    thread = threading.Thread(target=second)
    first = _open_connection()
    try:
        for sql, params in first_inserts:
            first.execute(sql, params)
        thread.start()
        deadline = time.monotonic() + 10
        while not db.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname = current_database()"
                             " AND wait_event_type = 'Lock'").fetchone()["n"]:
            assert time.monotonic() < deadline, "the second request never waited on the uncommitted row"
            time.sleep(0.02)
        first.commit()
    finally:
        first.close()  # 커밋하지 못했으면 되돌려서 기다리는 스레드를 풀어 준다
        if thread.ident is not None:
            thread.join(15)
    assert not thread.is_alive()
    return outcome


LINK_ROW = "INSERT INTO oauth_accounts (user_id, provider, provider_user_id, email) VALUES (%s, 'google', %s, %s)"


def test_concurrent_link_of_the_same_google_account_logs_in_instead_of_409(db):
    """회귀: 유일 제약 위반을 제약 이름의 부분 문자열('user_id'와 'provider')로 가렸는데 oauth_accounts의 두 제약이 모두
    걸렸다(provider_user_id에 user_id가 들어 있다). 그래서 같은 Google 사용자의 동시 요청이 다시 시도되지 않고
    '다른 계정이 이미 연동됨' 409가 됐다."""
    # GitHub으로 만든 계정(이메일이 확인된 주소)에 같은 Google 계정을 두 탭이 동시에 연동한다.
    existing = insert_passwordless_user(db, f"race-{uuid.uuid4().hex[:8]}@test.com", attested_by="github")
    sub = f"race-sub-{uuid.uuid4().hex[:8]}"
    outcome = _race(db, [(LINK_ROW, (existing["id"], sub, existing["email"]))],
                    provider="google", provider_user_id=sub, email=existing["email"], email_verified=True, name="n",
                    issue_identity=_no_new_identity)
    assert "error" not in outcome, outcome
    user, created, linked = outcome["result"]
    assert str(user["id"]) == existing["id"] and (created, linked) == (False, False)  # 먼저 끝난 연동으로 로그인
    assert "oauth_email" not in user
    assert db.execute("SELECT count(*) AS n FROM oauth_accounts WHERE user_id = %s AND provider = 'google'",
                      (existing["id"],)).fetchone()["n"] == 1


def test_concurrent_first_login_of_a_new_google_user_creates_one_account(db):
    email = f"race-new-{uuid.uuid4().hex[:8]}@gmail.com"
    sub = f"race-new-sub-{uuid.uuid4().hex[:8]}"
    first_id = str(uuid.uuid4())
    outcome = _race(
        db,
        [("INSERT INTO users (id, email, name, subject_did, password_hash) VALUES (%s, %s, 'first', %s, NULL)",
          (first_id, email, f"did:key:z6MkRaceFirst{uuid.uuid4().hex[:12]}")),
         (LINK_ROW, (first_id, sub, email))],
        provider="google", provider_user_id=sub, email=email, email_verified=True, name="second",
        issue_identity=lambda user_id: (f"did:key:z6MkRaceSecond{uuid.uuid4().hex[:12]}", None),
    )
    assert "error" not in outcome, outcome
    user, created, linked = outcome["result"]
    assert str(user["id"]) == first_id and (created, linked) == (False, False)
    assert db.execute("SELECT count(*) AS n FROM users WHERE email = %s", (email,)).fetchone()["n"] == 1


def test_concurrent_link_of_a_different_google_account_is_still_a_conflict(db):
    # 제약 이름을 보지 않아도, 다시 볼 때의 명시적 검사가 '다른 Google 계정이 이미 연동됨'을 가려낸다.
    existing = insert_passwordless_user(db, f"race-other-{uuid.uuid4().hex[:8]}@test.com", attested_by="github")
    sub = f"race-a-{uuid.uuid4().hex[:8]}"
    outcome = _race(db, [(LINK_ROW, (existing["id"], sub, existing["email"]))],
                    provider="google", provider_user_id=f"race-b-{uuid.uuid4().hex[:8]}", email=existing["email"],
                    email_verified=True, name="n", issue_identity=_no_new_identity)
    assert isinstance(outcome.get("error"), OAuthAccountConflict), outcome
    assert str(outcome["error"]) == "User is already linked to another account for this provider"
    rows = db.execute("SELECT provider_user_id FROM oauth_accounts WHERE user_id = %s AND provider = 'google'",
                      (existing["id"],)).fetchall()
    assert [r["provider_user_id"] for r in rows] == [sub]


@pytest.fixture
def commit_after_first_select(monkeypatch):
    """넣은 함수를 요청의 첫 조회(provider·sub로 연동 찾기)가 끝난 바로 뒤에 한 번 실행한다: {"fn": 함수, "fired": 횟수}.

    같은 Google 계정의 다른 요청이 그 사이에 커밋하는 순서를 그대로 만든다 (유일 제약에 걸리지 않는 경합).
    """
    from app.services import database

    hook = {"fn": None, "fired": 0}
    real = database.get_db_connection

    class HookCursor(psycopg.Cursor):
        def execute(self, query, params=None, **kwargs):
            result = super().execute(query, params, **kwargs)
            if hook["fn"] and "FROM oauth_accounts oa" in str(query):
                fn, hook["fn"] = hook["fn"], None
                hook["fired"] += 1
                fn()
            return result

    def connect(**extra):
        conn = real(**extra)
        conn.cursor_factory = HookCursor
        return conn

    monkeypatch.setattr(database, "get_db_connection", connect)
    return hook


def test_same_google_account_committed_between_the_two_lookups_is_a_login_not_a_conflict(db, commit_after_first_select):
    """회귀: 같은 Google 계정의 로그인 두 개가 겹칠 때, 다른 요청의 커밋이 이 요청의 첫 조회(provider·sub)와 둘째 조회
    (이메일) 사이에 끝나면 유일 제약에 걸리지 않고 '다른 계정이 이미 연동됨' 409가 됐다 (실측: 동시 첫 로그인 96건 중 2건).
    연동된 sub가 지금 들어온 sub와 같으면 그 계정의 로그인이다."""
    # 처음 로그인하는 Google 사용자: 다른 탭이 사용자와 연동을 먼저 만든다
    email, sub, first_id = f"win-{uuid.uuid4().hex[:8]}@gmail.com", f"win-sub-{uuid.uuid4().hex[:8]}", str(uuid.uuid4())

    def other_tab_creates_the_account():
        with _open_connection() as other:
            other.execute("INSERT INTO users (id, email, name, subject_did, password_hash) VALUES (%s, %s, 'first', %s,"
                          " NULL)", (first_id, email, f"did:key:z6MkWinFirst{uuid.uuid4().hex[:12]}"))
            other.execute(LINK_ROW, (first_id, sub, email))

    commit_after_first_select["fn"] = other_tab_creates_the_account
    user, created, linked = db_service.find_or_create_oauth_user(
        provider="google", provider_user_id=sub, email=email, email_verified=True, name="second",
        issue_identity=_no_new_identity)
    assert commit_after_first_select["fired"] == 1
    assert str(user["id"]) == first_id and (created, linked) == (False, False) and "oauth_email" not in user
    assert db.execute("SELECT count(*) AS n FROM users WHERE email = %s", (email,)).fetchone()["n"] == 1

    # 비밀번호 없는 기존 계정: 다른 탭이 같은 Google 계정의 연동을 먼저 넣는다
    existing = insert_passwordless_user(db, f"win-legacy-{uuid.uuid4().hex[:8]}@test.com")
    legacy_sub = f"win-legacy-sub-{uuid.uuid4().hex[:8]}"

    def other_tab_links():
        with _open_connection() as other:
            other.execute(LINK_ROW, (existing["id"], legacy_sub, existing["email"]))

    commit_after_first_select["fn"] = other_tab_links
    user, created, linked = db_service.find_or_create_oauth_user(
        provider="google", provider_user_id=legacy_sub, email=existing["email"], email_verified=True, name="n",
        issue_identity=_no_new_identity)
    assert commit_after_first_select["fired"] == 2
    assert str(user["id"]) == existing["id"] and (created, linked) == (False, False)
    assert db.execute("SELECT count(*) AS n FROM oauth_accounts WHERE user_id = %s",
                      (existing["id"],)).fetchone()["n"] == 1

    # 그 사이에 커밋된 것이 다른 Google 계정의 연동이면 지금처럼 충돌이다
    other_user = insert_passwordless_user(db, f"win-other-{uuid.uuid4().hex[:8]}@test.com")

    def other_tab_links_another_google_account():
        with _open_connection() as other:
            other.execute(LINK_ROW, (other_user["id"], f"win-a-{uuid.uuid4().hex[:8]}", other_user["email"]))

    commit_after_first_select["fn"] = other_tab_links_another_google_account
    with pytest.raises(OAuthAccountConflict, match="already linked to another account"):
        db_service.find_or_create_oauth_user(
            provider="google", provider_user_id=f"win-b-{uuid.uuid4().hex[:8]}", email=other_user["email"],
            email_verified=True, name="n", issue_identity=_no_new_identity)
    assert commit_after_first_select["fired"] == 3


def test_unique_violation_is_retried_exactly_once(monkeypatch):
    calls = []

    def always_collides(**kwargs):
        calls.append(kwargs)
        raise psycopg.errors.UniqueViolation("duplicate key value violates unique constraint")

    monkeypatch.setattr(db_service, "_find_or_create_oauth_user_once", always_collides)
    with pytest.raises(psycopg.errors.UniqueViolation):
        db_service.find_or_create_oauth_user(provider="google", provider_user_id="s", email="e@test.com",
                                             email_verified=True, name="n", issue_identity=_no_new_identity)
    assert len(calls) == 2  # 끝없이 다시 시도하지 않는다


# ── Google로 내보내기 전의 입력 한도 ─────────────────────────────────────────

def _state(client, provider: str = "google") -> str:
    res = client.get(f"/api/v1/users/oauth/{provider}/authorize")
    assert res.status_code == 200, res.text
    return res.json()["state"]


OUT_OF_BOUNDS = {
    "code-too-long": ("code", "c" * 2049),
    "state-too-long": ("state", "s" * 2049),
    "redirect-uri-too-long": ("redirect_uri", "http://localhost:5173/" + "r" * 2049),
    "verifier-42": ("code_verifier", "v" * 42),
    "verifier-129": ("code_verifier", "v" * 129),
    "verifier-plus": ("code_verifier", "v" * 42 + "+"),
    "verifier-slash": ("code_verifier", "v" * 42 + "/"),
    "verifier-padding": ("code_verifier", "v" * 42 + "="),
    "verifier-inner-space": ("code_verifier", "v" * 21 + " " + "v" * 21),
    "verifier-inner-newline": ("code_verifier", "v" * 21 + "\n" + "v" * 21),
    "verifier-non-ascii": ("code_verifier", "가" * 43),
    "verifier-huge": ("code_verifier", "v" * 5000),
}


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("field,value", OUT_OF_BOUNDS.values(), ids=OUT_OF_BOUNDS.keys())
def test_callback_rejects_out_of_bounds_input_before_calling_google(oauth_client, fakes, field, value, provider):
    # 회귀: 길이·형식 제한이 없어 1.9 MB짜리 code와 5만 자 code_verifier가 그대로 Google 토큰 엔드포인트로 나갔다.
    # GitHub·네이버 콜백도 같은 모델(OAuthCallbackRequest)로 받는다.
    body = {"code": "code-1", "state": _state(oauth_client, provider), field: value}
    res = oauth_client.post(f"/api/v1/users/oauth/{provider}/callback", json=body)
    assert res.status_code == 422, res.text
    assert [error["loc"] for error in res.json()["detail"]] == [["body", field]]
    assert value not in res.text  # 받은 값을 되돌려 주지 않는다
    assert fakes[provider].calls == []  # 제공자를 부르지 않았다


@pytest.mark.parametrize("verifier,forwarded", [
    (PKCE_VERIFIER, PKCE_VERIFIER), ("A" * 128, "A" * 128), ("a-._~" + "9" * 38, "a-._~" + "9" * 38),
    (f"  {PKCE_VERIFIER}\n", PKCE_VERIFIER),  # 앞뒤 공백은 예전처럼 뗀다
    ("", None), ("   ", None), (None, None),  # 비어 있으면 예전처럼 없는 것으로 본다
], ids=["43", "128", "unreserved-chars", "trimmed", "empty", "blank", "absent"])
def test_callback_forwards_a_well_formed_code_verifier_and_code_at_the_limit(oauth_client, fake_google, verifier,
                                                                              forwarded):
    fake_google.next_user = OAuthUserInfo(provider="google", provider_user_id=f"sub-{uuid.uuid4().hex[:8]}",
                                          email=f"pkce-{uuid.uuid4().hex[:8]}@gmail.com", email_verified=True, name="n")
    body = {"code": "c" * 2048, "state": _state(oauth_client)}
    if verifier is not None:
        body["code_verifier"] = verifier
    res = oauth_client.post(f"{BASE}/callback", json=body)
    assert res.status_code == 200, res.text
    assert fake_google.calls == [{"code": "c" * 2048, "redirect_uri": "http://localhost:5173/auth/google/callback",
                                  "code_verifier": forwarded}]


def test_get_callback_has_the_same_bounds_and_cuts_the_reflected_error(oauth_client, fake_google):
    state = _state(oauth_client)
    for params, field in (({"code": "c" * 2049, "state": state}, "code"),
                          ({"code": "c", "state": "s" * 2049}, "state"),
                          ({"code": "c", "state": state, "code_verifier": "too-short"}, "code_verifier"),
                          ({"code": "c", "state": state, "code_verifier": "v" * 129}, "code_verifier"),
                          ({"code": "c", "state": state, "code_verifier": "v" * 42 + "+"}, "code_verifier")):
        res = oauth_client.get(f"{BASE}/callback", params=params)
        assert res.status_code == 422, (field, res.text)
        assert [error["loc"] for error in res.json()["detail"]] == [["query", field]]
    assert fake_google.calls == []
    # 회귀: error 쿼리 값을 길이 제한 없이 되돌려 줬다 (5,000자 → 5,028자 응답).
    res = oauth_client.get(f"{BASE}/callback", params={"error": "E" * 5000})
    assert res.status_code == 400 and res.json() == {"detail": "OAuth authorization failed: " + "E" * 200}

    fake_google.next_user = OAuthUserInfo(provider="google", provider_user_id=f"sub-{uuid.uuid4().hex[:8]}",
                                          email=f"get-{uuid.uuid4().hex[:8]}@gmail.com", email_verified=True, name="n")
    ok = oauth_client.get(f"{BASE}/callback", params={"code": " c ", "state": state, "code_verifier": PKCE_VERIFIER})
    assert ok.status_code == 200, ok.text
    assert fake_google.calls[-1]["code"] == "c" and fake_google.calls[-1]["code_verifier"] == PKCE_VERIFIER


@pytest.mark.parametrize("challenge", ["c" * 30_000, "c" * 42, "c" * 44, "c" * 42 + "=", "c" * 42 + "+", "c" * 42 + "/",
                                       "c" * 21 + " " + "c" * 21, "가" * 43, " "],
                         ids=["30k", "42", "44", "padding", "plus", "slash", "inner-space", "non-ascii", "blank"])
def test_authorize_rejects_a_malformed_code_challenge(oauth_client, challenge):
    # 회귀: 3만 자 code_challenge가 그대로 Google 인가 주소(30,580자)에 실렸다.
    res = oauth_client.get(f"{BASE}/authorize", params={"code_challenge": challenge, "code_challenge_method": "S256"})
    assert res.status_code == 400 and res.json() == {"detail": "Invalid code_challenge"}


def test_authorize_with_the_request_shape_the_fe_sends(oauth_client):
    # FE는 항상 redirect_uri + code_challenge + code_challenge_method=S256을 보낸다 (FE/src/api/auth.ts).
    redirect_uri = "http://localhost:5173/auth/google/callback"
    for extra in ({"code_challenge_method": "S256"}, {}):  # 방식을 생략해도 S256이다
        res = oauth_client.get(f"{BASE}/authorize",
                               params={"redirect_uri": redirect_uri, "code_challenge": PKCE_CHALLENGE, **extra})
        assert res.status_code == 200, res.text
        query = parse_qs(urlparse(res.json()["authorization_url"]).query)
        assert query["code_challenge"] == [PKCE_CHALLENGE] and query["code_challenge_method"] == ["S256"]
        assert query["redirect_uri"] == [redirect_uri] and query["state"] == [res.json()["state"]]
    plain = oauth_client.get(f"{BASE}/authorize",
                             params={"code_challenge": PKCE_CHALLENGE, "code_challenge_method": "plain"})
    assert plain.status_code == 400 and plain.json() == {"detail": "Only S256 code_challenge_method is supported"}


def test_authorize_without_any_redirect_uri_is_503(oauth_client, monkeypatch):
    # 기본 콜백 주소를 비운 서버(production은 localhost 기본값으로 대신하지 않는다)에 redirect_uri 없이 물으면 503이다.
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "")
    get_settings.cache_clear()
    res = oauth_client.get(f"{BASE}/authorize")
    assert res.status_code == 503 and res.json() == {"detail": "Google OAuth redirect_uri is not configured"}
    # 허용 목록도 비었다: FE가 보낸 주소가 있어도 400 'Invalid redirect_uri'가 아니라 '설정 없음' 503이다
    # (FE는 '<제공자> 로그인을 지금 사용할 수 없습니다'로 보여 준다).
    res = oauth_client.get(f"{BASE}/authorize", params={"redirect_uri": "http://localhost:5173/auth/google/callback"})
    assert res.status_code == 503 and res.json() == {"detail": "Google OAuth redirect_uri is not configured"}


def signed_state(provider: str = "google", /, **claims) -> str:
    """서버와 같은 비밀값으로 서명한 provider용 state (클레임만 바꿔 서버 검사를 본다). 값이 None인 클레임은 뺀다."""
    payload = {"type": "oauth_state", "provider": provider,
               "redirect_uri": CALLBACKS[provider], "nonce": "n1",
               "exp": datetime.now(timezone.utc) + timedelta(minutes=5), **claims}
    return jwt.encode({name: value for name, value in payload.items() if value is not None},
                      get_settings().jwt_secret, algorithm=ALGORITHM)


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("claims", [
    {"type": "access"}, {"provider": "OTHER"}, {"provider": "GOOGLE"}, {"redirect_uri": ""}, {"redirect_uri": 5},
    {"redirect_uri": None}, {"nonce": None}, {"type": None}, {"provider": None}, {"exp": None},
], ids=["other-type", "other-provider", "provider-case", "empty-redirect", "non-string-redirect", "no-redirect",
        "no-nonce", "no-type", "no-provider", "no-exp"])
def test_signed_state_with_wrong_or_missing_claims_is_400_without_calling_google(oauth_client, fakes, claims, provider):
    # 대조: 클레임이 온전한 같은 서명의 state는 state 검사를 지나 그 제공자(가짜)까지 간다.
    fake = fakes[provider]
    callback = f"/api/v1/users/oauth/{provider}/callback"
    fake.error = OAuthCodeError("Invalid or expired OAuth authorization code")
    control = oauth_client.post(callback, json={"code": "c", "state": signed_state(provider)})
    assert control.json() == {"detail": "Invalid or expired OAuth authorization code"} and len(fake.calls) == 1
    fake.calls.clear()
    if claims.get("provider") == "OTHER":  # 다른 제공자에게 발급한 state (서명은 맞다)
        claims = {"provider": next(other for other in PROVIDERS if other != provider)}
    elif claims.get("provider") == "GOOGLE":  # 대소문자만 다른 제공자 이름
        claims = {"provider": provider.upper()}
    res = oauth_client.post(callback, json={"code": "c", "state": signed_state(provider, **claims)})
    assert res.status_code == 400 and res.json() == {"detail": "Invalid or expired OAuth state"}
    assert [f.calls for f in fakes.values()] == [[], [], []]


def test_access_token_is_not_accepted_as_state_and_state_is_not_a_bearer_token(oauth_client, fake_google, register):
    user = register()
    res = oauth_client.post(f"{BASE}/callback", json={"code": "c", "state": user["token"]})
    assert res.status_code == 400 and res.json() == {"detail": "Invalid or expired OAuth state"}
    assert fake_google.calls == []
    me = oauth_client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {_state(oauth_client)}"})
    assert me.status_code == 401


# ── 프로세스 전체의 Google 토큰 교환 한도 ────────────────────────────────────

def test_exchange_budget_is_a_token_bucket():
    now = [1000.0]
    budget = oauth_api.ExchangeBudget(calls_per_minute=15, clock=lambda: now[0])  # 4초에 하나씩 다시 찬다
    assert [budget.reserve() for _ in range(16)] == [None] * 15 + [4]
    now[0] += 2
    assert budget.reserve() == 2  # 거절은 몫을 쓰지 않는다
    now[0] += 2
    assert budget.reserve() is None and budget.reserve() == 4
    now[0] += 3600  # 오래 쉬어도 버킷 크기보다 많이 쌓이지 않는다
    assert [budget.reserve() for _ in range(16)] == [None] * 15 + [4]
    budget.clear()
    assert budget.reserve() is None
    # 운영 값: 프로세스마다 분당 300번 (기업용 조회 한도와 같은 크기)
    assert oauth_api.EXCHANGES_PER_MINUTE == 300 and oauth_api.oauth_exchange_budget.capacity == 300.0


BUDGET_NAMES = {"google": "oauth_exchange_budget", "github": "github_exchange_budget", "naver": "naver_exchange_budget"}


@pytest.mark.parametrize("provider", PROVIDERS)
def test_token_exchange_budget_answers_429_without_calling_google(oauth_client, fakes, monkeypatch, caplog, provider):
    now = [5000.0]
    monkeypatch.setattr(oauth_api, BUDGET_NAMES[provider],
                        oauth_api.ExchangeBudget(calls_per_minute=15, clock=lambda: now[0]))
    state = _state(oauth_client, provider)
    fake = fakes[provider]
    fake.error = OAuthCodeError("Invalid or expired OAuth authorization code")
    callback = f"/api/v1/users/oauth/{provider}/callback"

    def post(code: str, state_value: str = state):
        return oauth_client.post(callback, json={"code": code, "state": state_value})

    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        # 서명이 틀린 state는 제공자를 부르지 않으므로 몫도 쓰지 않는다.
        assert {post("c", "forged.state.value").status_code for _ in range(30)} == {400}
        assert [post(f"garbage-{i}").status_code for i in range(15)] == [400] * 15
        assert len(fake.calls) == 15
        assert audit_lines(caplog) == []  # 몫 안의 요청과 제공자를 부르지 않는 거절은 줄을 남기지 않는다

        for res in (post("garbage-16"),
                    oauth_client.get(callback, params={"code": "garbage-17", "state": state})):
            assert res.status_code == 429 and res.json() == TOO_MANY  # 로그인 한도와 같은 문구 (FE가 이미 번역한다)
            assert res.headers["retry-after"] == "4"
        assert len(fake.calls) == 15  # 한도를 넘은 요청은 제공자에 닿지 않는다

        now[0] += 4
        assert post("garbage-18").status_code == 400 and post("garbage-19").status_code == 429
        assert len(fake.calls) == 16
    # 한도가 거절한 요청마다 감사 로그 한 줄 (INFO, 코드·state·주소 없음): 지표 필터로 그 제공자 로그인의 429를 알 수 있다.
    assert audit_lines(caplog) == [f"event=oauth_exchange_denied retry_after=4 provider={provider}"] * 3
    assert all(record.levelno == logging.INFO for record in caplog.records if record.name == "vc-be.audit")
    # 한도는 제공자마다 따로다: 이 제공자의 몫이 바닥난 동안에도 다른 제공자의 콜백은 제공자까지 간다.
    for other in PROVIDERS:
        if other == provider:
            continue
        fakes[other].error = OAuthCodeError("Invalid or expired OAuth authorization code")
        res = oauth_client.post(f"/api/v1/users/oauth/{other}/callback",
                                json={"code": "c", "state": _state(oauth_client, other)})
        assert res.status_code == 400 and len(fakes[other].calls) == 1, (other, res.text)


def test_callback_flood_reaches_google_at_most_the_budget(client, monkeypatch):
    """감사 재현: state 하나(누구나 받을 수 있고 10분 동안 다시 쓸 수 있다)로 아무 code나 400번 보낸다.

    고치기 전에는 실제 어댑터가 client_secret을 실어 Google 토큰 엔드포인트를 400번 불렀다. 이제 프로세스 한도(분당 300번)
    까지만 부르고 나머지는 429다. 시계는 멈춰 둔다 (테스트가 도는 동안 몫이 다시 차지 않게).
    """
    from main import app

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "fake-google-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:5173/auth/google/callback")
    get_settings.cache_clear()
    outbound: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        outbound.append(request)
        return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad Request"})

    app.dependency_overrides[get_google_oauth_provider] = lambda: GoogleOAuthAdapter(
        client_id="cid.apps.googleusercontent.com", client_secret="fake-google-secret",
        transport=httpx.MockTransport(handler))
    monkeypatch.setattr(oauth_api.oauth_exchange_budget, "_clock", lambda: 0.0)
    oauth_api.oauth_exchange_budget.clear()
    try:
        state = _state(client)
        statuses = [client.post(f"{BASE}/callback", json={"code": f"garbage-{i}", "state": state}).status_code
                    for i in range(300)]
        statuses += [client.get(f"{BASE}/callback", params={"code": f"garbage-get-{i}", "state": state}).status_code
                     for i in range(100)]
        assert statuses == [400] * 300 + [429] * 100
        assert len(outbound) == 300 and {str(request.url) for request in outbound} == {TOKEN_URL}

        oauth_api.oauth_exchange_budget.clear()  # 몫이 다시 찬 뒤에도 한도를 넘는 값은 나가지 않는다
        huge = client.post(f"{BASE}/callback", json={"code": "A" * 1_900_000, "state": state})
        long_code = client.post(f"{BASE}/callback", json={"code": "A" * 5000, "state": state})
        long_verifier = client.post(f"{BASE}/callback",
                                    json={"code": "c", "state": state, "code_verifier": "v" * 5000 + "\n\x00!"})
        assert [huge.status_code, long_code.status_code, long_verifier.status_code] == [413, 422, 422]
        assert len(outbound) == 300
        assert max(len(parse_qs(request.content.decode())["code"][0]) for request in outbound) <= 2048
    finally:
        app.dependency_overrides.pop(get_google_oauth_provider, None)
        get_settings.cache_clear()


# ── GET 콜백은 development 전용 ─────────────────────────────────────────────

@pytest.mark.parametrize("provider", PROVIDERS)
def test_get_callback_is_404_in_production_and_the_post_flow_still_works(client, fakes, production_env,
                                                                          monkeypatch, provider):
    """FE는 POST만 쓴다. GET은 code·state가 URL(접근 로그·ALB 로그)에 남고 다른 사이트에서도 걸 수 있어 운영에서는 닫는다."""
    from main import app

    redirect_uri = f"https://app.example.com/auth/{provider}/callback"
    prefix = provider.upper()
    monkeypatch.setenv(f"{prefix}_CLIENT_ID", f"prod-{provider}-client-id")
    monkeypatch.setenv(f"{prefix}_CLIENT_SECRET", uuid.uuid4().hex + uuid.uuid4().hex)
    monkeypatch.setenv(f"{prefix}_REDIRECT_URI", redirect_uri)
    get_settings.cache_clear()
    assert get_settings().app_env == "production"
    fake = fakes[provider]
    app.dependency_overrides[DEPENDENCIES[provider]] = provide(fake)
    callback = f"/api/v1/users/oauth/{provider}/callback"
    try:
        state = _state(client, provider)
        # 쿼리가 무엇이든(정상·error·없음·한도 초과·형식 오류) 쿼리 검사보다 먼저 404다.
        for params in ({"code": "c", "state": state}, {"error": "access_denied"}, {},
                       {"code": "c" * 5000, "state": state}, {"code": "c", "state": state, "code_verifier": "bad"}):
            res = client.get(callback, params=params)
            assert res.status_code == 404 and res.json() == {"detail": "Not Found"}, (params, res.text)
        assert fake.calls == []

        fake.next_user = OAuthUserInfo(provider=provider, provider_user_id=f"sub-{uuid.uuid4().hex[:8]}",
                                       email=f"prod-{uuid.uuid4().hex[:8]}@example.com", email_verified=True,
                                       name="운영 사용자")
        res = client.post(callback, json={"code": "c", "state": state, "redirect_uri": redirect_uri,
                                          "code_verifier": PKCE_VERIFIER})
        assert res.status_code == 200 and res.json()["access_token"], res.text
        assert fake.calls == [{"code": "c", "redirect_uri": redirect_uri, "code_verifier": PKCE_VERIFIER}]
        assert fake.states == [state]  # 인가 요청에 실은 state를 그대로 넘긴다 (Naver는 토큰 요청에 싣는다)
    finally:
        app.dependency_overrides.pop(DEPENDENCIES[provider], None)
        get_settings.cache_clear()


# ── 어댑터: 접속 방식, 2xx만 결과, 프로필 값 검사·정리, Google 전용 오류 표시 ────────────

PROFILE = {"sub": "google-sub-1", "email": "alice@example.com", "email_verified": True, "name": "Alice"}


def google_double(*, token: httpx.Response | None = None, userinfo: httpx.Response | None = None):
    """토큰·사용자 정보 엔드포인트를 흉내 내는 어댑터와, 받은 요청 목록. 응답을 주지 않으면 정상 응답을 쓴다."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if str(request.url) == TOKEN_URL:
            return token or httpx.Response(200, json={"access_token": "goog-at-1", "token_type": "Bearer"})
        return userinfo or httpx.Response(200, json=PROFILE)

    return _google_adapter(handler), requests


def profile_response(**fields) -> httpx.Response:
    # json.dumps(ASCII 이스케이프)로 직접 만든다: 짝 없는 서로게이트는 "\\ud800" 이스케이프로만 보낼 수 있다.
    return httpx.Response(200, content=json.dumps({**PROFILE, **fields}).encode(),
                          headers={"Content-Type": "application/json"})


async def _exchange(adapter: GoogleOAuthAdapter) -> OAuthUserInfo:
    return await adapter.exchange_code(code="auth-code-1", redirect_uri="http://localhost:5173/auth/google/callback")


async def test_adapter_ignores_proxy_environment_and_does_not_follow_redirects(monkeypatch):
    """회귀: trust_env를 켠 채라 HTTPS_PROXY·SSL_CERT_FILE 환경변수가 client_secret·인가 코드가 실린 요청의 경로와
    신뢰 저장소를 정했다 (DXWorks·AI 어댑터는 이미 꺼 두었다)."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    real_client = httpx.AsyncClient
    # 대조: 환경변수를 따르는 클라이언트(운영처럼 전송 계층을 따로 주지 않음)는 프록시 경로를 만든다.
    # 클라이언트를 만들기만 하고 아무것도 보내지 않는다.
    control = real_client()
    assert control.trust_env is True and control._mounts
    await control.aclose()

    seen: list[dict] = []

    class Recording(real_client):
        def __init__(self, *args, **kwargs):
            assert not args
            seen.append(dict(kwargs))
            super().__init__(**kwargs)

    monkeypatch.setattr(oauth_adapter.httpx, "AsyncClient", Recording)
    adapter, requests = google_double(token=httpx.Response(302, headers={"Location": "https://evil.example/token"},
                                                           json={"access_token": "goog-at-1"}))
    with pytest.raises(ProviderError) as error:
        await _exchange(adapter)
    assert error.value.upstream_status == 302
    assert [str(request.url) for request in requests] == [TOKEN_URL]  # 리다이렉트를 따라가지 않았다
    [kwargs] = seen
    assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
    # 어댑터가 넘긴 인자 그대로 운영처럼(전송 계층 없이) 만든 클라이언트는 환경변수의 프록시를 쓰지 않는다.
    production_like = real_client(**{**kwargs, "transport": None})
    assert production_like.trust_env is False and production_like._mounts == {}
    await production_like.aclose()


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 399])
async def test_adapter_accepts_only_2xx_from_both_endpoints(status):
    # 회귀: 400 미만이면 성공으로 봐서, access_token이 든 302 응답으로 로그인이 됐다 (DXWorks 어댑터는 2xx만 결과다).
    adapter, requests = google_double(token=httpx.Response(status, json={"access_token": "goog-at-1"}))
    with pytest.raises(ProviderError) as error:
        await _exchange(adapter)
    assert error.value.upstream_status == status and error.value.service == "google_oauth"
    assert len(requests) == 1  # 사용자 정보는 묻지 않았다

    adapter, requests = google_double(userinfo=httpx.Response(status, json=PROFILE))
    with pytest.raises(ProviderError) as error:
        await _exchange(adapter)
    assert error.value.upstream_status == status and error.value.service == "google_oauth"


async def test_adapter_still_accepts_2xx_and_maps_userinfo_401_to_a_code_error():
    adapter, _ = google_double(token=httpx.Response(201, json={"access_token": "goog-at-1"}))
    assert (await _exchange(adapter)).provider_user_id == "google-sub-1"
    adapter, _ = google_double(userinfo=httpx.Response(401, json={"error": "invalid_token"}))
    with pytest.raises(OAuthCodeError, match="^Invalid OAuth access token$"):
        await _exchange(adapter)


DEEP_JSON = b'{"a":' * 200_000 + b"1" + b"}" * 200_000


@pytest.mark.parametrize("endpoint", ["token", "userinfo"])
async def test_adapter_treats_deeply_nested_json_as_a_bad_body(endpoint):
    # 회귀: 아주 깊게 중첩된 본문은 RecursionError → 500이었다. JSON이 아닌 본문처럼 502(제공자 오류)다.
    adapter, _ = google_double(**{endpoint: httpx.Response(200, content=DEEP_JSON)})
    with pytest.raises(ProviderError) as error:
        await _exchange(adapter)
    assert error.value.upstream_status == 200 and error.value.service == "google_oauth"


FAILING_GOOGLE = {
    "token-500": ({"token": httpx.Response(500, json={"error": "server_error"})}, 500),
    "token-403": ({"token": httpx.Response(403, json={"error": "access_denied"})}, 403),
    "token-400-other-error": ({"token": httpx.Response(400, json={"error": "invalid_client"})}, 400),
    "token-without-access-token": ({"token": httpx.Response(200, json={"token_type": "Bearer"})}, 200),
    "token-not-json": ({"token": httpx.Response(200, content=b"<html>")}, 200),
    "token-access-token-not-a-string": ({"token": httpx.Response(200, json={"access_token": 7})}, 200),
    "token-access-token-empty": ({"token": httpx.Response(200, json={"access_token": ""})}, 200),
    # Authorization 헤더에 넣을 수 없는 토큰: 요청을 만들다 500이 나지 않고 제공자 오류다.
    "token-access-token-non-ascii": ({"token": httpx.Response(200, json={"access_token": "토큰-ya29"})}, 200),
    "token-access-token-newline": ({"token": httpx.Response(200, json={"access_token": "ya29\r\nX-Injected: 1"})}, 200),
    "userinfo-500": ({"userinfo": httpx.Response(500, json={})}, 500),
    "userinfo-not-an-object": ({"userinfo": httpx.Response(200, json=["sub", "email"])}, 200),
    "userinfo-empty-object": ({"userinfo": httpx.Response(200, json={})}, 200),
}


@pytest.mark.parametrize("responses,upstream", FAILING_GOOGLE.values(), ids=FAILING_GOOGLE.keys())
async def test_adapter_failures_are_marked_as_google_oauth_errors(responses, upstream):
    adapter, _ = google_double(**responses)
    with pytest.raises(ProviderError) as error:
        await _exchange(adapter)
    assert error.value.service == "google_oauth" and error.value.upstream_status == upstream
    assert error.value.unavailable is False


async def test_adapter_network_and_configuration_errors_are_marked_as_google_oauth_errors():
    def down(request: httpx.Request):
        raise httpx.ConnectError("network down")

    def userinfo_down(request: httpx.Request):
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "goog-at-1"})
        raise httpx.ReadTimeout("slow")

    for handler in (down, userinfo_down):
        with pytest.raises(ProviderError) as error:
            await _exchange(_google_adapter(handler))
        assert error.value.service == "google_oauth" and error.value.upstream_status is None
    with pytest.raises(ProviderError) as error:
        await _exchange(_google_adapter(down, client_id="", client_secret=""))
    assert error.value.service == "google_oauth" and error.value.unavailable is True
    assert ProviderError("x").service == "credential"  # 발급·검증 인프라(DXWorks) 오류의 기본 표시는 그대로다


@pytest.mark.parametrize("error_code,logged", [("redirect_uri_mismatch", True), ("invalid_request", True),
                                               ("invalid_grant", False)])
async def test_adapter_logs_setup_like_token_errors_without_values(caplog, error_code, logged):
    # redirect_uri_mismatch·invalid_request는 사용자 탓이 아니라 서버 설정 문제일 수 있는데 400만 나가고 로그가 없었다.
    adapter, _ = google_double(token=httpx.Response(400, json={
        "error": error_code, "error_description": "redirect https://private.example/cb does not match"}))
    with caplog.at_level(logging.WARNING, logger="app.integrations.oauth"):
        with pytest.raises(OAuthCodeError, match="^Invalid or expired OAuth authorization code$"):
            await _exchange(adapter)
    lines = [r.getMessage() for r in caplog.records if r.name == "app.integrations.oauth"]
    assert lines == ([f"Google OAuth token request rejected: error={error_code}"] if logged else [])


BAD_PROFILES = {
    "sub-number": {"sub": 1234567890},
    "sub-object": {"sub": {"id": "x"}},
    "sub-missing": {"sub": None},
    "sub-blank": {"sub": "  "},
    "sub-256": {"sub": "s" * 256},
    "sub-newline": {"sub": "a\nb"},
    "sub-surrogate": {"sub": "a\ud800b"},
    "email-number": {"email": 5},
    "email-object": {"email": {"address": "x@test.com"}},
    "email-no-at": {"email": "not-an-email"},
    "email-no-local-part": {"email": "@example.com"},
    "email-no-domain": {"email": "alice@"},
    "email-newline": {"email": "a\nb@test.com"},
    "email-tab": {"email": "a\tb@test.com"},
    "email-line-separator": {"email": "a b@test.com"},
    "email-nul": {"email": "a\x00b@test.com"},
    "email-surrogate": {"email": "a\ud800b@test.com"},
    "email-256": {"email": "a" * 246 + "@gmail.com"},
    # 'İ'(U+0130)는 소문자로 바꾸면 두 글자가 된다: 133자 → 260자
    "email-grows-when-lowercased": {"email": "x@" + "\N{LATIN CAPITAL LETTER I WITH DOT ABOVE}" * 127 + ".com"},
}


@pytest.mark.parametrize("fields", BAD_PROFILES.values(), ids=BAD_PROFILES.keys())
async def test_adapter_rejects_unusable_sub_or_email_as_a_provider_error(fields):
    # 회귀: 길이를 넘는 sub·email은 DB 단계의 500이었고, 객체로 온 email은 그 repr이, '@' 없는 값도 그대로 계정이 됐다.
    adapter, _ = google_double(userinfo=profile_response(**fields))
    with pytest.raises(ProviderError) as error:
        await _exchange(adapter)
    assert error.value.service == "google_oauth" and error.value.upstream_status == 200
    assert error.value.message == "Google 사용자 정보의 필수 필드(sub, email)가 없거나 형식이 올바르지 않습니다"  # 값은 싣지 않는다


@pytest.mark.parametrize("fields", [{"email": None}, {"email": ""}, {"email": "  \n"}],
                         ids=["email-null", "email-empty", "email-blank"])
async def test_adapter_reports_a_missing_email_as_oauth_email_missing(fields):
    # 이메일이 없으면(없음·null·빈 문자열) 제공자 오류(502)가 아니라 이메일 제공 동의를 안내할 400이다 (api/oauth.py).
    from app.integrations.ports import OAuthEmailMissing

    for response in (profile_response(**fields),
                     httpx.Response(200, json={key: value for key, value in PROFILE.items() if key != "email"})):
        adapter, _ = google_double(userinfo=response)
        with pytest.raises(OAuthEmailMissing):
            await _exchange(adapter)


async def test_adapter_accepts_sub_and_email_at_the_column_limit():
    email = "A" * 245 + "@Gmail.com"  # 255자
    adapter, _ = google_double(userinfo=profile_response(sub=" " + "9" * 255 + "\n", email=f" {email} "))
    info = await _exchange(adapter)
    assert info.provider_user_id == "9" * 255 and info.email == email.lower() and len(info.email) == 255
    assert info.email_verified is True and info.name == "Alice"


_MISSING = object()
NOT_VERIFIED = {"false": False, "null": None, "missing": _MISSING, "string-true": "true", "string-True": "True",
                "string-false": "false", "number-1": 1, "string-1": "1", "list": [True], "object": {"value": True}}


def unverified_profile(value) -> httpx.Response:
    fields = {key: item for key, item in PROFILE.items() if key != "email_verified"}
    if value is not _MISSING:
        fields["email_verified"] = value
    return httpx.Response(200, json=fields)


@pytest.mark.parametrize("value", NOT_VERIFIED.values(), ids=NOT_VERIFIED.keys())
async def test_adapter_treats_only_the_json_boolean_true_as_a_verified_email(value):
    """email_verified는 JSON 불리언 true일 때만 인증된 이메일이다. 계정 연동 규칙(새 계정을 만들거나 409로 거절)이 이 값에
    기대므로, 문자열 "true"·"false"나 숫자 1을 참으로 읽는 구현(bool(...) 등)으로 바뀌면 여기서 걸린다."""
    adapter, _ = google_double(userinfo=unverified_profile(value))
    info = await _exchange(adapter)
    assert info.email_verified is False
    assert (info.provider_user_id, info.email) == ("google-sub-1", "alice@example.com")  # 나머지 값은 그대로 읽는다
    adapter, _ = google_double(userinfo=unverified_profile(True))
    assert (await _exchange(adapter)).email_verified is True


@pytest.mark.parametrize("value", ["true", 1], ids=["string-true", "number-1"])
def test_login_with_an_email_google_did_not_verify_as_boolean_true_is_400_and_creates_nothing(client, monkeypatch, db,
                                                                                              value):
    from main import app

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csec")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:5173/auth/google/callback")
    get_settings.cache_clear()
    email, sub = f"unverified-{uuid.uuid4().hex[:8]}@gmail.com", f"sub-unverified-{uuid.uuid4().hex[:8]}"
    profile = httpx.Response(200, json={"sub": sub, "email": email, "email_verified": value, "name": "미인증"})
    adapter, requests = google_double(userinfo=profile)  # 실제 어댑터 + 가짜 Google (MockTransport)
    app.dependency_overrides[get_google_oauth_provider] = lambda: adapter
    try:
        res = client.post(f"{BASE}/callback", json={"code": "c", "state": _state(client)})
    finally:
        app.dependency_overrides.pop(get_google_oauth_provider, None)
        get_settings.cache_clear()
    assert res.status_code == 400 and res.json() == {"detail": "OAuth email is not verified"}
    assert [str(request.url) for request in requests] == [TOKEN_URL, USERINFO_URL]  # Google까지 다녀온 뒤의 거절이다
    assert db.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone() is None
    assert db.execute("SELECT 1 FROM oauth_accounts WHERE provider_user_id = %s", (sub,)).fetchone() is None


CLEANED_NAMES = [
    ("Line1\nLine2 # heading", "Line1 Line2 # heading"),
    ("홍\t길동\x1b[31m 관리자\r\n끝", "홍 길동 [31m 관리자 끝"),
    ("  Alice   Kim  ", "Alice Kim"),
    ("a\x00b", "a b"),
    ("a\ud800b", "a b"),
    ("문단 구분", "문단 구분"),
    ("김 철수", "김 철수"),
    ("가" * 250, "가" * 100),
    ("가" * 99 + " 나다", "가" * 99),  # 100자로 자른 끝의 공백도 뗀다
    ("abc‮def", "abc‮def"),  # 서식 문자(Cf)는 가입 API처럼 그대로 둔다
    ("", "alice"), ("   ", "alice"), ("\n\t ", "alice"), (None, "alice"), ({"first": "A"}, "alice"), (42, "alice"),
]


@pytest.mark.parametrize("raw,expected", CLEANED_NAMES, ids=[f"name-{index}" for index in range(len(CLEANED_NAMES))])
async def test_adapter_cleans_the_display_name_instead_of_rejecting_it(raw, expected):
    # 회귀: 가입 API는 한 줄이 아닌 이름을 422로 거절하는데 Google 프로필 이름은 줄바꿈·ESC가 든 채로 저장됐다.
    adapter, _ = google_double(userinfo=profile_response(name=raw))
    info = await _exchange(adapter)
    assert info.name == expected
    assert info.provider_user_id == "google-sub-1" and info.email == "alice@example.com"


async def test_adapter_cuts_the_local_part_fallback_to_the_name_column():
    adapter, _ = google_double(userinfo=profile_response(name="", email="l" * 150 + "@example.com"))
    assert (await _exchange(adapter)).name == "l" * 100


# ── API: Google 실패는 Google 문구로 답한다 (상태 코드·code·로그 줄은 그대로) ─────────────

def test_google_failure_is_502_with_its_own_wording_and_one_provider_error_line(client, monkeypatch, caplog):
    from main import app

    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "csec")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "http://localhost:5173/auth/google/callback")
    get_settings.cache_clear()

    def down(request: httpx.Request):
        raise httpx.ConnectError("network down")

    app.dependency_overrides[get_google_oauth_provider] = lambda: _google_adapter(down)
    try:
        state = _state(client)
        with caplog.at_level(logging.WARNING, logger="vc-be"):
            res = client.post(f"{BASE}/callback", json={"code": "c", "state": state})
    finally:
        app.dependency_overrides.pop(get_google_oauth_provider, None)
        get_settings.cache_clear()
    # 회귀: 502 {"detail": "Credential service error"}로 답해 FE가 '배지 발급·검증 서비스' 안내를 보여 줬다.
    assert res.status_code == 502 and res.json() == GOOGLE_ERROR
    assert "Credential" not in res.text and "ConnectError" not in res.text
    # 운영 지표 필터가 보는 'Provider error' 줄은 한 줄 그대로다.
    assert [r.getMessage() for r in caplog.records if r.name == "vc-be"] == [
        "Provider error: Google OAuth 연결 실패: ConnectError (upstream=None)"]


async def test_provider_error_wording_is_chosen_by_service_and_defaults_to_credential():
    from main import provider_error_handler

    async def body(error: ProviderError) -> tuple[int, dict]:
        response = await provider_error_handler(None, error)
        return response.status_code, json.loads(response.body)

    assert await body(ProviderError("x")) == (502, {"detail": "Credential service error", "code": "PROVIDER_ERROR"})
    assert await body(ProviderError("x", unavailable=True)) == (
        503, {"detail": "Credential service is not configured", "code": "PROVIDER_NOT_CONFIGURED"})
    assert await body(ProviderError("x", service="google_oauth")) == (502, GOOGLE_ERROR)
    assert await body(ProviderError("x", service="google_oauth", unavailable=True)) == (503, GOOGLE_NOT_CONFIGURED)
    for name, label in (("github", "GitHub"), ("naver", "Naver")):
        assert await body(ProviderError("x", service=f"{name}_oauth")) == (
            502, {"detail": f"{label} OAuth error", "code": "PROVIDER_ERROR"})
        assert await body(ProviderError("x", service=f"{name}_oauth", unavailable=True)) == (
            503, {"detail": f"{label} OAuth is not configured", "code": "PROVIDER_NOT_CONFIGURED"})
    # 모르는 표시는 발급·검증 인프라 문구로 답한다 (오류를 내지 않는다).
    assert await body(ProviderError("x", service="something-new")) == (
        502, {"detail": "Credential service error", "code": "PROVIDER_ERROR"})

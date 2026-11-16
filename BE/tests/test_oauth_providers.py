"""GitHub·네이버 OAuth 2.0 어댑터와 제공자 사이의 계정 연동 규칙 (MockTransport / 가짜 제공자 + 실제 PostgreSQL).

GitHub(github.com·api.github.com)·네이버(nid.naver.com·openapi.naver.com)에는 한 번도 요청하지 않는다: 어댑터는
httpx.MockTransport로, API는 tests/test_oauth.py의 가짜 제공자(또는 MockTransport를 단 실제 어댑터)로 시험한다.
값은 모두 가상의 값이다.
- 어댑터: 인가 주소 매개변수(GitHub은 scope user:email + PKCE, prompt 등은 싣지 않음, 네이버는 PKCE를 싣지 않음), 토큰 요청
  값은 본문에만(client_secret·state가 주소에 실리지 않음), GitHub 토큰 요청의 Accept: application/json, GitHub 토큰 오류는
  HTTP 200 + error(코드 오류 400·기본 이메일 미인증 400·그 밖 502), REST 헤더(User-Agent·Accept·API 버전·Bearer),
  GitHub id는 양의 JSON 정수만, 이메일 목록에서 primary(JSON true) 항목만 쓰고 verified(JSON true)일 때만 인증된 이메일,
  이메일 없음·권한 없음(403·404) → OAuthEmailMissing(400), 네이버의 HTTP 200 + error, resultcode, 401, JSON이 아닌 본문,
  리다이렉트를 따라가지 않음, 오류 표시(github_oauth·naver_oauth).
- 계정 연동 규칙: 같은 이메일의 비밀번호 없는 계정에는, 이번 제공자가 이메일을 인증했고(Google·GitHub) 그 계정의 이메일이
  인증된 제공자 연동으로 확인된 주소일 때만 붙인다. 네이버 로그인은 다른 계정에 붙지 않고, 네이버로 만든 계정에도 붙지 않는다.
"""
import json
import logging
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.api import oauth as oauth_api
from app.core.config import get_settings
from app.integrations import oauth as oauth_adapter
from app.integrations.oauth import GitHubOAuthAdapter, GoogleOAuthAdapter, NaverOAuthAdapter
from app.integrations.ports import (
    OAuthCodeError, OAuthEmailMissing, OAuthEmailUnverified, OAuthUserInfo, ProviderError,
)
from app.services.database import (
    EMAIL_VERIFYING_OAUTH_PROVIDERS, OAUTH_OTHER_METHOD_ACCOUNT, OAUTH_PASSWORD_ACCOUNT, OAuthUnverifiedLinkConflict,
    db_service,
)
from tests.test_oauth import (  # noqa: F401  (fakes·fake_google·oauth_client는 이 파일에서도 쓰는 픽스처다)
    CALLBACKS, DEPENDENCIES, PKCE_CHALLENGE, PKCE_VERIFIER, PROVIDERS, audit_lines, fake_google, fakes,
    insert_passwordless_user, oauth_client, provide, social_login,
)

GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails?per_page=100"
NAVER_TOKEN_URL = "https://nid.naver.com/oauth2.0/token"
NAVER_USERINFO_URL = "https://openapi.naver.com/v1/nid/me"
# GET /user: email은 공개 프로필 이메일이라 쓰지 않는다 (이메일 목록의 기본 이메일과 다르게 둔다).
GITHUB_USER = {"login": "octo-kim", "id": 4242424242, "node_id": "U_kgDOtestonly", "type": "User",
               "name": "깃허브친구", "email": "public-profile@example.com"}
# GET /user/emails: 기본 이메일이 첫 항목이 아니다 (첫 항목은 인증됐지만 기본이 아니다).
GITHUB_PRIMARY = {"email": "Kim@GitHub-Mail.com", "primary": True, "verified": True, "visibility": "private"}
GITHUB_EMAILS = [
    {"email": "Old@Example.com", "primary": False, "verified": True, "visibility": None},
    GITHUB_PRIMARY,
    {"email": "4242424242+octo-kim@users.noreply.github.com", "primary": False, "verified": True, "visibility": None},
]
# Accept: application/json일 때의 토큰 응답 (새 OAuth App은 만료 토큰이 기본이라 refresh_token 등이 함께 온다).
GITHUB_TOKEN = {"access_token": "at-1", "token_type": "bearer", "scope": "user:email", "expires_in": 28800,
                "refresh_token": "ghr-refresh-only-for-test", "refresh_token_expires_in": 15897600}
# GitHub REST 요청마다 실어야 하는 헤더
GITHUB_REST_HEADERS = {"authorization": "Bearer at-1", "accept": "application/vnd.github+json",
                       "x-github-api-version": "2022-11-28", "user-agent": "vc-career-agent-be"}
NAVER_PROFILE = {"resultcode": "00", "message": "success",
                 "response": {"id": "AbC-naver_Id_123", "email": "Lee@Naver.com", "name": "이네이버",
                              "nickname": "네이버닉"}}
PROFILES = {"github": GITHUB_USER, "naver": NAVER_PROFILE}
ADAPTERS = {"google": GoogleOAuthAdapter, "github": GitHubOAuthAdapter, "naver": NaverOAuthAdapter}
TOKEN_URLS = {"github": GITHUB_TOKEN_URL, "naver": NAVER_TOKEN_URL}
CODE_ERROR = "Invalid or expired OAuth authorization code"
NOT_VERIFIED = {"detail": "OAuth email is not verified"}
EMAIL_REQUIRED = {"detail": "OAuth email is required"}
REDIRECT = {"github": CALLBACKS["github"], "naver": CALLBACKS["naver"]}


def github_token_endpoint(request: httpx.Request, access_token: str = "at-1") -> httpx.Response:
    """GitHub 토큰 엔드포인트처럼 답한다: 응답 형식은 Accept 헤더를 따르고, 없으면 form-encoded다 (문서의 기본값)."""
    if request.headers.get("accept") == "application/json":
        return httpx.Response(200, json={**GITHUB_TOKEN, "access_token": access_token})
    return httpx.Response(200, text=f"access_token={access_token}&scope=user%3Aemail&token_type=bearer",
                          headers={"Content-Type": "application/x-www-form-urlencoded"})


def double(provider: str, *, token: httpx.Response | None = None, userinfo: httpx.Response | None = None,
           emails: httpx.Response | None = None, client_id: str = "cid", client_secret: str = "csec-0123456789"):
    """토큰(POST)·사용자 정보(GET, GitHub은 /user와 /user/emails)를 흉내 내는 실제 어댑터와, 받은 요청 목록.
    응답을 주지 않으면 정상 응답."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            if token is not None:
                return token
            if provider == "github":
                return github_token_endpoint(request)
            return httpx.Response(200, json={"access_token": "at-1", "token_type": "bearer", "expires_in": "3600"})
        if provider == "github" and request.url.path == "/user/emails":
            return emails or httpx.Response(200, json=GITHUB_EMAILS)
        return userinfo or httpx.Response(200, json=PROFILES[provider])

    adapter = ADAPTERS[provider](client_id=client_id, client_secret=client_secret,
                                 transport=httpx.MockTransport(handler))
    return adapter, requests


async def exchange(adapter, *, code_verifier: str | None = PKCE_VERIFIER, state: str | None = "state-jwt-1"):
    return await adapter.exchange_code(code="auth-code-1", redirect_uri=REDIRECT[adapter.provider_name],
                                       code_verifier=code_verifier, state=state)


def form_of(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode(), keep_blank_values=True)


def _without_drops(values: dict) -> dict:
    return {key: value for key, value in values.items() if value is not _DROP}


def with_user(**fields) -> httpx.Response:
    """GitHub GET /user: 필드만 바꾼다 (값이 _DROP이면 뺀다)."""
    return httpx.Response(200, json=_without_drops({**GITHUB_USER, **fields}))


def primary(**fields) -> dict:
    """GitHub 이메일 목록의 기본 이메일 항목: 필드만 바꾼다 (값이 _DROP이면 뺀다)."""
    return _without_drops({**GITHUB_PRIMARY, **fields})


def with_emails(*entries) -> httpx.Response:
    """GitHub GET /user/emails: 주어진 항목들의 목록."""
    return httpx.Response(200, json=list(entries))


def with_naver(**fields) -> httpx.Response:
    """네이버 사용자 정보: response의 필드만 바꾼다 (값이 _DROP이면 뺀다)."""
    profile = {**NAVER_PROFILE["response"], **fields}
    profile = {key: value for key, value in profile.items() if value is not _DROP}
    return httpx.Response(200, json={**NAVER_PROFILE, "response": profile})


def requests_until_userinfo(provider: str) -> int:
    """정상 흐름의 요청 수: GitHub은 토큰·/user·/user/emails 셋, 네이버는 토큰·프로필 둘."""
    return 3 if provider == "github" else 2


_DROP = object()


# ── 경로 ─────────────────────────────────────────────────────────

def test_oauth_routes_are_three_static_paths_per_provider():
    """경로 변수('/{provider}')가 아니라 제공자마다 고정 경로 셋이다: 본문 상한·접근 로그 가리기·WAF 규칙이 정확한 경로로 맞춘다.
    Google 경로의 이름(OpenAPI operationId)은 예전 함수 이름 그대로다."""
    from main import app
    from tests.test_auth_security import route_inventory

    assert PROVIDERS == ("google", "github", "naver")
    routes = sorted((method, path) for method, path in route_inventory() if path.startswith("/api/v1/users/oauth"))
    assert routes == sorted((method, f"/api/v1/users/oauth/{provider}/{action}") for provider in PROVIDERS
                            for method, action in (("GET", "authorize"), ("GET", "callback"), ("POST", "callback")))
    operations = {(method.upper(), path): operation["operationId"]
                  for path, item in app.openapi()["paths"].items() if path.startswith("/api/v1/users/oauth")
                  for method, operation in item.items()}
    assert operations == {
        (method, f"/api/v1/users/oauth/{provider}/{action}"):
            f"{provider}_{name}_api_v1_users_oauth_{provider}_{action}_{method.lower()}"
        for provider in PROVIDERS
        for method, action, name in (("GET", "authorize", "authorize"), ("GET", "callback", "callback_get"),
                                     ("POST", "callback", "callback_post"))}
    # 문서(OpenAPI)의 경로 순서는 FE의 버튼 순서와 같다: Google, GitHub, Naver.
    order = [path.split("/")[5] for path in app.openapi()["paths"] if path.startswith("/api/v1/users/oauth")]
    assert list(dict.fromkeys(order)) == ["google", "github", "naver"]


# ── 어댑터 속성 ─────────────────────────────────────────────────────

def test_adapter_attributes_and_the_email_verifying_provider_list_agree():
    assert [(a.provider_name, a.label, a.supports_pkce, a.requires_verified_email) for a in ADAPTERS.values()] == [
        ("google", "Google", True, True), ("github", "GitHub", True, True), ("naver", "Naver", False, False)]
    # 계정 연동 규칙(database.py)이 '확인된 이메일'로 보는 제공자 = 이메일을 인증한 것만 받는 제공자
    assert EMAIL_VERIFYING_OAUTH_PROVIDERS == ("google", "github")
    assert set(EMAIL_VERIFYING_OAUTH_PROVIDERS) == {
        a.provider_name for a in ADAPTERS.values() if a.requires_verified_email}
    assert {name: ADAPTERS[name](client_id="c", client_secret="s").service for name in PROVIDERS} == {
        "google": "google_oauth", "github": "github_oauth", "naver": "naver_oauth"}
    # 토큰 요청 헤더를 더하는 제공자는 GitHub뿐이다 (Google·Naver의 요청은 예전과 같다).
    assert {name: adapter.token_headers for name, adapter in ADAPTERS.items()} == {
        "google": {}, "github": {"Accept": "application/json"}, "naver": {}}


def test_adapters_read_their_own_settings_and_the_shared_timeout(monkeypatch):
    for name in PROVIDERS:
        monkeypatch.setenv(f"{name.upper()}_CLIENT_ID", f" {name}-id ")
        monkeypatch.setenv(f"{name.upper()}_CLIENT_SECRET", f"{name}-secret")
    monkeypatch.setenv("OAUTH_TIMEOUT_SECONDS", "7")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        for name, adapter_class in ADAPTERS.items():
            adapter = adapter_class.from_settings(settings)
            assert (adapter.client_id, adapter.client_secret, adapter.timeout) == (f"{name}-id", f"{name}-secret", 7.0)
    finally:
        get_settings.cache_clear()


def test_each_provider_has_its_own_exchange_budget():
    budgets = {name: oauth_api.exchange_budget(name) for name in PROVIDERS}
    assert budgets["github"] is oauth_api.github_exchange_budget
    assert len({id(budget) for budget in budgets.values()}) == 3  # 한 제공자의 몫이 바닥나도 다른 제공자는 그대로다
    assert all(budget.capacity == 300.0 for budget in budgets.values())


# ── 인가 주소 ─────────────────────────────────────────────────────

def test_github_authorize_url_carries_scope_and_pkce_but_no_prompt():
    adapter, requests = double("github")
    url = adapter.build_authorize_url(state="st-1", redirect_uri=REDIRECT["github"], code_challenge=PKCE_CHALLENGE,
                                      code_challenge_method="S256")
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "github.com", "/login/oauth/authorize")
    # allow_signup·prompt·login·response_type은 싣지 않는다. scope는 이메일 목록을 읽는 user:email 하나다.
    assert parse_qs(parsed.query) == {"client_id": ["cid"], "redirect_uri": [REDIRECT["github"]],
                                      "scope": ["user:email"], "state": ["st-1"],
                                      "code_challenge": [PKCE_CHALLENGE], "code_challenge_method": ["S256"]}
    assert "csec" not in url and requests == []  # 시크릿은 인가 주소에 없고, 인가 주소를 만들 때는 아무것도 부르지 않는다
    # FE가 code_challenge를 보내지 않으면 PKCE 매개변수도 없다.
    plain = parse_qs(urlparse(adapter.build_authorize_url(state="st-1", redirect_uri=REDIRECT["github"])).query)
    assert set(plain) == {"client_id", "redirect_uri", "scope", "state"}


def test_naver_authorize_url_never_carries_pkce():
    adapter, _ = double("naver")
    url = adapter.build_authorize_url(state="st-1", redirect_uri=REDIRECT["naver"], code_challenge=PKCE_CHALLENGE,
                                      code_challenge_method="S256")
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "nid.naver.com", "/oauth2.0/authorize")
    assert parse_qs(parsed.query) == {"response_type": ["code"], "client_id": ["cid"],
                                      "redirect_uri": [REDIRECT["naver"]], "state": ["st-1"]}
    assert "csec" not in url  # 시크릿은 인가 주소에 없다


@pytest.mark.parametrize("provider", ["github", "naver"])
def test_unconfigured_adapter_is_unavailable_with_its_own_service_tag(provider):
    for client_id, client_secret in (("", ""), ("cid", ""), ("", "csec"), ("  ", " ")):
        adapter, requests = double(provider, client_id=client_id, client_secret=client_secret)
        with pytest.raises(ProviderError) as error:
            adapter.build_authorize_url(state="s", redirect_uri=REDIRECT[provider])
        assert error.value.unavailable is True and error.value.service == f"{provider}_oauth"
        assert error.value.message == f"{adapter.label} OAuth is not configured"
        assert requests == []


# ── 토큰 요청: 값은 본문에만 ─────────────────────────────────────────

async def test_github_token_request_and_profile():
    adapter, requests = double("github")
    info = await exchange(adapter)
    token, user, emails = requests
    # 토큰 요청: 값은 모두 본문에만 (주소에 client_secret·코드가 없다), JSON 응답을 받으려고 Accept: application/json.
    assert (token.method, str(token.url)) == ("POST", GITHUB_TOKEN_URL) and token.url.query == b""
    assert "csec" not in str(token.url) and "auth-code-1" not in str(token.url)
    assert token.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert token.headers["accept"] == "application/json"
    assert form_of(token) == {"client_id": ["cid"], "client_secret": ["csec-0123456789"], "code": ["auth-code-1"],
                              "redirect_uri": [REDIRECT["github"]], "code_verifier": [PKCE_VERIFIER]}
    # 사용자 정보: 같은 토큰으로 /user와 /user/emails(첫 페이지 100개), REST 헤더 넷을 모두 싣는다.
    assert [(r.method, str(r.url)) for r in (user, emails)] == [("GET", GITHUB_USER_URL), ("GET", GITHUB_EMAILS_URL)]
    for request in (user, emails):
        assert {name: request.headers.get(name) for name in GITHUB_REST_HEADERS} == GITHUB_REST_HEADERS
        assert request.content == b""
    # 이메일은 목록의 기본 이메일(첫 항목이 아니다)이다. /user의 공개 프로필 이메일은 쓰지 않는다.
    assert info == OAuthUserInfo(provider="github", provider_user_id="4242424242", email="kim@github-mail.com",
                                 email_verified=True, name="깃허브친구")
    # code_verifier가 없으면 싣지 않는다.
    adapter, requests = double("github")
    await exchange(adapter, code_verifier=None)
    assert set(form_of(requests[0])) == {"client_id", "client_secret", "code", "redirect_uri"}


async def test_github_token_without_the_json_accept_header_would_be_form_encoded():
    # 대조: GitHub 토큰 엔드포인트는 Accept 헤더가 없으면 form-encoded로 답한다 (흉내 낸 응답이 그 동작을 따르는지 확인).
    # 어댑터는 Accept: application/json을 보내므로 이 응답을 받지 않는다. 받으면 JSON이 아니라 502다.
    form_body = github_token_endpoint(httpx.Request("POST", GITHUB_TOKEN_URL))
    assert form_body.headers["content-type"] == "application/x-www-form-urlencoded"
    adapter, requests = double("github", token=form_body)
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == "github_oauth" and len(requests) == 1


async def test_naver_token_request_sends_the_secret_and_state_only_in_the_body():
    """네이버 문서의 예시는 모든 값을 주소(쿼리)에 싣는다. 주소는 요청 로그(httpx 요청 줄·프록시)에 남으므로 본문에만 싣는다."""
    adapter, requests = double("naver")
    info = await exchange(adapter, state="state-jwt-1")
    token, userinfo = requests
    assert (token.method, str(token.url)) == ("POST", NAVER_TOKEN_URL) and token.url.query == b""
    assert "csec" not in str(token.url) and "state-jwt-1" not in str(token.url)
    assert token.headers["content-type"].startswith("application/x-www-form-urlencoded")
    # PKCE·redirect_uri는 이 엔드포인트의 매개변수가 아니다 (FE가 code_verifier를 보내도 싣지 않는다).
    assert form_of(token) == {"grant_type": ["authorization_code"], "client_id": ["cid"],
                              "client_secret": ["csec-0123456789"], "code": ["auth-code-1"],
                              "state": ["state-jwt-1"]}
    assert (userinfo.method, str(userinfo.url)) == ("GET", NAVER_USERINFO_URL)
    assert userinfo.headers["authorization"] == "Bearer at-1"
    # 네이버는 이메일 인증 여부를 알리지 않는다: 항상 인증되지 않은 이메일이다.
    assert info == OAuthUserInfo(provider="naver", provider_user_id="AbC-naver_Id_123", email="lee@naver.com",
                                 email_verified=False, name="이네이버")


# ── GitHub 사용자 id ─────────────────────────────────────────────────

BAD_GITHUB_IDS = {"true": True, "false": False, "float": 4242.0, "string": "4242", "zero": 0, "negative": -5,
                  "too-big": 2 ** 63, "huge": 10 ** 40, "null": None, "list": [4242], "object": {"id": 4242},
                  "absent": _DROP}


@pytest.mark.parametrize("value", BAD_GITHUB_IDS.values(), ids=BAD_GITHUB_IDS.keys())
async def test_github_id_must_be_a_positive_json_integer(value):
    # id는 int64 정수다. JSON true는 파이썬에서 int의 하위 형식이라 isinstance(…, int)로 보면 "True" 계정이 된다.
    adapter, requests = double("github", userinfo=with_user(id=value))
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == "github_oauth" and error.value.upstream_status == 200
    assert error.value.message == "GitHub 사용자 정보의 필수 필드(id, email)가 없거나 형식이 올바르지 않습니다"
    assert [str(r.url) for r in requests] == [GITHUB_TOKEN_URL, GITHUB_USER_URL]  # 이메일 목록은 묻지 않았다


@pytest.mark.parametrize("value,stored", [(1, "1"), (2 ** 63 - 1, "9223372036854775807"),
                                          (2 ** 53 + 1, "9007199254740993")])
async def test_github_id_keeps_every_digit(value, stored):
    adapter, _ = double("github", userinfo=with_user(id=value))
    assert (await exchange(adapter)).provider_user_id == stored


# ── GitHub 이메일 목록: primary(JSON true) 항목만, verified(JSON true)일 때만 인증 ───────────────

_FLAGS = [True, False, None, "true", 1, _DROP]
_FLAG_IDS = ["true", "false", "null", "string", "one", "absent"]


@pytest.mark.parametrize("verified", _FLAGS, ids=_FLAG_IDS)
async def test_github_email_is_verified_only_when_the_primary_entry_says_json_true(verified):
    # 다른(기본이 아닌) 항목이 인증됐어도 기본 이메일의 verified만 본다.
    adapter, _ = double("github", emails=with_emails(
        {"email": "verified-other@example.com", "primary": False, "verified": True}, primary(verified=verified)))
    info = await exchange(adapter)
    assert info.email_verified is (verified is True)
    assert info.email == "kim@github-mail.com"  # 나머지 값은 그대로 읽는다


@pytest.mark.parametrize("flag", _FLAGS[1:], ids=_FLAG_IDS[1:])
async def test_github_uses_only_an_entry_whose_primary_is_json_true(flag):
    # primary가 JSON true가 아닌 항목(문자열 "true"·숫자 1 포함)은 기본 이메일이 아니다: 기본 이메일이 없으면 이메일 없음(400).
    adapter, _ = double("github", emails=with_emails(primary(primary=flag)))
    with pytest.raises(OAuthEmailMissing):
        await exchange(adapter)
    # 목록 뒤쪽에 진짜 기본 이메일이 있으면 그것을 쓴다 (앞쪽 항목·객체가 아닌 항목은 건너뛴다).
    adapter, _ = double("github", emails=with_emails(
        primary(primary=flag, email="not-primary@example.com"), "x@example.com", None, ["a"],
        primary(email="Real@Example.com")))
    info = await exchange(adapter)
    assert (info.email, info.email_verified) == ("real@example.com", True)


async def test_github_first_primary_entry_wins():
    adapter, _ = double("github", emails=with_emails(primary(email="First@Example.com", verified=False),
                                                     primary(email="second@example.com")))
    info = await exchange(adapter)
    assert (info.email, info.email_verified) == ("first@example.com", False)


async def test_github_noreply_primary_address_is_used_like_any_other():
    # users.noreply.github.com 주소도 사용자마다 유일하다: 기본·인증 이메일이면 그대로 쓴다.
    adapter, _ = double("github", emails=with_emails(primary(email="4242424242+Octo-Kim@users.noreply.github.com")))
    assert (await exchange(adapter)).email == "4242424242+octo-kim@users.noreply.github.com"


async def test_github_reads_only_the_first_page_of_emails():
    # 첫 페이지(100개)에 기본 이메일이 없으면 이메일이 없는 것으로 본다 (다음 페이지를 묻지 않는다).
    entries = [{"email": f"e{index}@example.com", "primary": False, "verified": True} for index in range(100)]
    adapter, requests = double("github", emails=httpx.Response(
        200, json=entries, headers={"Link": '<https://api.github.com/user/emails?per_page=100&page=2>; rel="next"'}))
    with pytest.raises(OAuthEmailMissing):
        await exchange(adapter)
    assert [str(r.url) for r in requests] == [GITHUB_TOKEN_URL, GITHUB_USER_URL, GITHUB_EMAILS_URL]


# ── 이메일: 없으면 OAuthEmailMissing ───────────────────────────────────

MISSING_EMAIL = {
    "github-no-primary": ("github", lambda: {"emails": with_emails(
        {"email": "a@example.com", "primary": False, "verified": True})}),
    "github-empty-list": ("github", lambda: {"emails": with_emails()}),
    "github-primary-email-null": ("github", lambda: {"emails": with_emails(primary(email=None))}),
    "github-primary-email-empty": ("github", lambda: {"emails": with_emails(primary(email=""))}),
    "github-primary-email-blank": ("github", lambda: {"emails": with_emails(primary(email="   "))}),
    "github-primary-email-absent": ("github", lambda: {"emails": with_emails(primary(email=_DROP))}),
    # 사용자가 승인할 때 user:email 권한을 뺐다 (GitHub은 403이나 404로 답한다)
    "github-403": ("github", lambda: {"emails": httpx.Response(403, json={"message": "Must have access"})}),
    "github-404": ("github", lambda: {"emails": httpx.Response(404, json={"message": "Not Found"})}),
    "naver-null": ("naver", lambda: {"userinfo": with_naver(email=None)}),
    "naver-empty": ("naver", lambda: {"userinfo": with_naver(email="")}),
    "naver-absent": ("naver", lambda: {"userinfo": with_naver(email=_DROP)}),
}


@pytest.mark.parametrize("provider,responses", MISSING_EMAIL.values(), ids=MISSING_EMAIL.keys())
async def test_missing_email_is_reported_as_oauth_email_missing(provider, responses):
    # 사용자가 이메일 제공에 동의하지 않았다: 제공자 오류(502)가 아니라 동의를 안내할 400이다 (api/oauth.py).
    adapter, requests = double(provider, **responses())
    with pytest.raises(OAuthEmailMissing):
        await exchange(adapter)
    assert len(requests) == requests_until_userinfo(provider)


@pytest.mark.parametrize("provider,responses", [
    ("github", lambda: {"emails": with_emails(primary(email="no-at-sign"))}),
    ("github", lambda: {"emails": with_emails(primary(email=5))}),
    ("github", lambda: {"emails": with_emails(primary(email="a\nb@example.com"))}),
    ("github", lambda: {"emails": with_emails(primary(email="a" * 250 + "@g.com"))}),
    ("github", lambda: {"emails": with_emails(primary(email={"a": "b"}))}),
    ("naver", lambda: {"userinfo": with_naver(email="@naver.com")}),
    ("naver", lambda: {"userinfo": with_naver(email={"a": "b"})}),
    ("naver", lambda: {"userinfo": with_naver(id=12345)}), ("naver", lambda: {"userinfo": with_naver(id="  ")}),
    ("naver", lambda: {"userinfo": with_naver(id=None)}), ("naver", lambda: {"userinfo": with_naver(id="i" * 256)}),
    ("naver", lambda: {"userinfo": with_naver(id="a b")}),
], ids=["github-no-at", "github-number", "github-newline", "github-256", "github-object", "naver-no-local",
        "naver-object", "naver-id-number", "naver-id-blank", "naver-id-null", "naver-id-256",
        "naver-id-line-separator"])
async def test_unusable_email_or_id_is_a_provider_error(provider, responses):
    adapter, _ = double(provider, **responses())
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == f"{provider}_oauth" and error.value.upstream_status == 200


async def test_naver_email_is_never_verified_whatever_the_body_says():
    adapter, _ = double("naver", userinfo=with_naver(email_verified=True, is_email_verified=True,
                                                     is_email_valid=True))
    assert (await exchange(adapter)).email_verified is False


# ── 이름 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("provider,responses,expected", [
    ("github", lambda: {"userinfo": with_user(name="  줄\n바꿈  ")}, "줄 바꿈"),
    ("github", lambda: {"userinfo": with_user(name=" ")}, "octo-kim"),
    ("github", lambda: {"userinfo": with_user(name=None)}, "octo-kim"),
    ("github", lambda: {"userinfo": with_user(name=_DROP, login="octo\tkim")}, "octo kim"),
    ("github", lambda: {"userinfo": with_user(name=None, login=None)}, "kim"),
    ("github", lambda: {"userinfo": with_user(name=["x"], login=5)}, "kim"),
    ("github", lambda: {"userinfo": with_user(name="가" * 150)}, "가" * 100),
    ("naver", lambda: {"userinfo": with_naver(name="이\t네이버")}, "이 네이버"),
    ("naver", lambda: {"userinfo": with_naver(name=None)}, "네이버닉"),
    ("naver", lambda: {"userinfo": with_naver(name="", nickname=_DROP)}, "lee"),
    ("naver", lambda: {"userinfo": with_naver(name="가" * 150)}, "가" * 100),
], ids=["github-cleaned", "github-blank-name", "github-null-name", "github-login-cleaned", "github-email-local-part",
        "github-not-strings", "github-cut", "naver-cleaned", "naver-nickname", "naver-email-local-part", "naver-cut"])
async def test_display_name_falls_back_in_order_and_is_cleaned(provider, responses, expected):
    # 이름: GitHub은 name → login → 이메일 @ 앞부분, 네이버는 name → nickname → 이메일 @ 앞부분.
    adapter, _ = double(provider, **responses())
    assert (await exchange(adapter)).name == expected


# ── 토큰 오류 응답 ───────────────────────────────────────────────────

def _github_error(status: int, error, **extra) -> httpx.Response:
    body = {"error": error, "error_description": "The code passed is incorrect or expired. code=secret-code-value",
            "error_uri": "https://docs.github.com/apps/troubleshooting", **extra}
    return httpx.Response(status, json=body)


GITHUB_TOKEN_CODE_ERRORS = {
    # GitHub은 실패한 토큰 요청에도 HTTP 200으로 답한다: 상태 코드와 관계없이 error 키를 본다.
    "200-bad-verification-code": (_github_error(200, "bad_verification_code"), None),  # 사용자 쪽 문제: 경고 없음
    "200-redirect-uri-mismatch": (_github_error(200, "redirect_uri_mismatch"), "redirect_uri_mismatch"),
    "200-invalid-grant": (_github_error(200, "invalid_grant"), "invalid_grant"),  # PKCE 불일치 등 (RFC 이름 대비)
    # access_token이 함께 와도 error가 있으면 오류다 (사용자 정보를 묻지 않는다).
    "200-error-with-token": (_github_error(200, "bad_verification_code", access_token="at-1"), None),
    "400-bad-verification-code": (_github_error(400, "bad_verification_code"), None),
    "401-redirect-uri-mismatch": (_github_error(401, "redirect_uri_mismatch"), "redirect_uri_mismatch"),
}


@pytest.mark.parametrize("response,logged", GITHUB_TOKEN_CODE_ERRORS.values(), ids=GITHUB_TOKEN_CODE_ERRORS.keys())
async def test_github_token_error_key_is_a_code_error_even_with_http_200(caplog, response, logged):
    adapter, requests = double("github", token=response)
    with caplog.at_level(logging.WARNING, logger="app.integrations.oauth"):
        with pytest.raises(OAuthCodeError, match=f"^{CODE_ERROR}$"):
            await exchange(adapter)
    assert len(requests) == 1
    lines = [r.getMessage() for r in caplog.records if r.name == "app.integrations.oauth"]
    assert lines == ([f"GitHub OAuth token request rejected: error={logged}"] if logged else [])
    assert "secret-code-value" not in caplog.text


@pytest.mark.parametrize("status", [200, 400])
async def test_github_unverified_user_email_is_its_own_error(caplog, status):
    # 기본 이메일이 인증되지 않은 GitHub 계정에는 토큰이 나오지 않는다: 이메일 미인증(400)이다. 사용자 쪽 문제라 경고 없음.
    adapter, requests = double("github", token=_github_error(status, "unverified_user_email"))
    with caplog.at_level(logging.WARNING, logger="app.integrations.oauth"):
        with pytest.raises(OAuthEmailUnverified):
            await exchange(adapter)
    assert len(requests) == 1 and not [r for r in caplog.records if r.name == "app.integrations.oauth"]
    assert not issubclass(OAuthEmailUnverified, (OAuthCodeError, OAuthEmailMissing, ProviderError))


GITHUB_TOKEN_PROVIDER_ERRORS = {
    # Client ID·secret이 틀렸다: 서버 설정 문제다 (502 + 오류 코드만 경고).
    "200-incorrect-client-credentials": (_github_error(200, "incorrect_client_credentials"),
                                         "incorrect_client_credentials"),
    "200-null-error": (httpx.Response(200, json={"error": None, "access_token": "at-1"}), "?"),
    "200-other-error": (_github_error(200, "unsupported_grant_type"), "unsupported_grant_type"),
    "200-odd-error-code": (_github_error(200, "bad\ncode LEAKME"), "?"),
    "200-error-not-a-string": (_github_error(200, ["bad_verification_code"]), "?"),
    "200-unverified-email-not-a-string": (_github_error(200, {"code": "unverified_user_email"}), "?"),
    # 3xx·5xx는 본문과 관계없이 제공자 오류다 (네이버와 같다).
    "500-bad-verification-code": (_github_error(500, "bad_verification_code"), "bad_verification_code"),
    "502-unverified-user-email": (_github_error(502, "unverified_user_email"), "unverified_user_email"),
    "503-no-body": (httpx.Response(503, content=b"busy"), None),
    "302-redirect": (httpx.Response(302, headers={"Location": "https://evil.example/t"},
                                    json={"access_token": "at-1"}), None),
    "200-not-json": (httpx.Response(200, content=b"<html>"), None),
    "200-form-encoded": (httpx.Response(200, text="access_token=at-1&scope=user%3Aemail&token_type=bearer"), None),
    "200-no-access-token": (httpx.Response(200, json={"token_type": "bearer", "scope": "user:email"}), None),
    "200-non-ascii-token": (httpx.Response(200, json={"access_token": "토큰"}), None),
    "200-trailing-space-token": (httpx.Response(200, json={"access_token": "gho_x "}), None),
    "200-blank-token": (httpx.Response(200, json={"access_token": "   "}), None),
    "200-json-list": (httpx.Response(200, json=[{"access_token": "at-1"}]), None),
}


@pytest.mark.parametrize("response,logged", GITHUB_TOKEN_PROVIDER_ERRORS.values(),
                         ids=GITHUB_TOKEN_PROVIDER_ERRORS.keys())
async def test_github_other_token_failures_are_provider_errors(caplog, response, logged):
    adapter, requests = double("github", token=response)
    with caplog.at_level(logging.WARNING, logger="app.integrations.oauth"):
        with pytest.raises(ProviderError) as error:
            await exchange(adapter)
    assert error.value.service == "github_oauth" and error.value.upstream_status == response.status_code
    assert error.value.unavailable is False and len(requests) == 1  # 리다이렉트를 따라가지 않고 사용자 정보도 묻지 않았다
    lines = [r.getMessage() for r in caplog.records if r.name == "app.integrations.oauth"]
    assert lines == ([f"GitHub OAuth token request rejected: error={logged}"] if logged else [])
    assert "LEAKME" not in caplog.text and "secret-code-value" not in caplog.text
    assert "LEAKME" not in error.value.message and "secret-code-value" not in error.value.message


NAVER_TOKEN_CODE_ERRORS = {
    # 네이버는 실패한 토큰 요청에도 HTTP 200으로 답할 수 있다: 상태 코드와 관계없이 error 키를 본다.
    "200-invalid-grant": (httpx.Response(200, json={"error": "invalid_grant", "error_description": "bad"}), None),
    "200-invalid-request": (httpx.Response(200, json={"error": "invalid_request", "error_description": "no state"}),
                            "invalid_request"),
    "200-unauthorized-client": (httpx.Response(200, json={"error": "unauthorized_client"}), "unauthorized_client"),
    # access_token이 함께 와도 error가 있으면 오류다 (사용자 정보를 묻지 않는다).
    "200-error-with-token": (httpx.Response(200, json={"error": "invalid_grant", "access_token": "at-1"}), None),
    "400-invalid-grant": (httpx.Response(400, json={"error": "invalid_grant"}), None),
    "401-unauthorized-client": (httpx.Response(401, json={"error": "unauthorized_client"}), "unauthorized_client"),
}


@pytest.mark.parametrize("response,logged", NAVER_TOKEN_CODE_ERRORS.values(), ids=NAVER_TOKEN_CODE_ERRORS.keys())
async def test_naver_token_error_key_is_an_error_even_with_http_200(caplog, response, logged):
    adapter, requests = double("naver", token=response)
    with caplog.at_level(logging.WARNING, logger="app.integrations.oauth"):
        with pytest.raises(OAuthCodeError, match=f"^{CODE_ERROR}$"):
            await exchange(adapter)
    assert len(requests) == 1
    lines = [r.getMessage() for r in caplog.records if r.name == "app.integrations.oauth"]
    assert lines == ([f"Naver OAuth token request rejected: error={logged}"] if logged else [])


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"error": "server_error", "error_description": "temporary"}),
    httpx.Response(200, json={"error": None, "access_token": "at-1"}),
    httpx.Response(200, json={"error": "unsupported_response_type"}),
    httpx.Response(500, json={"error": "invalid_grant"}), httpx.Response(503, content=b"busy"),
    httpx.Response(200, content=b"not json"), httpx.Response(200, json={"access_token": "토큰"}),
    httpx.Response(307, headers={"Location": "https://evil.example/t"}, json={"access_token": "at-1"}),
], ids=["200-server-error", "200-null-error", "200-other-error", "500-invalid-grant", "503", "not-json",
        "non-ascii-token", "redirect"])
async def test_naver_other_token_failures_are_provider_errors(response):
    adapter, requests = double("naver", token=response)
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == "naver_oauth" and error.value.upstream_status == response.status_code
    assert error.value.unavailable is False and len(requests) == 1


# ── 사용자 정보 오류 응답 ─────────────────────────────────────────────

@pytest.mark.parametrize("provider", ["github", "naver"])
async def test_userinfo_401_is_a_code_error_and_other_failures_are_provider_errors(provider):
    adapter, _ = double(provider, userinfo=httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(OAuthCodeError, match="^Invalid OAuth access token$"):
        await exchange(adapter)
    for response in (httpx.Response(500, json={}), httpx.Response(302, headers={"Location": "https://evil.example"}),
                     httpx.Response(403, json={"message": "API rate limit exceeded"}),
                     httpx.Response(200, content=b"<html>"), httpx.Response(200, json=["id"]),
                     httpx.Response(200, json={})):
        adapter, requests = double(provider, userinfo=response)
        with pytest.raises(ProviderError) as error:
            await exchange(adapter)
        assert error.value.service == f"{provider}_oauth" and error.value.upstream_status == response.status_code
        assert len(requests) == 2  # GitHub은 /user가 실패하면 이메일 목록을 묻지 않는다


GITHUB_EMAILS_FAILURES = {
    "500": httpx.Response(500, json={"message": "Server Error"}),
    "302": httpx.Response(302, headers={"Location": "https://evil.example"}, json=GITHUB_EMAILS),
    "429": httpx.Response(429, json={"message": "Too many"}),
    "object": httpx.Response(200, json={"emails": GITHUB_EMAILS}),
    "string": httpx.Response(200, json="kim@example.com"),
    "not-json": httpx.Response(200, content=b"<html>"),
    "deep-json": httpx.Response(200, content=b"[" * 200_000 + b"]" * 200_000),
}


@pytest.mark.parametrize("response", GITHUB_EMAILS_FAILURES.values(), ids=GITHUB_EMAILS_FAILURES.keys())
async def test_github_email_list_failures_are_provider_errors(response):
    adapter, requests = double("github", emails=response)
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == "github_oauth" and error.value.upstream_status == response.status_code
    assert error.value.unavailable is False and len(requests) == 3


async def test_github_email_list_401_is_a_code_error():
    adapter, requests = double("github", emails=httpx.Response(401, json={"message": "Bad credentials"}))
    with pytest.raises(OAuthCodeError, match="^Invalid OAuth access token$"):
        await exchange(adapter)
    assert len(requests) == 3


@pytest.mark.parametrize("body", [
    {**NAVER_PROFILE, "resultcode": "024", "message": "Authentication failed"},
    {**NAVER_PROFILE, "resultcode": 0}, {**NAVER_PROFILE, "resultcode": None},
    {key: value for key, value in NAVER_PROFILE.items() if key != "resultcode"},
    {**NAVER_PROFILE, "response": "not-an-object"}, {**NAVER_PROFILE, "response": None},
], ids=["024", "number", "null", "absent", "response-string", "response-null"])
async def test_naver_profile_needs_resultcode_00_and_a_response_object(body):
    adapter, _ = double("naver", userinfo=httpx.Response(200, json=body))
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == "naver_oauth" and error.value.upstream_status == 200


@pytest.mark.parametrize("provider", ["github", "naver"])
async def test_network_errors_are_provider_errors(provider):
    def down(request: httpx.Request):
        raise httpx.ConnectError("network down")

    adapter = ADAPTERS[provider](client_id="cid", client_secret="csec", transport=httpx.MockTransport(down))
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == f"{provider}_oauth" and error.value.upstream_status is None
    assert error.value.message == f"{adapter.label} OAuth 연결 실패: ConnectError"


@pytest.mark.parametrize("failing_path", ["/user", "/user/emails"])
async def test_github_network_error_on_either_rest_call_is_a_provider_error(failing_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return github_token_endpoint(request)
        if request.url.path == failing_path:
            raise httpx.ReadTimeout("slow")
        return httpx.Response(200, json=GITHUB_USER)

    adapter = GitHubOAuthAdapter(client_id="cid", client_secret="csec", transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.message == "GitHub OAuth 연결 실패: ReadTimeout" and error.value.upstream_status is None


@pytest.mark.parametrize("provider", ["github", "naver"])
async def test_adapter_ignores_proxy_environment_and_does_not_follow_redirects(monkeypatch, provider):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    real_client = httpx.AsyncClient
    seen: list[dict] = []

    class Recording(real_client):
        def __init__(self, *args, **kwargs):
            assert not args
            seen.append(dict(kwargs))
            super().__init__(**kwargs)

    monkeypatch.setattr(oauth_adapter.httpx, "AsyncClient", Recording)
    adapter, requests = double(provider, token=httpx.Response(
        302, headers={"Location": "https://evil.example/token"}, json={"access_token": "at-1"}))
    with pytest.raises(ProviderError):
        await exchange(adapter)
    assert [str(request.url) for request in requests] == [TOKEN_URLS[provider]]
    [kwargs] = seen
    assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False


async def test_github_uses_one_client_for_the_three_requests(monkeypatch):
    # 토큰·/user·/user/emails 세 요청이 같은 클라이언트(같은 접속 설정)로 나간다.
    real_client = httpx.AsyncClient
    seen: list[dict] = []

    class Recording(real_client):
        def __init__(self, *args, **kwargs):
            seen.append(dict(kwargs))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(oauth_adapter.httpx, "AsyncClient", Recording)
    adapter, requests = double("github")
    await exchange(adapter)
    assert len(seen) == 1 and len(requests) == 3


# ── API: 이메일 없음·인증되지 않은 이메일 ──────────────────────────────────

@pytest.mark.parametrize("provider", PROVIDERS)
def test_missing_email_is_400_email_required_and_creates_nothing(oauth_client, fakes, db, provider):
    fake = fakes[provider]
    fake.error = OAuthEmailMissing("no email")
    state = oauth_client.get(f"/api/v1/users/oauth/{provider}/authorize").json()["state"]
    before = db.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
    res = oauth_client.post(f"/api/v1/users/oauth/{provider}/callback", json={"code": "c", "state": state})
    assert res.status_code == 400 and res.json() == EMAIL_REQUIRED
    assert oauth_api.OAUTH_EMAIL_REQUIRED == "OAuth email is required"  # FE가 이 문구로 안내를 고른다
    assert db.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == before


@pytest.mark.parametrize("provider", PROVIDERS)
def test_provider_reported_unverified_email_is_400_not_verified_and_creates_nothing(oauth_client, fakes, db,
                                                                                     caplog, provider):
    # 어댑터가 토큰 단계에서 이메일 미인증을 알리면(GitHub unverified_user_email) 502가 아니라 400 'not verified'다.
    fake = fakes[provider]
    fake.error = OAuthEmailUnverified("unverified primary email")
    state = oauth_client.get(f"/api/v1/users/oauth/{provider}/authorize").json()["state"]
    before = db.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = oauth_client.post(f"/api/v1/users/oauth/{provider}/callback", json={"code": "c", "state": state})
    assert res.status_code == 400 and res.json() == NOT_VERIFIED
    assert oauth_api.OAUTH_EMAIL_NOT_VERIFIED == "OAuth email is not verified"  # FE가 이 문구로 안내를 고른다
    assert db.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == before
    assert audit_lines(caplog) == [] and len(fake.calls) == 1


@pytest.mark.parametrize("provider", ["google", "github"])
def test_providers_that_verify_email_refuse_an_unverified_email(oauth_client, fakes, db, provider):
    email = f"unverified-{uuid.uuid4().hex[:8]}@example.com"
    res = social_login(oauth_client, fakes[provider], provider_user_id=f"id-{uuid.uuid4().hex[:8]}", email=email,
                       verified=False)
    assert res.status_code == 400 and res.json() == NOT_VERIFIED
    assert db.execute("SELECT 1 FROM users WHERE email = %s", (email,)).fetchone() is None


def test_naver_creates_an_account_with_its_unverified_email(oauth_client, fakes, db, caplog):
    # 네이버는 인증 여부를 알리지 않으므로 막지 않는다: 같은 이메일의 계정이 없으면 새 계정을 만든다.
    email, naver_id = f"naver-{uuid.uuid4().hex[:8]}@naver.com", f"nid-{uuid.uuid4().hex[:10]}"
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        created = social_login(oauth_client, fakes["naver"], provider_user_id=naver_id, email=email, name="네이버 사용자")
        again = social_login(oauth_client, fakes["naver"], provider_user_id=naver_id, email=email)
    assert created.status_code == 200, created.text
    assert created.json()["message"] == "Naver OAuth 회원가입 및 로그인 완료"
    assert again.status_code == 200 and again.json()["message"] == "Naver OAuth 로그인 성공"
    user_id = created.json()["data"]["id"]
    assert again.json()["data"]["id"] == user_id
    assert audit_lines(caplog) == [f"event=oauth_login outcome=created user={user_id} provider=naver",
                                   f"event=oauth_login outcome=login user={user_id} provider=naver"]
    me = oauth_client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {created.json()['access_token']}"})
    assert me.json()["oauth_providers"] == ["naver"]
    row = db.execute("SELECT provider, provider_user_id, email FROM oauth_accounts WHERE user_id = %s",
                     (user_id,)).fetchone()
    assert (row["provider"], row["provider_user_id"], row["email"]) == ("naver", naver_id, email)


# ── 계정 연동 규칙 (제공자 사이) ─────────────────────────────────────────

def _new_email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:8]}@example.com"


def _id(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:10]}"


def _links(db, user_id: str) -> list[str]:
    rows = db.execute("SELECT provider FROM oauth_accounts WHERE user_id = %s ORDER BY provider", (user_id,))
    return [r["provider"] for r in rows.fetchall()]


def _users_with(db, email: str) -> int:
    return db.execute("SELECT count(*) AS n FROM users WHERE lower(email) = lower(%s)", (email,)).fetchone()["n"]


@pytest.mark.parametrize("claimed_verified", [False, True], ids=["as-sent", "fake-claims-verified"])
@pytest.mark.parametrize("owner_provider", ["google", "github"])
def test_naver_cannot_link_to_a_google_or_github_account(oauth_client, fakes, db, caplog, claimed_verified,
                                                         owner_provider):
    """네이버 이메일은 사용자가 바꿀 수 있는, 인증되지 않은 주소다: 같은 주소의 Google·GitHub 계정에 들어가지 않는다.
    어댑터가 (잘못해서) 인증됐다고 알려도 경로가 네이버 이메일을 인증되지 않은 것으로 다룬다."""
    email = _new_email("victim")
    owner = social_login(oauth_client, fakes[owner_provider], provider_user_id=_id("o"), email=email)
    assert owner.status_code == 200, owner.text
    owner_id = owner.json()["data"]["id"]
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = social_login(oauth_client, fakes["naver"], provider_user_id=_id("n"), email=email.upper(),
                           verified=claimed_verified)
    assert res.status_code == 409 and res.json() == {"detail": OAUTH_OTHER_METHOD_ACCOUNT}
    assert "access_token" not in res.text
    assert audit_lines(caplog) == [f"event=oauth_login outcome=refused_unverified_link user={owner_id} provider=naver"]
    assert _links(db, owner_id) == [owner_provider] and _users_with(db, email) == 1


@pytest.mark.parametrize("provider", ["google", "github"])
def test_google_and_github_cannot_link_to_a_naver_created_account(oauth_client, fakes, db, caplog, provider):
    """네이버로 만든 계정의 이메일은 확인된 주소가 아니다: 그 주소의 진짜 주인이 Google·GitHub으로 와도 그 계정에 넣지 않는다
    (넣으면 남의 주소로 네이버 계정을 만들어 둔 사람의 계정에 주인이 들어간다). 주인에게는 다른 방법 안내(409)가 간다."""
    email = _new_email("squatted")
    squatter = social_login(oauth_client, fakes["naver"], provider_user_id=_id("n"), email=email)
    assert squatter.status_code == 200 and squatter.json()["message"] == "Naver OAuth 회원가입 및 로그인 완료"
    squatter_id = squatter.json()["data"]["id"]
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = social_login(oauth_client, fakes[provider], provider_user_id=_id(provider), email=email)
    assert res.status_code == 409 and res.json() == {"detail": OAUTH_OTHER_METHOD_ACCOUNT}
    assert audit_lines(caplog) == [
        f"event=oauth_login outcome=refused_unverified_link user={squatter_id} provider={provider}"]
    assert _links(db, squatter_id) == ["naver"] and _users_with(db, email) == 1


def test_verified_github_links_to_a_google_account_and_google_to_a_github_account(oauth_client, fakes, db, caplog):
    # Google로 만든 계정 + 인증된 GitHub 기본 이메일(대소문자만 다름) → 그 계정에 GitHub을 붙인다.
    email = _new_email("both")
    google = social_login(oauth_client, fakes["google"], provider_user_id=_id("g"), email=email)
    user_id = google.json()["data"]["id"]
    github_id = _id("gh")
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        linked = social_login(oauth_client, fakes["github"], provider_user_id=github_id, email=email.upper())
        again = social_login(oauth_client, fakes["github"], provider_user_id=github_id, email=email)
    assert linked.status_code == 200, linked.text
    assert linked.json()["message"] == "기존 계정에 GitHub 계정 연동 및 로그인 성공"
    assert linked.json()["data"]["id"] == user_id and linked.json()["data"]["did"] == google.json()["data"]["did"]
    assert again.json()["message"] == "GitHub OAuth 로그인 성공" and again.json()["data"]["id"] == user_id
    assert audit_lines(caplog) == [f"event=oauth_login outcome=linked user={user_id} provider=github",
                                   f"event=oauth_login outcome=login user={user_id} provider=github"]
    me = oauth_client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {linked.json()['access_token']}"})
    assert me.json()["oauth_providers"] == ["github", "google"]
    # 그래도 네이버는 붙지 않는다.
    naver = social_login(oauth_client, fakes["naver"], provider_user_id=_id("n"), email=email)
    assert naver.status_code == 409 and naver.json() == {"detail": OAUTH_OTHER_METHOD_ACCOUNT}

    # 반대 방향: GitHub으로 만든 계정에 Google을 붙인다.
    email2 = _new_email("github-first")
    github = social_login(oauth_client, fakes["github"], provider_user_id=_id("gh"), email=email2)
    assert github.json()["message"] == "GitHub OAuth 회원가입 및 로그인 완료"
    google2 = social_login(oauth_client, fakes["google"], provider_user_id=_id("g"), email=email2)
    assert google2.status_code == 200 and google2.json()["message"] == "기존 계정에 Google 계정 연동 및 로그인 성공"
    assert _links(db, github.json()["data"]["id"]) == ["github", "google"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_password_accounts_are_refused_for_every_provider(oauth_client, fakes, register, db, caplog, provider):
    account = register("비밀번호 계정")
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = social_login(oauth_client, fakes[provider], provider_user_id=_id(provider), email=account["email"])
    assert res.status_code == 409 and res.json() == {"detail": OAUTH_PASSWORD_ACCOUNT}
    assert audit_lines(caplog) == [
        f"event=oauth_login outcome=refused_password_account user={account['id']} provider={provider}"]
    assert _links(db, account["id"]) == [] and _users_with(db, account["email"]) == 1


@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_second_account_of_the_same_provider_is_refused(oauth_client, fakes, db, provider):
    email = _new_email("same")
    first = social_login(oauth_client, fakes[provider], provider_user_id=_id("one"), email=email)
    assert first.status_code == 200, first.text
    second = social_login(oauth_client, fakes[provider], provider_user_id=_id("two"), email=email)
    assert second.status_code == 409
    assert second.json() == {"detail": "User is already linked to another account for this provider"}
    assert _links(db, first.json()["data"]["id"]) == [provider]


@pytest.mark.parametrize("provider", ["google", "github"])
def test_state_issued_for_one_provider_is_refused_by_the_others(oauth_client, fakes, provider):
    state = oauth_client.get(f"/api/v1/users/oauth/{provider}/authorize").json()["state"]
    for other in PROVIDERS:
        if other == provider:
            continue
        res = oauth_client.post(f"/api/v1/users/oauth/{other}/callback", json={"code": "c", "state": state})
        assert res.status_code == 400 and res.json() == {"detail": "Invalid or expired OAuth state"}
        assert fakes[other].calls == []


# ── 계정 연동 규칙: DB 단위 ──────────────────────────────────────────

def _no_new_identity(user_id: str):
    raise AssertionError("must not create a new user")


def _link(db, user_id: str, provider: str, email: str) -> None:
    db.execute("INSERT INTO oauth_accounts (user_id, provider, provider_user_id, email) VALUES (%s, %s, %s, %s)",
               (user_id, provider, _id(provider), email))


def _attach(email: str, *, provider: str = "google", email_verified=True):
    return db_service.find_or_create_oauth_user(provider=provider, provider_user_id=_id(provider), email=email,
                                                email_verified=email_verified, name="n",
                                                issue_identity=_no_new_identity)


def test_link_needs_a_verified_provider_link_with_the_account_email(db):
    # GitHub 연동의 이메일이 계정 이메일과 대소문자만 다르면 확인된 주소다 (Google 로그인이 그 계정에 붙는다).
    mixed = insert_passwordless_user(db, f"Mixed-{uuid.uuid4().hex[:8]}@Example.com")
    _link(db, mixed["id"], "github", mixed["email"].lower())
    user, created, linked = _attach(mixed["email"].lower())
    assert str(user["id"]) == mixed["id"] and (created, linked) == (False, True)

    # 네이버 연동은 확인으로 치지 않는다.
    naver_only = insert_passwordless_user(db, _new_email("naver-only"))
    _link(db, naver_only["id"], "naver", naver_only["email"])
    with pytest.raises(OAuthUnverifiedLinkConflict) as error:
        _attach(naver_only["email"])
    assert error.value.user_id == naver_only["id"] and str(error.value) == OAUTH_OTHER_METHOD_ACCOUNT

    # 없앤 제공자(목록에 없는 이름)의 예전 연동 행도 확인으로 치지 않는다 (개발 DB에만 남을 수 있다).
    retired = insert_passwordless_user(db, _new_email("retired"))
    _link(db, retired["id"], "retired-provider", retired["email"])
    with pytest.raises(OAuthUnverifiedLinkConflict):
        _attach(retired["email"], provider="github")

    # Google 연동이 있어도 그 이메일이 계정 이메일과 다르면(Google 쪽 주소를 바꾼 뒤) 확인으로 치지 않는다.
    moved = insert_passwordless_user(db, _new_email("moved"))
    _link(db, moved["id"], "google", _new_email("elsewhere"))
    with pytest.raises(OAuthUnverifiedLinkConflict):
        _attach(moved["email"], provider="github")
    assert _links(db, moved["id"]) == ["google"]


@pytest.mark.parametrize("email_verified", [False, None, 1, "true"], ids=["false", "none", "one", "string"])
def test_link_needs_email_verified_to_be_exactly_true(db, email_verified):
    attested = insert_passwordless_user(db, _new_email("attested"), attested_by="google")
    with pytest.raises(OAuthUnverifiedLinkConflict):
        _attach(attested["email"], provider="github", email_verified=email_verified)
    assert _links(db, attested["id"]) == ["google"]
    # 같은 계정에 같은 이메일, email_verified=True면 붙는다 (대조).
    user, created, linked = _attach(attested["email"], provider="github")
    assert (str(user["id"]), created, linked) == (attested["id"], False, True)


# ── 실제 어댑터 + 가짜 제공자 응답(MockTransport)으로 끝까지 ─────────────────────

@pytest.fixture
def real_adapters(client, monkeypatch):
    """GitHub·네이버 경로에 MockTransport를 단 실제 어댑터를 끼운다: {"github": (adapter, requests, responses), …}.

    responses의 token·userinfo(·emails)를 바꾸면 다음 요청부터 그 응답을 쓴다."""
    from main import app

    for name in ("github", "naver"):
        monkeypatch.setenv(f"{name.upper()}_CLIENT_ID", f"{name}-cid")
        monkeypatch.setenv(f"{name.upper()}_CLIENT_SECRET", f"{name}-csec-0123456789")
        monkeypatch.setenv(f"{name.upper()}_REDIRECT_URI", CALLBACKS[name])
    get_settings.cache_clear()
    made = {}
    for name in ("github", "naver"):
        requests: list[httpx.Request] = []
        responses = {"token": None, "userinfo": None, "emails": None}

        def handler(request: httpx.Request, requests=requests, responses=responses, name=name) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                if responses["token"]:
                    return responses["token"]
                if name == "github":
                    return github_token_endpoint(request, access_token=f"{name}-at")
                return httpx.Response(200, json={"access_token": f"{name}-at"})
            if name == "github" and request.url.path == "/user/emails":
                return responses["emails"] or httpx.Response(200, json=GITHUB_EMAILS)
            return responses["userinfo"] or httpx.Response(200, json=PROFILES[name])

        adapter = ADAPTERS[name].from_settings(get_settings())
        adapter.transport = httpx.MockTransport(handler)
        made[name] = (adapter, requests, responses)
        app.dependency_overrides[DEPENDENCIES[name]] = provide(adapter)
    yield made
    for name in ("github", "naver"):
        app.dependency_overrides.pop(DEPENDENCIES[name], None)
    get_settings.cache_clear()


def _authorize(client, provider: str) -> dict:
    res = client.get(f"/api/v1/users/oauth/{provider}/authorize",
                     params={"redirect_uri": CALLBACKS[provider], "code_challenge": PKCE_CHALLENGE,
                             "code_challenge_method": "S256"})
    assert res.status_code == 200, res.text
    return res.json()


def _github_callback(client, code: str) -> httpx.Response:
    return client.post("/api/v1/users/oauth/github/callback",
                       json={"code": code, "state": _authorize(client, "github")["state"]})


def test_github_login_end_to_end_with_the_real_adapter(client, real_adapters, db, caplog):
    adapter, requests, responses = real_adapters["github"]
    local = f"octo-{uuid.uuid4().hex[:8]}"
    github_id = uuid.uuid4().int % 10 ** 15 + 1  # 실행마다 다른 id (JSON 정수)
    responses["userinfo"] = with_user(id=github_id, login=local, name=None)
    responses["emails"] = with_emails({"email": f"other-{local}@example.com", "primary": False, "verified": True},
                                      primary(email=f"{local}@Example.com"))
    body = _authorize(client, "github")
    assert body["authorization_url"].startswith("https://github.com/login/oauth/authorize?")
    assert parse_qs(urlparse(body["authorization_url"]).query) == {
        "client_id": ["github-cid"], "redirect_uri": [CALLBACKS["github"]], "scope": ["user:email"],
        "state": [body["state"]], "code_challenge": [PKCE_CHALLENGE], "code_challenge_method": ["S256"]}
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = client.post("/api/v1/users/oauth/github/callback",
                          json={"code": "github-code", "state": body["state"], "redirect_uri": CALLBACKS["github"],
                                "code_verifier": PKCE_VERIFIER})
        assert res.status_code == 200, res.text
        again = _github_callback(client, "github-code-2")
    assert res.json()["message"] == "GitHub OAuth 회원가입 및 로그인 완료"
    assert res.json()["data"]["email"] == f"{local}@example.com" and res.json()["data"]["name"] == local
    user_id = res.json()["data"]["id"]
    assert again.status_code == 200 and again.json()["message"] == "GitHub OAuth 로그인 성공"
    assert again.json()["data"]["id"] == user_id
    assert audit_lines(caplog) == [f"event=oauth_login outcome=created user={user_id} provider=github",
                                   f"event=oauth_login outcome=login user={user_id} provider=github"]
    token, user, emails = requests[:3]
    # 토큰 요청: JSON Accept, 값은 본문에만 (state는 싣지 않는다), FE가 보낸 code_verifier·redirect_uri를 그대로 싣는다.
    assert str(token.url) == GITHUB_TOKEN_URL and token.url.query == b"" and token.headers["accept"] == "application/json"
    assert form_of(token) == {"client_id": ["github-cid"], "client_secret": ["github-csec-0123456789"],
                              "code": ["github-code"], "redirect_uri": [CALLBACKS["github"]],
                              "code_verifier": [PKCE_VERIFIER]}
    assert [str(user.url), str(emails.url)] == [GITHUB_USER_URL, GITHUB_EMAILS_URL]
    assert user.headers["authorization"] == "Bearer github-at" and user.headers["user-agent"] == "vc-career-agent-be"
    row = db.execute("SELECT provider, provider_user_id, email FROM oauth_accounts WHERE user_id = %s",
                     (user_id,)).fetchone()
    assert (row["provider"], row["provider_user_id"], row["email"]) == ("github", str(github_id), f"{local}@example.com")

    # Google로 만든(이메일이 확인된) 계정에 같은 기본 이메일의 GitHub 계정을 붙인다.
    owner = insert_passwordless_user(db, _new_email("google-owner"), attested_by="google")
    responses["userinfo"] = with_user(id=github_id + 1)
    responses["emails"] = with_emails(primary(email=owner["email"].upper()))
    linked = _github_callback(client, "github-code-3")
    assert linked.status_code == 200, linked.text
    assert linked.json()["message"] == "기존 계정에 GitHub 계정 연동 및 로그인 성공"
    assert linked.json()["data"]["id"] == owner["id"] and _links(db, owner["id"]) == ["github", "google"]

    # 기본 이메일이 인증되지 않았다 → 400 'not verified', 아무것도 만들지 않는다 (다른 인증된 항목이 있어도 쓰지 않는다).
    other = _new_email("unverified")
    responses["userinfo"] = with_user(id=github_id + 2)
    responses["emails"] = with_emails(primary(email=other, verified=False),
                                      {"email": f"verified-{other}", "primary": False, "verified": True})
    res = _github_callback(client, "github-code-4")
    assert res.status_code == 400 and res.json() == NOT_VERIFIED
    assert _users_with(db, other) == 0 and _users_with(db, f"verified-{other}") == 0

    # 토큰 단계의 unverified_user_email → 400 'not verified' (사용자 정보를 묻지 않는다)
    responses["token"] = _github_error(200, "unverified_user_email")
    sent = len(requests)
    res = _github_callback(client, "github-code-5")
    assert res.status_code == 400 and res.json() == NOT_VERIFIED and len(requests) == sent + 1
    # 다시 쓴·만료된 코드(HTTP 200 + bad_verification_code) → 400 코드 오류
    responses["token"] = _github_error(200, "bad_verification_code")
    res = _github_callback(client, "github-code-6")
    assert res.status_code == 400 and res.json() == {"detail": CODE_ERROR}
    # Client secret이 틀렸다 → 502 GitHub 문구 (협력사 문구 없음)
    responses["token"] = _github_error(200, "incorrect_client_credentials")
    res = _github_callback(client, "github-code-7")
    assert res.status_code == 502 and res.json() == {"detail": "GitHub OAuth error", "code": "PROVIDER_ERROR"}
    responses["token"] = None
    # user:email 권한을 빼고 승인했다 (이메일 목록 403·404), 기본 이메일이 없다, 빈 목록 → 400 'OAuth email is required'
    for index, emails in enumerate((httpx.Response(403, json={"message": "Resource not accessible"}),
                                    httpx.Response(404, json={"message": "Not Found"}), with_emails(),
                                    with_emails({"email": _new_email("x"), "primary": False, "verified": True}))):
        responses["userinfo"] = with_user(id=github_id + 3 + index)
        responses["emails"] = emails
        res = _github_callback(client, f"github-code-8-{index}")
        assert res.status_code == 400 and res.json() == EMAIL_REQUIRED, index
    # 이메일 목록이 배열이 아니다 → 502 GitHub 문구
    responses["emails"] = httpx.Response(200, json={"email": _new_email("x"), "primary": True, "verified": True})
    res = _github_callback(client, "github-code-8-object")
    assert res.status_code == 502 and res.json() == {"detail": "GitHub OAuth error", "code": "PROVIDER_ERROR"}
    # /user가 401 → 400 'Invalid OAuth access token'
    responses["userinfo"] = httpx.Response(401, json={"message": "Bad credentials"})
    res = _github_callback(client, "github-code-9")
    assert res.status_code == 400 and res.json() == {"detail": "Invalid OAuth access token"}
    # /user의 id가 정수가 아니다 → 502 GitHub 문구
    responses["userinfo"] = with_user(id=str(github_id))
    res = _github_callback(client, "github-code-10")
    assert res.status_code == 502 and res.json() == {"detail": "GitHub OAuth error", "code": "PROVIDER_ERROR"}


def test_naver_login_end_to_end_with_the_real_adapter(client, real_adapters, db):
    adapter, requests, responses = real_adapters["naver"]
    email = f"naver-{uuid.uuid4().hex[:8]}@naver.com"
    naver_id = f"naverid{uuid.uuid4().hex[:10]}"
    responses["userinfo"] = with_naver(email=email, id=naver_id)
    body = _authorize(client, "naver")
    query = parse_qs(urlparse(body["authorization_url"]).query)
    assert body["authorization_url"].startswith("https://nid.naver.com/oauth2.0/authorize?")
    assert "code_challenge" not in query and query["state"] == [body["state"]]
    res = client.post("/api/v1/users/oauth/naver/callback",
                      json={"code": "naver-code", "state": body["state"], "redirect_uri": CALLBACKS["naver"],
                            "code_verifier": PKCE_VERIFIER})
    assert res.status_code == 200, res.text
    assert res.json()["message"] == "Naver OAuth 회원가입 및 로그인 완료"
    token = requests[0]
    # 토큰 요청: 본문에 인가 요청의 state를 싣고, 주소에는 아무것도 싣지 않는다. FE가 보낸 code_verifier는 싣지 않는다.
    assert str(token.url) == NAVER_TOKEN_URL and token.url.query == b""
    assert form_of(token) == {"grant_type": ["authorization_code"], "client_id": ["naver-cid"],
                              "client_secret": ["naver-csec-0123456789"], "code": ["naver-code"],
                              "state": [body["state"]]}
    row = db.execute("SELECT provider_user_id FROM oauth_accounts WHERE provider = 'naver' AND user_id = %s",
                     (res.json()["data"]["id"],)).fetchone()
    assert row["provider_user_id"] == naver_id

    # HTTP 200 + error → 400 (코드 오류), resultcode != "00" → 502 (네이버 문구)
    responses["token"] = httpx.Response(200, json={"error": "invalid_grant", "error_description": "used code"})
    res = client.post("/api/v1/users/oauth/naver/callback",
                      json={"code": "naver-code", "state": _authorize(client, "naver")["state"]})
    assert res.status_code == 400 and res.json() == {"detail": CODE_ERROR}
    responses["token"] = None
    responses["userinfo"] = httpx.Response(200, json={**NAVER_PROFILE, "resultcode": "024"})
    res = client.post("/api/v1/users/oauth/naver/callback",
                      json={"code": "naver-code-2", "state": _authorize(client, "naver")["state"]})
    assert res.status_code == 502 and res.json() == {"detail": "Naver OAuth error", "code": "PROVIDER_ERROR"}
    # 이메일 제공을 거절한 사용자 → 400 'OAuth email is required'
    responses["userinfo"] = with_naver(email=None, id=f"naverid{uuid.uuid4().hex[:10]}")
    res = client.post("/api/v1/users/oauth/naver/callback",
                      json={"code": "naver-code-3", "state": _authorize(client, "naver")["state"]})
    assert res.status_code == 400 and res.json() == EMAIL_REQUIRED


def test_unconfigured_github_and_naver_answer_503_with_their_own_wording(client, monkeypatch):
    for name, label in (("github", "GitHub"), ("naver", "Naver")):
        monkeypatch.setenv(f"{name.upper()}_CLIENT_ID", "")
        monkeypatch.setenv(f"{name.upper()}_CLIENT_SECRET", "")
        get_settings.cache_clear()
        res = client.get(f"/api/v1/users/oauth/{name}/authorize")
        assert res.status_code == 503 and res.json() == {"detail": f"{label} OAuth is not configured",
                                                         "code": "PROVIDER_NOT_CONFIGURED"}
    get_settings.cache_clear()


# ── 검증 단계 보강 (2026-11-15) ─────────────────────────────────────────

@pytest.mark.parametrize("name,label", [("github", "GitHub"), ("naver", "Naver")])
def test_provider_without_a_callback_address_is_503_even_when_the_fe_sends_one(client, monkeypatch, name, label):
    # 회귀: 콜백 주소를 비운(운영에서 아직 켜지 않은) 제공자에 FE가 자기 콜백 주소를 보내면 400 'Invalid redirect_uri'였다.
    # FE는 그 400을 '허용되지 않은 로그인 경로'로 보여 주므로, 설정 없음(503)으로 답해 '지금 사용할 수 없습니다'가 나오게 한다.
    monkeypatch.setenv(f"{name.upper()}_REDIRECT_URI", "")
    get_settings.cache_clear()
    try:
        for params in ({"redirect_uri": CALLBACKS[name], "code_challenge": PKCE_CHALLENGE}, {}):
            res = client.get(f"/api/v1/users/oauth/{name}/authorize", params=params)
            assert res.status_code == 503, res.text
            assert res.json() == {"detail": f"{label} OAuth redirect_uri is not configured"}
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("provider,email", [
    ("github", "Kim@example.com"),          # 켈빈 기호: 소문자로 바꾸면 ASCII k
    ("github", "İlker@example.com"),        # İ: 소문자로 바꾸면 i + 결합 점
    ("naver", "vic@example.com​"),         # 폭 없는 공백 (보이지 않는다)
    ("naver", "‮moc.elpmaxe@lee"),         # 방향 바꿈 문자
    ("github", "kim⁠@example.com"),         # 단어 결합자 (Cf)
    ("github", "kim​@example.com"),         # 폭 없는 공백 (Cf)
], ids=["kelvin", "dotted-i", "zero-width-space", "rtl-override", "word-joiner", "github-zero-width-space"])
async def test_confusable_or_invisible_email_characters_are_refused(provider, email):
    # 소문자로 바꿔 비교·연동하므로, 다른 메일함이 ASCII 계정과 같은 이메일로 읽히거나 똑같아 보이는 이메일은 받지 않는다.
    responses = {"emails": with_emails(primary(email=email))} if provider == "github" else {
        "userinfo": with_naver(email=email)}
    adapter, _ = double(provider, **responses)
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == f"{provider}_oauth" and error.value.upstream_status == 200


async def test_ordinary_non_ascii_email_is_still_accepted_and_lowercased():
    adapter, _ = double("github", emails=with_emails(primary(email="Ünal.Café@Example.com")))
    assert (await exchange(adapter)).email == "ünal.café@example.com"


@pytest.mark.parametrize("provider", ["google", "github", "naver"])
async def test_token_failure_message_never_carries_the_provider_description(provider):
    # main.py가 이 메시지를 'Provider error: …'로 로그에 남긴다: 제공자의 설명 문구(인가 코드 등이 들어갈 수 있다)는 싣지 않는다.
    async def fail_with(body: dict) -> str:
        adapter, _ = double(provider, token=httpx.Response(401, json=body))
        with pytest.raises(ProviderError) as error:
            await adapter.exchange_code(code="auth-code-1", redirect_uri=CALLBACKS[provider],
                                        code_verifier=PKCE_VERIFIER, state="state-jwt-1")
        return str(error.value)

    message = await fail_with({"error": "invalid_client", "error_description": "secret=LEAKME code=LEAKCODE"})
    assert "LEAKME" not in message and "LEAKCODE" not in message
    assert "invalid_client" in message and "401" in message
    message = await fail_with({"error": "bad\ncode LEAKME"})
    assert "LEAKME" not in message and "(?," in message


def _new_identity(user_id: str):
    return f"did:key:z6MkOzReg{uuid.uuid4().hex[:16]}", None


def test_attestation_only_counts_links_of_the_same_account(db):
    # 계정 A: Google 연동의 이메일이 (Google 쪽 주소를 바꾼 뒤) v로 갱신된 상태. A 자신의 이메일은 다르다.
    v = _new_email("victim")
    a = insert_passwordless_user(db, _new_email("a"))
    _link(db, a["id"], "google", v)
    # 계정 V: 네이버로 만든 계정 (이메일 v, 확인되지 않은 주소)
    victim = insert_passwordless_user(db, v, attested_by="naver")
    # 인증된 GitHub 이메일 v로 로그인해도 V에 붙지 않는다 (A의 Google 연동은 V의 이메일을 확인하지 않는다).
    with pytest.raises(OAuthUnverifiedLinkConflict):
        _attach(v, provider="github")
    assert _links(db, victim["id"]) == ["naver"]


def test_login_updates_the_stored_provider_email_so_a_moved_address_no_longer_attests(db):
    e1, e2 = _new_email("old"), _new_email("new")
    sub = _id("sub")
    user, created, _ = db_service.find_or_create_oauth_user(provider="google", provider_user_id=sub, email=e1,
                                                            email_verified=True, name="n",
                                                            issue_identity=_new_identity)
    assert created
    again, created, linked = db_service.find_or_create_oauth_user(provider="google", provider_user_id=sub, email=e2,
                                                                  email_verified=True, name="n",
                                                                  issue_identity=_no_new_identity)
    assert str(again["id"]) == str(user["id"]) and (created, linked) == (False, False)
    assert db.execute("SELECT email FROM oauth_accounts WHERE provider = 'google' AND provider_user_id = %s",
                      (sub,)).fetchone()["email"] == e2
    # Google 쪽 주소가 바뀐 뒤에는 e1(계정 이메일)이 더 이상 확인된 주소가 아니다: GitHub e1은 붙지 않는다.
    with pytest.raises(OAuthUnverifiedLinkConflict):
        _attach(e1, provider="github")
    assert _links(db, str(user["id"])) == ["google"]


def test_github_login_failure_logs_carry_no_token_code_or_email(client, real_adapters, caplog):
    # 실패한 로그인의 로그(어댑터 경고·'Provider error' 줄)에는 토큰·인가 코드·이메일·client_secret이 없다.
    adapter, requests, responses = real_adapters["github"]
    responses["token"] = _github_error(200, "incorrect_client_credentials", access_token="gho_SHOULD_NOT_LOG")
    with caplog.at_level(logging.DEBUG):
        res = _github_callback(client, "code-SHOULD-NOT-LOG")
    assert res.status_code == 502
    text = caplog.text + json.dumps([r.getMessage() for r in caplog.records])
    for secret in ("gho_SHOULD_NOT_LOG", "code-SHOULD-NOT-LOG", "github-csec-0123456789", "secret-code-value"):
        assert secret not in text
    assert "GitHub OAuth token request rejected: error=incorrect_client_credentials" in text


@pytest.mark.parametrize("provider", ["google", "github", "naver"])
@pytest.mark.parametrize("token", ["at-1 ", " at-1", "at 1", "   ", "at-1\t", "at-ü1"],
                         ids=["trailing-space", "leading-space", "inner-space", "blank", "tab", "non-ascii"])
async def test_access_token_must_be_visible_ascii_without_spaces(provider, token):
    # 회귀: 'gho_x '처럼 공백이 붙은 토큰을 받아 Authorization 헤더를 만들다 h11이 거절했고, LOG_LEVEL=DEBUG의 httpcore 로그가
    # 그 예외에 토큰을 그대로 남겼다. 이제 헤더를 만들기 전에 제공자 오류(502)로 끝나고 사용자 정보 요청은 보내지 않는다.
    adapter, requests = double(provider, token=httpx.Response(200, json={"access_token": token, "token_type": "bearer"}))
    with pytest.raises(ProviderError) as error:
        await adapter.exchange_code(code="auth-code-1", redirect_uri=CALLBACKS[provider],
                                    code_verifier=PKCE_VERIFIER, state="state-jwt-1")
    assert error.value.service == f"{provider}_oauth" and len(requests) == 1
    assert token.strip() == "" or token.strip() not in str(error.value)


@pytest.mark.parametrize("provider", ["google", "naver"])
async def test_google_and_naver_token_requests_keep_their_exact_headers_and_body(provider):
    # GitHub용 토큰 요청 헤더 훅(token_headers)이 Google·네이버 요청을 바꾸지 않는다: Accept: application/json을 싣지 않고,
    # 헤더 이름과 본문 필드는 GitHub 추가 이전과 같다.
    google_profile = httpx.Response(200, json={"sub": "g-1", "email": "g@example.com", "email_verified": True, "name": "n"})
    adapter, requests = double(provider, userinfo=google_profile if provider == "google" else None)
    await adapter.exchange_code(code="auth-code-1", redirect_uri=CALLBACKS[provider],
                                code_verifier=PKCE_VERIFIER, state="state-jwt-1")
    token_request = requests[0]
    assert token_request.method == "POST" and token_request.url.query == b""
    names = sorted(name.lower() for name in token_request.headers.keys())
    assert names == ["accept", "accept-encoding", "connection", "content-length", "content-type", "host", "user-agent"]
    assert token_request.headers["accept"] == "*/*"
    assert token_request.headers["content-type"] == "application/x-www-form-urlencoded"
    expected = {"google": {"client_id", "client_secret", "code", "redirect_uri", "grant_type", "code_verifier"},
                "naver": {"grant_type", "client_id", "client_secret", "code", "state"}}[provider]
    assert set(form_of(token_request)) == expected

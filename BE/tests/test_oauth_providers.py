"""카카오·네이버 OAuth 2.0 어댑터와 제공자 사이의 계정 연동 규칙 (MockTransport / 가짜 제공자 + 실제 PostgreSQL).

카카오(kauth.kakao.com·kapi.kakao.com)·네이버(nid.naver.com·openapi.naver.com)에는 한 번도 요청하지 않는다: 어댑터는
httpx.MockTransport로, API는 tests/test_oauth.py의 가짜 제공자(또는 MockTransport를 단 실제 어댑터)로 시험한다.
값은 모두 가상의 값이다.
- 어댑터: 인가 주소 매개변수(카카오는 PKCE를 싣고 scope·prompt는 싣지 않음, 네이버는 PKCE를 싣지 않음), 토큰 요청 값은 본문에만
  (client_secret·state가 주소에 실리지 않음), 카카오 회원번호는 양의 JSON 정수만, 이메일 없음 → OAuthEmailMissing(400),
  카카오 이메일 인증 여부(is_email_valid·is_email_verified 둘 다 true), 네이버의 HTTP 200 + error, resultcode, 401,
  JSON이 아닌 본문, 리다이렉트를 따라가지 않음, 오류 표시(kakao_oauth·naver_oauth).
- 계정 연동 규칙: 같은 이메일의 비밀번호 없는 계정에는, 이번 제공자가 이메일을 인증했고(Google·Kakao) 그 계정의 이메일이
  인증된 제공자 연동으로 확인된 주소일 때만 붙인다. 네이버 로그인은 다른 계정에 붙지 않고, 네이버로 만든 계정에도 붙지 않는다.
"""
import logging
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.api import oauth as oauth_api
from app.core.config import get_settings
from app.integrations import oauth as oauth_adapter
from app.integrations.oauth import GoogleOAuthAdapter, KakaoOAuthAdapter, NaverOAuthAdapter
from app.integrations.ports import OAuthCodeError, OAuthEmailMissing, OAuthUserInfo, ProviderError
from app.services.database import (
    EMAIL_VERIFYING_OAUTH_PROVIDERS, OAUTH_OTHER_METHOD_ACCOUNT, OAUTH_PASSWORD_ACCOUNT, OAuthUnverifiedLinkConflict,
    db_service,
)
from tests.test_oauth import (  # noqa: F401  (fakes·fake_google·oauth_client는 이 파일에서도 쓰는 픽스처다)
    CALLBACKS, DEPENDENCIES, PKCE_CHALLENGE, PKCE_VERIFIER, PROVIDERS, audit_lines, fake_google, fakes,
    insert_passwordless_user, oauth_client, provide, social_login,
)

KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAKAO_USERINFO_URL = "https://kapi.kakao.com/v2/user/me"
NAVER_TOKEN_URL = "https://nid.naver.com/oauth2.0/token"
NAVER_USERINFO_URL = "https://openapi.naver.com/v1/nid/me"
KAKAO_PROFILE = {"id": 4242424242, "connected_at": "2026-11-15T00:00:00Z",
                 "properties": {"nickname": "속성닉네임"},
                 "kakao_account": {"email": "Kim@Kakao.com", "is_email_valid": True, "is_email_verified": True,
                                   "email_needs_agreement": False, "profile": {"nickname": "카카오친구"}}}
NAVER_PROFILE = {"resultcode": "00", "message": "success",
                 "response": {"id": "AbC-naver_Id_123", "email": "Lee@Naver.com", "name": "이네이버",
                              "nickname": "네이버닉"}}
PROFILES = {"kakao": KAKAO_PROFILE, "naver": NAVER_PROFILE}
ADAPTERS = {"google": GoogleOAuthAdapter, "kakao": KakaoOAuthAdapter, "naver": NaverOAuthAdapter}
TOKEN_URLS = {"kakao": KAKAO_TOKEN_URL, "naver": NAVER_TOKEN_URL}
CODE_ERROR = "Invalid or expired OAuth authorization code"
REDIRECT = {"kakao": CALLBACKS["kakao"], "naver": CALLBACKS["naver"]}


def double(provider: str, *, token: httpx.Response | None = None, userinfo: httpx.Response | None = None,
           client_id: str = "cid", client_secret: str = "csec-0123456789"):
    """토큰(POST)·사용자 정보(GET) 엔드포인트를 흉내 내는 실제 어댑터와, 받은 요청 목록. 응답을 주지 않으면 정상 응답."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return token or httpx.Response(200, json={"access_token": "at-1", "token_type": "bearer",
                                                      "expires_in": "3600"})
        return userinfo or httpx.Response(200, json=PROFILES[provider])

    adapter = ADAPTERS[provider](client_id=client_id, client_secret=client_secret,
                                 transport=httpx.MockTransport(handler))
    return adapter, requests


async def exchange(adapter, *, code_verifier: str | None = PKCE_VERIFIER, state: str | None = "state-jwt-1"):
    return await adapter.exchange_code(code="auth-code-1", redirect_uri=REDIRECT[adapter.provider_name],
                                       code_verifier=code_verifier, state=state)


def form_of(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode(), keep_blank_values=True)


def with_account(**fields) -> httpx.Response:
    """카카오 사용자 정보: kakao_account의 필드만 바꾼다 (값이 _DROP이면 뺀다)."""
    account = {**KAKAO_PROFILE["kakao_account"], **fields}
    account = {key: value for key, value in account.items() if value is not _DROP}
    return httpx.Response(200, json={**KAKAO_PROFILE, "kakao_account": account})


def with_naver(**fields) -> httpx.Response:
    """네이버 사용자 정보: response의 필드만 바꾼다 (값이 _DROP이면 뺀다)."""
    profile = {**NAVER_PROFILE["response"], **fields}
    profile = {key: value for key, value in profile.items() if value is not _DROP}
    return httpx.Response(200, json={**NAVER_PROFILE, "response": profile})


_DROP = object()


# ── 경로 ─────────────────────────────────────────────────────────

def test_oauth_routes_are_three_static_paths_per_provider():
    """경로 변수('/{provider}')가 아니라 제공자마다 고정 경로 셋이다: 본문 상한·접근 로그 가리기·WAF 규칙이 정확한 경로로 맞춘다.
    Google 경로의 이름(OpenAPI operationId)은 예전 함수 이름 그대로다."""
    from main import app
    from tests.test_auth_security import route_inventory

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


# ── 어댑터 속성 ─────────────────────────────────────────────────────

def test_adapter_attributes_and_the_email_verifying_provider_list_agree():
    assert [(a.provider_name, a.label, a.supports_pkce, a.requires_verified_email) for a in ADAPTERS.values()] == [
        ("google", "Google", True, True), ("kakao", "Kakao", True, True), ("naver", "Naver", False, False)]
    # 계정 연동 규칙(database.py)이 '확인된 이메일'로 보는 제공자 = 이메일을 인증한 것만 받는 제공자
    assert set(EMAIL_VERIFYING_OAUTH_PROVIDERS) == {
        a.provider_name for a in ADAPTERS.values() if a.requires_verified_email}
    assert {name: ADAPTERS[name](client_id="c", client_secret="s").service for name in PROVIDERS} == {
        "google": "google_oauth", "kakao": "kakao_oauth", "naver": "naver_oauth"}


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


# ── 인가 주소 ─────────────────────────────────────────────────────

def test_kakao_authorize_url_carries_pkce_but_no_scope_or_prompt():
    adapter, _ = double("kakao")
    url = adapter.build_authorize_url(state="st-1", redirect_uri=REDIRECT["kakao"], code_challenge=PKCE_CHALLENGE,
                                      code_challenge_method="S256")
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "kauth.kakao.com", "/oauth/authorize")
    assert parse_qs(parsed.query) == {"client_id": ["cid"], "redirect_uri": [REDIRECT["kakao"]],
                                      "response_type": ["code"], "state": ["st-1"],
                                      "code_challenge": [PKCE_CHALLENGE], "code_challenge_method": ["S256"]}
    # FE가 code_challenge를 보내지 않으면 PKCE 매개변수도 없다.
    plain = parse_qs(urlparse(adapter.build_authorize_url(state="st-1", redirect_uri=REDIRECT["kakao"])).query)
    assert set(plain) == {"client_id", "redirect_uri", "response_type", "state"}


def test_naver_authorize_url_never_carries_pkce():
    adapter, _ = double("naver")
    url = adapter.build_authorize_url(state="st-1", redirect_uri=REDIRECT["naver"], code_challenge=PKCE_CHALLENGE,
                                      code_challenge_method="S256")
    parsed = urlparse(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("https", "nid.naver.com", "/oauth2.0/authorize")
    assert parse_qs(parsed.query) == {"response_type": ["code"], "client_id": ["cid"],
                                      "redirect_uri": [REDIRECT["naver"]], "state": ["st-1"]}
    assert "csec" not in url  # 시크릿은 인가 주소에 없다


@pytest.mark.parametrize("provider", ["kakao", "naver"])
def test_unconfigured_adapter_is_unavailable_with_its_own_service_tag(provider):
    for client_id, client_secret in (("", ""), ("cid", ""), ("", "csec"), ("  ", " ")):
        adapter, requests = double(provider, client_id=client_id, client_secret=client_secret)
        with pytest.raises(ProviderError) as error:
            adapter.build_authorize_url(state="s", redirect_uri=REDIRECT[provider])
        assert error.value.unavailable is True and error.value.service == f"{provider}_oauth"
        assert error.value.message == f"{adapter.label} OAuth is not configured"
        assert requests == []


# ── 토큰 요청: 값은 본문에만 ─────────────────────────────────────────

async def test_kakao_token_request_and_profile():
    adapter, requests = double("kakao")
    info = await exchange(adapter)
    token, userinfo = requests
    assert (token.method, str(token.url)) == ("POST", KAKAO_TOKEN_URL) and token.url.query == b""
    assert token.headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert form_of(token) == {"grant_type": ["authorization_code"], "client_id": ["cid"],
                              "redirect_uri": [REDIRECT["kakao"]], "code": ["auth-code-1"],
                              "client_secret": ["csec-0123456789"], "code_verifier": [PKCE_VERIFIER]}
    assert (userinfo.method, str(userinfo.url)) == ("GET", KAKAO_USERINFO_URL)
    assert userinfo.headers["authorization"] == "Bearer at-1"
    assert info == OAuthUserInfo(provider="kakao", provider_user_id="4242424242", email="kim@kakao.com",
                                 email_verified=True, name="카카오친구")
    # code_verifier가 없으면 싣지 않는다.
    adapter, requests = double("kakao")
    await exchange(adapter, code_verifier=None)
    assert "code_verifier" not in form_of(requests[0])


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


# ── 카카오 회원번호 ──────────────────────────────────────────────────

BAD_KAKAO_IDS = {"true": True, "false": False, "float": 4242.0, "string": "4242", "zero": 0, "negative": -5,
                 "too-big": 2 ** 63, "huge": 10 ** 40, "null": None, "list": [4242], "object": {"id": 4242}}


@pytest.mark.parametrize("value", BAD_KAKAO_IDS.values(), ids=BAD_KAKAO_IDS.keys())
async def test_kakao_id_must_be_a_positive_json_integer(value):
    # 회원번호는 Long(1 ~ 2^63-1)이다. JSON true는 파이썬에서 int의 하위 형식이라 isinstance(…, int)로 보면 "True" 계정이 된다.
    adapter, _ = double("kakao", userinfo=httpx.Response(200, json={**KAKAO_PROFILE, "id": value}))
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == "kakao_oauth" and error.value.upstream_status == 200
    assert error.value.message == "Kakao 사용자 정보의 필수 필드(id, email)가 없거나 형식이 올바르지 않습니다"


@pytest.mark.parametrize("value,stored", [(1, "1"), (2 ** 63 - 1, "9223372036854775807"),
                                          (2 ** 53 + 1, "9007199254740993")])
async def test_kakao_id_keeps_every_digit(value, stored):
    adapter, _ = double("kakao", userinfo=httpx.Response(200, json={**KAKAO_PROFILE, "id": value}))
    assert (await exchange(adapter)).provider_user_id == stored


async def test_kakao_profile_without_an_id_is_a_provider_error():
    adapter, _ = double("kakao", userinfo=httpx.Response(
        200, json={key: value for key, value in KAKAO_PROFILE.items() if key != "id"}))
    with pytest.raises(ProviderError):
        await exchange(adapter)


# ── 이메일: 없으면 OAuthEmailMissing, 카카오 인증 여부 ─────────────────────

MISSING_EMAIL = {
    "kakao-null": ("kakao", lambda: with_account(email=None)),
    "kakao-empty": ("kakao", lambda: with_account(email="")),
    "kakao-blank": ("kakao", lambda: with_account(email="   ")),
    "kakao-absent": ("kakao", lambda: with_account(email=_DROP, email_needs_agreement=True)),
    "kakao-no-account": ("kakao", lambda: httpx.Response(200, json={"id": 4242424242})),
    "kakao-account-not-object": ("kakao", lambda: httpx.Response(200, json={"id": 4242424242,
                                                                           "kakao_account": "x"})),
    "naver-null": ("naver", lambda: with_naver(email=None)),
    "naver-empty": ("naver", lambda: with_naver(email="")),
    "naver-absent": ("naver", lambda: with_naver(email=_DROP)),
}


@pytest.mark.parametrize("provider,response", MISSING_EMAIL.values(), ids=MISSING_EMAIL.keys())
async def test_missing_email_is_reported_as_oauth_email_missing(provider, response):
    # 사용자가 이메일 제공에 동의하지 않았다: 제공자 오류(502)가 아니라 동의를 안내할 400이다 (api/oauth.py).
    adapter, requests = double(provider, userinfo=response())
    with pytest.raises(OAuthEmailMissing):
        await exchange(adapter)
    assert len(requests) == 2


@pytest.mark.parametrize("provider,response", [
    ("kakao", lambda: with_account(email="no-at-sign")), ("kakao", lambda: with_account(email=5)),
    ("kakao", lambda: with_account(email="a\nb@kakao.com")),
    ("kakao", lambda: with_account(email="a" * 250 + "@k.com")),
    ("naver", lambda: with_naver(email="@naver.com")), ("naver", lambda: with_naver(email={"a": "b"})),
    ("naver", lambda: with_naver(id=12345)), ("naver", lambda: with_naver(id="  ")),
    ("naver", lambda: with_naver(id=None)), ("naver", lambda: with_naver(id="i" * 256)),
    ("naver", lambda: with_naver(id="a\u2028b")),
], ids=["kakao-no-at", "kakao-number", "kakao-newline", "kakao-256", "naver-no-local", "naver-object",
        "naver-id-number", "naver-id-blank", "naver-id-null", "naver-id-256", "naver-id-line-separator"])
async def test_unusable_email_or_id_is_a_provider_error(provider, response):
    adapter, _ = double(provider, userinfo=response())
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == f"{provider}_oauth" and error.value.upstream_status == 200


_FLAGS = [True, False, None, "true", 1, _DROP]


@pytest.mark.parametrize("valid", _FLAGS, ids=["true", "false", "null", "string", "one", "absent"])
@pytest.mark.parametrize("verified", _FLAGS, ids=["true", "false", "null", "string", "one", "absent"])
async def test_kakao_email_is_verified_only_when_valid_and_verified_are_both_json_true(valid, verified):
    adapter, _ = double("kakao", userinfo=with_account(is_email_valid=valid, is_email_verified=verified))
    info = await exchange(adapter)
    assert info.email_verified is (valid is True and verified is True)
    assert info.email == "kim@kakao.com"  # 나머지 값은 그대로 읽는다


async def test_naver_email_is_never_verified_whatever_the_body_says():
    adapter, _ = double("naver", userinfo=with_naver(email_verified=True, is_email_verified=True,
                                                     is_email_valid=True))
    assert (await exchange(adapter)).email_verified is False


# ── 이름 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("provider,response,expected", [
    ("kakao", lambda: with_account(profile={"nickname": "  줄\n바꿈  "}), "줄 바꿈"),
    ("kakao", lambda: with_account(profile={"nickname": " "}), "속성닉네임"),
    ("kakao", lambda: with_account(profile=_DROP), "속성닉네임"),
    ("kakao", lambda: httpx.Response(200, json={**KAKAO_PROFILE, "properties": None,
                                                "kakao_account": {**KAKAO_PROFILE["kakao_account"], "profile": []}}),
     "kim"),
    ("naver", lambda: with_naver(name="이\t네이버"), "이 네이버"),
    ("naver", lambda: with_naver(name=None), "네이버닉"),
    ("naver", lambda: with_naver(name="", nickname=_DROP), "lee"),
    ("naver", lambda: with_naver(name="가" * 150), "가" * 100),
], ids=["kakao-cleaned", "kakao-blank-nickname", "kakao-no-profile", "kakao-email-local-part", "naver-cleaned",
        "naver-nickname", "naver-email-local-part", "naver-cut"])
async def test_display_name_falls_back_in_order_and_is_cleaned(provider, response, expected):
    adapter, _ = double(provider, userinfo=response())
    assert (await exchange(adapter)).name == expected


# ── 토큰 오류 응답 ───────────────────────────────────────────────────

def _kakao_error(status: int, error: str, error_code: str | None = None) -> httpx.Response:
    body = {"error": error, "error_description": "authorization code not found for code=secret-code-value"}
    if error_code is not None:
        body["error_code"] = error_code
    return httpx.Response(status, json=body)


@pytest.mark.parametrize("response,logged", [
    (_kakao_error(400, "invalid_grant", "KOE320"), None),  # 다시 쓴·만료된 코드: 사용자 쪽 문제라 경고 없음
    (_kakao_error(400, "invalid_grant"), None),
    (_kakao_error(400, "invalid_grant", "KOE303"), "error=invalid_grant error_code=KOE303"),  # redirect_uri 불일치
    (_kakao_error(401, "invalid_grant", "KOE303\nINJECTED"), "error=invalid_grant error_code=?"),
], ids=["koe320", "no-error-code", "koe303", "odd-error-code"])
async def test_kakao_invalid_grant_is_a_code_error_and_setup_problems_are_logged(caplog, response, logged):
    adapter, requests = double("kakao", token=response)
    with caplog.at_level(logging.WARNING, logger="app.integrations.oauth"):
        with pytest.raises(OAuthCodeError, match=f"^{CODE_ERROR}$"):
            await exchange(adapter)
    lines = [r.getMessage() for r in caplog.records if r.name == "app.integrations.oauth"]
    assert lines == ([f"Kakao OAuth token request rejected: {logged}"] if logged else [])
    assert "secret-code-value" not in caplog.text and len(requests) == 1


@pytest.mark.parametrize("response", [
    _kakao_error(401, "invalid_client", "KOE010"), _kakao_error(400, "invalid_request", "KOE237"),
    _kakao_error(500, "invalid_grant", "KOE320"), httpx.Response(200, json={"token_type": "bearer"}),
    httpx.Response(200, content=b"<html>"), httpx.Response(302, headers={"Location": "https://evil.example/t"},
                                                           json={"access_token": "at-1"}),
], ids=["invalid-client", "rate-limit", "server-500", "no-access-token", "not-json", "redirect"])
async def test_kakao_other_token_failures_are_provider_errors(response):
    adapter, requests = double("kakao", token=response)
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == "kakao_oauth" and error.value.upstream_status == response.status_code
    assert len(requests) == 1  # 리다이렉트를 따라가지 않고 사용자 정보도 묻지 않았다


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

@pytest.mark.parametrize("provider", ["kakao", "naver"])
async def test_userinfo_401_is_a_code_error_and_other_failures_are_provider_errors(provider):
    adapter, _ = double(provider, userinfo=httpx.Response(401, json={"code": -401, "msg": "InvalidTokenException"}))
    with pytest.raises(OAuthCodeError, match="^Invalid OAuth access token$"):
        await exchange(adapter)
    for response in (httpx.Response(500, json={}), httpx.Response(302, headers={"Location": "https://evil.example"}),
                     httpx.Response(200, content=b"<html>"), httpx.Response(200, json=["id"]),
                     httpx.Response(200, json={})):
        adapter, requests = double(provider, userinfo=response)
        with pytest.raises(ProviderError) as error:
            await exchange(adapter)
        assert error.value.service == f"{provider}_oauth" and error.value.upstream_status == response.status_code
        assert len(requests) == 2


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


@pytest.mark.parametrize("provider", ["kakao", "naver"])
async def test_network_errors_are_provider_errors(provider):
    def down(request: httpx.Request):
        raise httpx.ConnectError("network down")

    adapter = ADAPTERS[provider](client_id="cid", client_secret="csec", transport=httpx.MockTransport(down))
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == f"{provider}_oauth" and error.value.upstream_status is None
    assert error.value.message == f"{adapter.label} OAuth 연결 실패: ConnectError"


@pytest.mark.parametrize("provider", ["kakao", "naver"])
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


# ── API: 이메일 없음·인증되지 않은 이메일 ──────────────────────────────────

@pytest.mark.parametrize("provider", PROVIDERS)
def test_missing_email_is_400_email_required_and_creates_nothing(oauth_client, fakes, db, provider):
    fake = fakes[provider]
    fake.error = OAuthEmailMissing("no email")
    state = oauth_client.get(f"/api/v1/users/oauth/{provider}/authorize").json()["state"]
    before = db.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
    res = oauth_client.post(f"/api/v1/users/oauth/{provider}/callback", json={"code": "c", "state": state})
    assert res.status_code == 400 and res.json() == {"detail": "OAuth email is required"}
    assert oauth_api.OAUTH_EMAIL_REQUIRED == "OAuth email is required"  # FE가 이 문구로 안내를 고른다
    assert db.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == before


@pytest.mark.parametrize("provider", ["google", "kakao"])
def test_providers_that_verify_email_refuse_an_unverified_email(oauth_client, fakes, db, provider):
    email = f"unverified-{uuid.uuid4().hex[:8]}@example.com"
    res = social_login(oauth_client, fakes[provider], provider_user_id=f"id-{uuid.uuid4().hex[:8]}", email=email,
                       verified=False)
    assert res.status_code == 400 and res.json() == {"detail": "OAuth email is not verified"}
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
def test_naver_cannot_link_to_a_google_account(oauth_client, fakes, db, caplog, claimed_verified):
    """네이버 이메일은 사용자가 바꿀 수 있는, 인증되지 않은 주소다: 같은 주소의 Google 계정에 들어가지 않는다.
    어댑터가 (잘못해서) 인증됐다고 알려도 경로가 네이버 이메일을 인증되지 않은 것으로 다룬다."""
    email = _new_email("victim")
    owner = social_login(oauth_client, fakes["google"], provider_user_id=_id("g"), email=email)
    assert owner.status_code == 200, owner.text
    owner_id = owner.json()["data"]["id"]
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        res = social_login(oauth_client, fakes["naver"], provider_user_id=_id("n"), email=email.upper(),
                           verified=claimed_verified)
    assert res.status_code == 409 and res.json() == {"detail": OAUTH_OTHER_METHOD_ACCOUNT}
    assert "access_token" not in res.text
    assert audit_lines(caplog) == [f"event=oauth_login outcome=refused_unverified_link user={owner_id} provider=naver"]
    assert _links(db, owner_id) == ["google"] and _users_with(db, email) == 1


@pytest.mark.parametrize("provider", ["google", "kakao"])
def test_google_and_kakao_cannot_link_to_a_naver_created_account(oauth_client, fakes, db, caplog, provider):
    """네이버로 만든 계정의 이메일은 확인된 주소가 아니다: 그 주소의 진짜 주인이 Google·Kakao로 와도 그 계정에 넣지 않는다
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


def test_verified_kakao_links_to_a_google_account_and_google_to_a_kakao_account(oauth_client, fakes, db, caplog):
    # Google로 만든 계정 + 인증된 카카오 이메일(대소문자만 다름) → 그 계정에 카카오를 붙인다.
    email = _new_email("both")
    google = social_login(oauth_client, fakes["google"], provider_user_id=_id("g"), email=email)
    user_id = google.json()["data"]["id"]
    kakao_id = _id("k")
    with caplog.at_level(logging.INFO, logger="vc-be.audit"):
        linked = social_login(oauth_client, fakes["kakao"], provider_user_id=kakao_id, email=email.upper())
        again = social_login(oauth_client, fakes["kakao"], provider_user_id=kakao_id, email=email)
    assert linked.status_code == 200, linked.text
    assert linked.json()["message"] == "기존 계정에 Kakao 계정 연동 및 로그인 성공"
    assert linked.json()["data"]["id"] == user_id and linked.json()["data"]["did"] == google.json()["data"]["did"]
    assert again.json()["message"] == "Kakao OAuth 로그인 성공" and again.json()["data"]["id"] == user_id
    assert audit_lines(caplog) == [f"event=oauth_login outcome=linked user={user_id} provider=kakao",
                                   f"event=oauth_login outcome=login user={user_id} provider=kakao"]
    me = oauth_client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {linked.json()['access_token']}"})
    assert me.json()["oauth_providers"] == ["google", "kakao"]
    # 그래도 네이버는 붙지 않는다.
    naver = social_login(oauth_client, fakes["naver"], provider_user_id=_id("n"), email=email)
    assert naver.status_code == 409 and naver.json() == {"detail": OAUTH_OTHER_METHOD_ACCOUNT}

    # 반대 방향: 카카오로 만든 계정에 Google을 붙인다.
    email2 = _new_email("kakao-first")
    kakao = social_login(oauth_client, fakes["kakao"], provider_user_id=_id("k"), email=email2)
    assert kakao.json()["message"] == "Kakao OAuth 회원가입 및 로그인 완료"
    google2 = social_login(oauth_client, fakes["google"], provider_user_id=_id("g"), email=email2)
    assert google2.status_code == 200 and google2.json()["message"] == "기존 계정에 Google 계정 연동 및 로그인 성공"
    assert _links(db, kakao.json()["data"]["id"]) == ["google", "kakao"]


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


@pytest.mark.parametrize("provider", ["google", "kakao"])
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
    # 카카오 연동의 이메일이 계정 이메일과 대소문자만 다르면 확인된 주소다.
    mixed = insert_passwordless_user(db, f"Mixed-{uuid.uuid4().hex[:8]}@Example.com")
    _link(db, mixed["id"], "kakao", mixed["email"].lower())
    user, created, linked = _attach(mixed["email"].lower())
    assert str(user["id"]) == mixed["id"] and (created, linked) == (False, True)

    # 네이버 연동은 확인으로 치지 않는다.
    naver_only = insert_passwordless_user(db, _new_email("naver-only"))
    _link(db, naver_only["id"], "naver", naver_only["email"])
    with pytest.raises(OAuthUnverifiedLinkConflict) as error:
        _attach(naver_only["email"])
    assert error.value.user_id == naver_only["id"] and str(error.value) == OAUTH_OTHER_METHOD_ACCOUNT

    # Google 연동이 있어도 그 이메일이 계정 이메일과 다르면(Google 쪽 주소를 바꾼 뒤) 확인으로 치지 않는다.
    moved = insert_passwordless_user(db, _new_email("moved"))
    _link(db, moved["id"], "google", _new_email("elsewhere"))
    with pytest.raises(OAuthUnverifiedLinkConflict):
        _attach(moved["email"], provider="kakao")
    assert _links(db, moved["id"]) == ["google"]


@pytest.mark.parametrize("email_verified", [False, None, 1, "true"], ids=["false", "none", "one", "string"])
def test_link_needs_email_verified_to_be_exactly_true(db, email_verified):
    attested = insert_passwordless_user(db, _new_email("attested"), attested_by="google")
    with pytest.raises(OAuthUnverifiedLinkConflict):
        _attach(attested["email"], provider="kakao", email_verified=email_verified)
    assert _links(db, attested["id"]) == ["google"]
    # 같은 계정에 같은 이메일, email_verified=True면 붙는다 (대조).
    user, created, linked = _attach(attested["email"], provider="kakao")
    assert (str(user["id"]), created, linked) == (attested["id"], False, True)


# ── 실제 어댑터 + 가짜 제공자 응답(MockTransport)으로 끝까지 ─────────────────────

@pytest.fixture
def real_adapters(client, monkeypatch):
    """카카오·네이버 경로에 MockTransport를 단 실제 어댑터를 끼운다: {"kakao": (adapter, requests, responses), …}.

    responses의 token·userinfo를 바꾸면 다음 요청부터 그 응답을 쓴다."""
    from main import app

    for name in ("kakao", "naver"):
        monkeypatch.setenv(f"{name.upper()}_CLIENT_ID", f"{name}-cid")
        monkeypatch.setenv(f"{name.upper()}_CLIENT_SECRET", f"{name}-csec-0123456789")
        monkeypatch.setenv(f"{name.upper()}_REDIRECT_URI", CALLBACKS[name])
    get_settings.cache_clear()
    made = {}
    for name in ("kakao", "naver"):
        requests: list[httpx.Request] = []
        responses = {"token": None, "userinfo": None}

        def handler(request: httpx.Request, requests=requests, responses=responses, name=name) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                return responses["token"] or httpx.Response(200, json={"access_token": f"{name}-at"})
            return responses["userinfo"] or httpx.Response(200, json=PROFILES[name])

        adapter = ADAPTERS[name].from_settings(get_settings())
        adapter.transport = httpx.MockTransport(handler)
        made[name] = (adapter, requests, responses)
        app.dependency_overrides[DEPENDENCIES[name]] = provide(adapter)
    yield made
    for name in ("kakao", "naver"):
        app.dependency_overrides.pop(DEPENDENCIES[name], None)
    get_settings.cache_clear()


def _authorize(client, provider: str) -> dict:
    res = client.get(f"/api/v1/users/oauth/{provider}/authorize",
                     params={"redirect_uri": CALLBACKS[provider], "code_challenge": PKCE_CHALLENGE,
                             "code_challenge_method": "S256"})
    assert res.status_code == 200, res.text
    return res.json()


def test_kakao_login_end_to_end_with_the_real_adapter(client, real_adapters, db):
    adapter, requests, responses = real_adapters["kakao"]
    email_local = f"kakao-{uuid.uuid4().hex[:8]}"
    kakao_id = uuid.uuid4().int % 10 ** 15 + 1  # 실행마다 다른 회원번호 (JSON 정수)
    account = {**KAKAO_PROFILE["kakao_account"], "email": f"{email_local}@Kakao.com"}
    responses["userinfo"] = httpx.Response(200, json={**KAKAO_PROFILE, "id": kakao_id, "kakao_account": account})
    body = _authorize(client, "kakao")
    query = parse_qs(urlparse(body["authorization_url"]).query)
    assert body["authorization_url"].startswith("https://kauth.kakao.com/oauth/authorize?")
    assert query["code_challenge"] == [PKCE_CHALLENGE] and query["client_id"] == ["kakao-cid"]
    assert "scope" not in query and "prompt" not in query
    res = client.post("/api/v1/users/oauth/kakao/callback",
                      json={"code": "kakao-code", "state": body["state"], "redirect_uri": CALLBACKS["kakao"],
                            "code_verifier": PKCE_VERIFIER})
    assert res.status_code == 200, res.text
    assert res.json()["message"] == "Kakao OAuth 회원가입 및 로그인 완료"
    assert res.json()["data"]["email"] == f"{email_local}@kakao.com"
    token = requests[0]
    assert token.url.query == b"" and form_of(token)["code_verifier"] == [PKCE_VERIFIER]
    assert form_of(token)["redirect_uri"] == [CALLBACKS["kakao"]] and "state" not in form_of(token)
    row = db.execute("SELECT provider, provider_user_id FROM oauth_accounts WHERE user_id = %s",
                     (res.json()["data"]["id"],)).fetchone()
    assert (row["provider"], row["provider_user_id"]) == ("kakao", str(kakao_id))

    # 인증되지 않은 카카오 이메일(is_email_verified=false)은 400이고 아무것도 만들지 않는다.
    other = f"kakao-unverified-{uuid.uuid4().hex[:8]}@kakao.com"
    responses["userinfo"] = httpx.Response(200, json={**KAKAO_PROFILE, "id": 777, "kakao_account": {
        **KAKAO_PROFILE["kakao_account"], "email": other, "is_email_verified": False}})
    res = client.post("/api/v1/users/oauth/kakao/callback",
                      json={"code": "kakao-code-2", "state": _authorize(client, "kakao")["state"]})
    assert res.status_code == 400 and res.json() == {"detail": "OAuth email is not verified"}
    assert _users_with(db, other) == 0

    # 이메일 동의가 없으면 400 'OAuth email is required'
    responses["userinfo"] = with_account(email=_DROP, email_needs_agreement=True)
    res = client.post("/api/v1/users/oauth/kakao/callback",
                      json={"code": "kakao-code-3", "state": _authorize(client, "kakao")["state"]})
    assert res.status_code == 400 and res.json() == {"detail": "OAuth email is required"}

    # 카카오 쪽 실패는 카카오 문구로 답한다 (502, 협력사 문구 없음).
    responses["token"] = _kakao_error(401, "invalid_client", "KOE010")
    res = client.post("/api/v1/users/oauth/kakao/callback",
                      json={"code": "kakao-code-4", "state": _authorize(client, "kakao")["state"]})
    assert res.status_code == 502 and res.json() == {"detail": "Kakao OAuth error", "code": "PROVIDER_ERROR"}


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
    assert res.status_code == 400 and res.json() == {"detail": "OAuth email is required"}


def test_unconfigured_kakao_and_naver_answer_503_with_their_own_wording(client, monkeypatch):
    for name, label in (("kakao", "Kakao"), ("naver", "Naver")):
        monkeypatch.setenv(f"{name.upper()}_CLIENT_ID", "")
        monkeypatch.setenv(f"{name.upper()}_CLIENT_SECRET", "")
        get_settings.cache_clear()
        res = client.get(f"/api/v1/users/oauth/{name}/authorize")
        assert res.status_code == 503 and res.json() == {"detail": f"{label} OAuth is not configured",
                                                         "code": "PROVIDER_NOT_CONFIGURED"}
    get_settings.cache_clear()


# ── 검증 단계 보강 (2026-11-15) ─────────────────────────────────────────

@pytest.mark.parametrize("name,label", [("kakao", "Kakao"), ("naver", "Naver")])
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
    ("kakao", "Kim@example.com"),          # 켈빈 기호: 소문자로 바꾸면 ASCII k
    ("kakao", "İlker@example.com"),        # İ: 소문자로 바꾸면 i + 결합 점
    ("naver", "vic@example.com​"),         # 폭 없는 공백 (보이지 않는다)
    ("naver", "‮moc.elpmaxe@lee"),         # 방향 바꿈 문자
    ("kakao", "kim⁠@example.com"),         # 단어 결합자 (Cf)
], ids=["kelvin", "dotted-i", "zero-width-space", "rtl-override", "word-joiner"])
async def test_confusable_or_invisible_email_characters_are_refused(provider, email):
    # 소문자로 바꿔 비교·연동하므로, 다른 메일함이 ASCII 계정과 같은 이메일로 읽히거나 똑같아 보이는 이메일은 받지 않는다.
    response = with_account(email=email) if provider == "kakao" else with_naver(email=email)
    adapter, _ = double(provider, userinfo=response)
    with pytest.raises(ProviderError) as error:
        await exchange(adapter)
    assert error.value.service == f"{provider}_oauth" and error.value.upstream_status == 200


async def test_ordinary_non_ascii_email_is_still_accepted_and_lowercased():
    adapter, _ = double("kakao", userinfo=with_account(email="Ünal.Café@Example.com"))
    assert (await exchange(adapter)).email == "ünal.café@example.com"


@pytest.mark.parametrize("provider", ["google", "kakao", "naver"])
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
    # 인증된 카카오 이메일 v로 로그인해도 V에 붙지 않는다 (A의 Google 연동은 V의 이메일을 확인하지 않는다).
    with pytest.raises(OAuthUnverifiedLinkConflict):
        _attach(v, provider="kakao")
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
    # Google 쪽 주소가 바뀐 뒤에는 e1(계정 이메일)이 더 이상 확인된 주소가 아니다: 카카오 e1은 붙지 않는다.
    with pytest.raises(OAuthUnverifiedLinkConflict):
        _attach(e1, provider="kakao")
    assert _links(db, str(user["id"])) == ["google"]

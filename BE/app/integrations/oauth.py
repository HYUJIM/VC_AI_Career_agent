"""OAuth 2.0 로그인 어댑터 (Google·GitHub·Naver).

제공자의 인가·토큰·사용자 정보 엔드포인트 형식은 이 파일에서만 다룬다. 공통 부분(접속 방식, 2xx만 결과, 헤더에 넣을 수 있는
access_token, 프로필 값 검사·정리)은 _OAuthAdapter에 두고, 제공자마다 다른 부분(인가·토큰 요청 매개변수·헤더, 토큰 오류 응답
해석, 프로필 필드 위치)만 각 클래스가 정한다.
"""
import logging
import re
import unicodedata
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.core.text import has_visible_text, is_email_confusable, strip_invisible
from app.integrations.ports import (
    OAuthCodeError, OAuthEmailMissing, OAuthEmailUnverified, OAuthUserInfo, ProviderError,
)

logger = logging.getLogger(__name__)

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_GOOGLE_SCOPE = "openid email profile"
_CLIENT_CODE_ERRORS = frozenset({"invalid_grant", "invalid_request", "redirect_uri_mismatch"})
# GitHub OAuth App (GitHub App 아님). 브라우저는 github.com의 인가 주소로 가고, BE는 github.com(토큰)과
# api.github.com(사용자·이메일)만 부른다.
_GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"
_GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
# 이메일 목록(GET /user/emails)을 읽는 가장 작은 권한. id·login·name은 권한 없이 받는 공개 프로필에도 있다.
_GITHUB_SCOPE = "user:email"
# GitHub 사용자 id는 int64 정수다 (1 ~ 2^63-1).
_GITHUB_MAX_ID = 2 ** 63 - 1
# 사용자의 인가 코드 문제로 보는 토큰 오류: 틀리거나 만료·재사용된 코드(bad_verification_code), 등록한 콜백 주소와 다름
# (redirect_uri_mismatch), RFC 이름(invalid_grant, PKCE 불일치 등 문서에 없는 경우 대비)
_GITHUB_CODE_ERRORS = frozenset({"bad_verification_code", "redirect_uri_mismatch", "invalid_grant"})
# GitHub이 기본 이메일이 인증되지 않은 사용자에게 토큰을 주지 않을 때의 오류 코드
_GITHUB_UNVERIFIED_EMAIL = "unverified_user_email"
# REST API 공통 헤더 값. API 버전 2022-11-28은 2028-03-10까지 지원한다. User-Agent가 없거나 잘못되면 403이므로
# 고정된 일반 이름을 보낸다 (개인정보·저장소 이름을 넣지 않는다).
_GITHUB_API_VERSION = "2022-11-28"
_GITHUB_USER_AGENT = "vc-career-agent-be"
# 이메일 목록은 페이지로 나뉜다: 첫 페이지(최대 100개)만 본다. 그 안에 기본 이메일이 없으면 이메일이 없는 것으로 본다.
_GITHUB_EMAILS_PER_PAGE = "100"
_NAVER_AUTH_URL = "https://nid.naver.com/oauth2.0/authorize"
_NAVER_TOKEN_URL = "https://nid.naver.com/oauth2.0/token"
_NAVER_USERINFO_URL = "https://openapi.naver.com/v1/nid/me"
_NAVER_CODE_ERRORS = frozenset({"invalid_request", "unauthorized_client", "invalid_grant"})
# DB 컬럼 길이: users.email·oauth_accounts.email·oauth_accounts.provider_user_id는 VARCHAR(255), users.name은 VARCHAR(100)
_MAX_ID_CHARS = 255
_MAX_NAME_CHARS = 100
# 한 줄 텍스트에 둘 수 없는 문자: 제어 문자(Cc, NUL 포함)·줄 구분자(Zl)·문단 구분자(Zp)·짝 없는 서로게이트(Cs, DB에 저장할 수 없다)
_NOT_PLAIN_TEXT = frozenset({"Cc", "Zl", "Zp", "Cs"})
# 경고 로그에 그대로 남겨도 되는 제공자 오류 코드 모양 (예: incorrect_client_credentials). 다른 모양의 값은 남기지 않는다.
_LOGGABLE_CODE = re.compile(r"[A-Za-z0-9_.-]{1,40}")
# Authorization 헤더에 넣을 제공자 액세스 토큰: 공백 없는 보이는 ASCII 문자(0x21~0x7E)만
_HEADER_SAFE_TOKEN = re.compile(r"[\x21-\x7e]+")


def _json_dict(response: httpx.Response) -> Optional[dict[str, Any]]:
    try:
        data = response.json()
    except (ValueError, RecursionError):  # 아주 깊게 중첩된 본문(RecursionError)도 JSON이 아닌 본문으로 본다
        return None
    return data if isinstance(data, dict) else None


def _json_list(response: httpx.Response) -> Optional[list[Any]]:
    """JSON 배열 본문이면 그 목록, 아니면(JSON이 아님·객체·아주 깊게 중첩) None. GitHub 이메일 목록에 쓴다."""
    try:
        data = response.json()
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, list) else None


def _loggable_code(value: Any) -> str:
    """경고 로그에 남길 제공자 오류 코드: 코드 모양의 문자열이면 그대로, 아니면 '?' (설명 문구·값은 남기지 않는다)."""
    return value if isinstance(value, str) and _LOGGABLE_CODE.fullmatch(value) else "?"


def _object(value: Any) -> dict[str, Any]:
    """JSON 객체면 그대로, 아니면(없음·목록·문자열 등) 빈 dict. 중첩된 프로필 필드를 읽을 때 쓴다."""
    return value if isinstance(value, dict) else {}


def _plain_text(value: Any) -> Optional[str]:
    """제공자가 준 식별자·이메일: 앞뒤 공백을 뗀 값이 비어 있지 않은 한 줄 문자열이면 그 값, 아니면 None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(unicodedata.category(ch) in _NOT_PLAIN_TEXT for ch in value):
        return None
    return value


def _display_name(*values: Any, fallback: str) -> str:
    """프로필 이름 정리. 가입 API는 한 줄이 아닌 이름을 거절하지만, 여기서는 거절하지 않고 고친다
    (프로필 이름 때문에 로그인이 막히지 않게): 제어 문자·줄/문단 구분자는 공백으로 바꾸고, 방향 제어·보이지 않는 서식
    문자(U+202E·U+200B 등, app.core.text.BIDI_AND_INVISIBLE: 가입 API는 거절한다)는 지우고, 이어진 공백은 하나로 줄이고,
    앞뒤 공백을 떼고 100자로 자른다. 후보(values)를 앞에서부터 보고 정리한 결과에 보이는 글자가 있는 첫 값을 쓰며,
    쓸 이름이 없으면 fallback(이메일의 @ 앞부분)을 쓴다."""
    for value in values:
        text = strip_invisible(value) if isinstance(value, str) else ""
        text = "".join(" " if unicodedata.category(ch) in _NOT_PLAIN_TEXT else ch for ch in text)
        text = " ".join(text.split())[:_MAX_NAME_CHARS].strip()
        if text and has_visible_text(text):  # 결합 문자·이형 선택자만 남은 이름은 빈 이름처럼 보이므로 다음 후보로
            return text
    fallback = fallback[:_MAX_NAME_CHARS]
    return fallback if has_visible_text(fallback) else "사용자"  # 보이는 글자가 없는 이메일 앞부분은 쓰지 않는다


def _confusable_email_char(ch: str) -> bool:
    """이메일에 두지 않는 문자.
    - 보이지 않는 서식 문자(Cf: U+200B 폭 없는 공백, U+202E 방향 바꿈 등): 화면에서 다른 계정 이메일과 똑같아 보인다.
    - 소문자로 바꾸면 ASCII가 되는 비ASCII 문자(U+212A 켈빈 기호 → k, U+0130 İ → i + 점): 이메일은 소문자로 바꿔
      비교·연동하므로, 다른(비ASCII) 메일함이 ASCII 계정과 같은 이메일로 읽힌다."""
    if is_email_confusable(ch):  # Cf, 폭 0인 미배정 글자(U+E0080 등)·한글 채움 문자·이형 선택자·CGJ·U+FFFC
        return True
    return not ch.isascii() and any(c.isascii() for c in ch.lower())


def _is_missing(value: Any) -> bool:
    """제공자가 이메일을 주지 않은 경우: 필드가 없거나 null이거나 빈(공백뿐인) 문자열."""
    return value is None or (isinstance(value, str) and not value.strip())


class _OAuthAdapter:
    """OAuth 2.0 Authorization Code 클라이언트의 공통 부분.

    제공자 클래스는 아래 클래스 속성과 _authorize_params·_token_form·_check_token_response·_profile을 정한다.
    - provider_name: oauth_accounts.provider, state의 provider 클레임, 경로 이름(/oauth/<provider_name>/…)
    - label: 응답(main.py의 고정 문구)·로그 문구에 쓰는 이름
    - supports_pkce: False면 code_challenge를 인가 주소에, code_verifier를 토큰 요청에 싣지 않는다
    - requires_verified_email: True면 제공자가 인증했다고 알린 이메일만 받는다 (api/oauth.py)
    - token_headers: 토큰 요청에 더하는 헤더 (기본 없음. GitHub은 JSON 응답을 받으려고 Accept를 보낸다)
    사용자 정보를 한 번의 요청으로 받지 않는 제공자(GitHub: 프로필 + 이메일 목록)는 _fetch_userinfo를 다시 정하고
    _get·_get_object로 같은 클라이언트에서 여러 번 부른다.
    ProviderError의 service는 "<provider_name>_oauth"다 (main.py가 이 값으로 고정 문구를 고른다).
    """

    provider_name: str
    label: str
    supports_pkce: bool
    requires_verified_email: bool
    auth_url: str
    token_url: str
    userinfo_url: str
    # 프로필 필수 필드 검사 실패 로그 문구에 쓰는 필드 이름
    profile_fields: str
    # 토큰 요청 헤더 (비어 있으면 httpx 기본 헤더만 보낸다: Google·Naver의 요청은 예전과 같다)
    token_headers: dict[str, str] = {}

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        timeout: float = 10.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.timeout = timeout
        self.transport = transport

    @classmethod
    def from_settings(cls, settings: Settings):
        # 설정 이름은 제공자 이름을 따른다: google_client_id·github_client_secret 등. 호출 제한 시간은 세 제공자가 같이 쓴다.
        return cls(
            client_id=getattr(settings, f"{cls.provider_name}_client_id"),
            client_secret=getattr(settings, f"{cls.provider_name}_client_secret"),
            timeout=settings.oauth_timeout_seconds,
        )

    @property
    def service(self) -> str:
        return f"{self.provider_name}_oauth"

    def _error(self, message: str, **kwargs: Any) -> ProviderError:
        """제공자 쪽 실패. main.py가 발급·검증 인프라 문구가 아니라 이 제공자의 로그인 문구로 답하도록 표시한다."""
        return ProviderError(message, service=self.service, **kwargs)

    def _ensure_configured(self) -> None:
        if not self.client_id or not self.client_secret:
            raise self._error(f"{self.label} OAuth is not configured", unavailable=True)

    def _client(self) -> httpx.AsyncClient:
        # 주변 프록시 환경변수(HTTP(S)_PROXY·ALL_PROXY)·SSL_CERT_FILE을 쓰지 않고 리다이렉트도 따라가지 않는다
        # (dxworks·ai_service와 같음). 이 요청에는 client_secret·인가 코드·제공자 액세스 토큰이 실린다.
        return httpx.AsyncClient(timeout=self.timeout, transport=self.transport,
                                 follow_redirects=False, trust_env=False)

    # ── 인가 주소 ─────────────────────────────────────────────

    def _authorize_params(self, *, state: str, redirect_uri: str) -> dict[str, str]:
        raise NotImplementedError

    def build_authorize_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
    ) -> str:
        self._ensure_configured()
        params = self._authorize_params(state=state, redirect_uri=redirect_uri)
        # PKCE를 받지 않는 제공자(Naver)에는 FE가 보낸 code_challenge를 싣지 않는다.
        if code_challenge and self.supports_pkce:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = code_challenge_method or "S256"
        return f"{self.auth_url}?{urlencode(params)}"

    # ── 토큰 교환 + 사용자 정보 ─────────────────────────────────

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None,
        state: Optional[str] = None,
    ) -> OAuthUserInfo:
        self._ensure_configured()
        if not self.supports_pkce:
            code_verifier = None  # 인가 주소에 code_challenge를 싣지 않았으므로 토큰 요청에도 싣지 않는다
        async with self._client() as client:
            access_token = await self._fetch_access_token(
                client, code=code, redirect_uri=redirect_uri, code_verifier=code_verifier, state=state
            )
            return await self._fetch_userinfo(client, access_token)

    def _token_form(self, *, code: str, redirect_uri: str, code_verifier: Optional[str],
                    state: Optional[str]) -> dict[str, str]:
        raise NotImplementedError

    def _check_token_response(self, response: httpx.Response, body: Optional[dict[str, Any]]) -> None:
        """토큰 응답이 오류면 OAuthCodeError(사용자의 코드 문제, 400)나 ProviderError(502)를 낸다."""
        raise NotImplementedError

    def _token_failure(self, response: httpx.Response, body: Optional[dict[str, Any]]) -> ProviderError:
        # 메시지는 main.py가 로그에 남긴다: 제공자가 보낸 설명(error_description)은 인가 코드 등을 담을 수 있어 넣지 않고,
        # 오류 코드도 코드 모양일 때만 넣는다 (GitHub·네이버 경고 줄과 같은 기준).
        error_code = (body or {}).get("error")
        if error_code is None or error_code == "":
            shown = str(response.status_code)
        elif isinstance(error_code, str) and _LOGGABLE_CODE.fullmatch(error_code):
            shown = error_code
        else:
            shown = "?"
        return self._error(f"{self.label} OAuth 토큰 오류 ({shown}, HTTP {response.status_code})",
                           upstream_status=response.status_code)

    async def _fetch_access_token(
        self,
        client: httpx.AsyncClient,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str],
        state: Optional[str],
    ) -> str:
        form = self._token_form(code=code, redirect_uri=redirect_uri, code_verifier=code_verifier, state=state)
        try:
            # 매개변수는 모두 본문(application/x-www-form-urlencoded)에 싣는다. 주소(쿼리)에 넣으면 client_secret·인가 코드가
            # 요청 주소를 남기는 로그(httpx 요청 줄, 프록시·게이트웨이 로그)에 남는다.
            # 더할 헤더가 없으면 headers=None(httpx 기본값)이라 Google·Naver의 요청은 예전과 같다.
            response = await client.post(self.token_url, data=form, headers=dict(self.token_headers) or None)
        except httpx.HTTPError as exc:
            raise self._error(f"{self.label} OAuth 연결 실패: {type(exc).__name__}") from exc

        body = _json_dict(response)
        self._check_token_response(response, body)

        access_token = body.get("access_token") if body else None
        # 토큰은 다음 요청의 Authorization 헤더에 그대로 들어간다: 공백 없는 보이는 ASCII 문자만 받는다. 헤더에 넣을 수 없는 값
        # (ASCII가 아니거나 제어 문자, 앞뒤·중간 공백)은 요청을 만들다 나는 오류 대신 바로 제공자 오류(502)로 답한다.
        # (공백이 붙은 토큰은 h11이 헤더를 거절하는데, LOG_LEVEL=DEBUG의 httpcore 로그가 그 예외에 토큰을 그대로 남긴다.)
        if not isinstance(access_token, str) or not _HEADER_SAFE_TOKEN.fullmatch(access_token):
            raise self._error(f"{self.label} OAuth 토큰 응답에 쓸 수 있는 access_token이 없습니다",
                              upstream_status=response.status_code)
        return access_token

    async def _get(self, client: httpx.AsyncClient, url: str, *, headers: dict[str, str],
                   params: Optional[dict[str, str]] = None) -> httpx.Response:
        """제공자 API GET 한 번. 연결 실패(시간 초과·끊김 등)는 제공자 오류(502)다."""
        try:
            return await client.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise self._error(f"{self.label} OAuth 연결 실패: {type(exc).__name__}") from exc

    async def _get_object(self, client: httpx.AsyncClient, url: str, *,
                          headers: dict[str, str]) -> tuple[dict[str, Any], int]:
        """사용자 정보(JSON 객체)와 상태 코드. 401은 토큰 문제(400), 2xx가 아니거나 빈·객체가 아닌 본문은 제공자 오류(502)."""
        response = await self._get(client, url, headers=headers)
        if response.status_code == 401:
            raise OAuthCodeError("Invalid OAuth access token")
        body = _json_dict(response)
        if not 200 <= response.status_code < 300:
            raise self._error(
                f"{self.label} 사용자 정보 조회 실패 ({response.status_code})",
                upstream_status=response.status_code,
            )
        if not body:
            raise self._error(f"{self.label} 사용자 정보 응답 형식이 올바르지 않습니다",
                              upstream_status=response.status_code)
        return body, response.status_code

    async def _fetch_userinfo(self, client: httpx.AsyncClient, access_token: str) -> OAuthUserInfo:
        body, status = await self._get_object(client, self.userinfo_url,
                                              headers={"Authorization": f"Bearer {access_token}"})
        return self._profile(body, status)

    def _profile(self, body: dict[str, Any], status: int) -> OAuthUserInfo:
        raise NotImplementedError

    def _profile_error(self, status: int) -> ProviderError:
        return self._error(f"{self.label} 사용자 정보의 필수 필드({self.profile_fields})가 없거나 형식이 올바르지 않습니다",
                           upstream_status=status)

    def _user_info(self, *, user_id: Optional[str], raw_email: Any, email_verified: bool, names: tuple[Any, ...],
                   status: int) -> OAuthUserInfo:
        # 식별자·이메일은 그대로 DB 키가 된다: 문자열이 아니거나(숫자·객체를 str()로 바꿔 쓰지 않는다) 한 줄이 아니거나
        # 컬럼 길이를 넘으면 저장 단계의 500 대신 제공자 오류(502)로 답한다. 값은 로그에 남기지 않는다.
        if not user_id or len(user_id) > _MAX_ID_CHARS:
            raise self._profile_error(status)
        if _is_missing(raw_email):
            # 사용자가 이메일 제공에 동의하지 않았거나 계정에 이메일이 없다: 제공자 오류가 아니라 사용자에게 알릴 400이다.
            raise OAuthEmailMissing(f"{self.label} 사용자 정보에 이메일이 없습니다")
        plain_email = _plain_text(raw_email) or ""
        if any(_confusable_email_char(ch) for ch in plain_email):
            raise self._profile_error(status)
        email = plain_email.lower()
        local_part, _, domain = email.partition("@")
        if not local_part or not domain or len(email) > _MAX_ID_CHARS:
            raise self._profile_error(status)
        return OAuthUserInfo(
            provider=self.provider_name,
            provider_user_id=user_id,
            email=email,
            email_verified=email_verified,
            name=_display_name(*names, fallback=local_part),
        )


class GoogleOAuthAdapter(_OAuthAdapter):
    """Google OAuth 2.0 Authorization Code + OIDC UserInfo 클라이언트."""

    provider_name = "google"
    label = "Google"
    supports_pkce = True
    requires_verified_email = True
    auth_url = _GOOGLE_AUTH_URL
    token_url = _GOOGLE_TOKEN_URL
    userinfo_url = _GOOGLE_USERINFO_URL
    profile_fields = "sub, email"

    def _authorize_params(self, *, state: str, redirect_uri: str) -> dict[str, str]:
        return {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _GOOGLE_SCOPE,
            "state": state,
            "prompt": "select_account",
        }

    def _token_form(self, *, code: str, redirect_uri: str, code_verifier: Optional[str],
                    state: Optional[str]) -> dict[str, str]:
        form = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        if code_verifier:
            form["code_verifier"] = code_verifier
        return form

    def _check_token_response(self, response: httpx.Response, body: Optional[dict[str, Any]]) -> None:
        # 2xx만 결과로 본다 (dxworks와 같음): 3xx 등은 본문에 access_token이 있어도 오류다.
        if 200 <= response.status_code < 300:
            return
        error_code = str((body or {}).get("error") or "")
        if response.status_code == 400 and error_code in _CLIENT_CODE_ERRORS:
            if error_code != "invalid_grant":
                # 사용자의 코드가 아니라 서버 설정 문제일 수 있다 (Google 콘솔에 등록한 redirect URI와 다름 등).
                # 응답은 그대로 400이므로 운영자가 원인을 볼 수 있게 오류 코드만 남긴다 (값은 남기지 않는다).
                logger.warning("Google OAuth token request rejected: error=%s", error_code)
            raise OAuthCodeError("Invalid or expired OAuth authorization code")
        raise self._token_failure(response, body)

    def _profile(self, body: dict[str, Any], status: int) -> OAuthUserInfo:
        return self._user_info(
            user_id=_plain_text(body.get("sub")),
            raw_email=body.get("email"),
            # JSON 불리언 true일 때만 인증된 이메일이다 (문자열 "true"·숫자 1은 아니다).
            email_verified=body.get("email_verified") is True,
            names=(body.get("name"),),
            status=status,
        )


class GitHubOAuthAdapter(_OAuthAdapter):
    """GitHub 로그인 (OAuth App, OAuth 2.0 Authorization Code) 클라이언트.

    - 인가 요청: scope user:email + PKCE(S256, 2025-07-14부터 지원). allow_signup·prompt·login은 보내지 않는다.
    - 토큰 응답은 Accept 헤더를 따른다 (기본은 form-encoded): Accept: application/json을 보낸다. 실패도 (보통) HTTP 200에
      본문의 error 키로 알린다.
    - 사용자 정보는 두 요청이다: GET /user(id·login·name)와 GET /user/emails(기본·인증 여부). /user의 email은 공개 프로필
      이메일일 수 있어 쓰지 않고, 이메일 목록에서 primary가 true인 항목만 쓴다 (verified가 true일 때만 인증된 이메일).
    - 토큰은 두 요청에만 쓰고 저장·로그하지 않고 버린다 (폐기 API는 부르지 않는다: 새 OAuth App의 토큰은 8시간이면 만료된다).
    """

    provider_name = "github"
    label = "GitHub"
    supports_pkce = True
    requires_verified_email = True
    auth_url = _GITHUB_AUTH_URL
    token_url = _GITHUB_TOKEN_URL
    userinfo_url = _GITHUB_USER_URL
    profile_fields = "id, email"
    token_headers = {"Accept": "application/json"}

    def _authorize_params(self, *, state: str, redirect_uri: str) -> dict[str, str]:
        return {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": _GITHUB_SCOPE,
            "state": state,
        }

    def _token_form(self, *, code: str, redirect_uri: str, code_verifier: Optional[str],
                    state: Optional[str]) -> dict[str, str]:
        # grant_type은 문서의 매개변수가 아니라 보내지 않는다. redirect_uri는 인가 요청의 값과 같아야 한다.
        form = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            form["code_verifier"] = code_verifier  # 인가 요청에 code_challenge를 실었으면 반드시 보내야 한다
        return form

    def _check_token_response(self, response: httpx.Response, body: Optional[dict[str, Any]]) -> None:
        # GitHub은 실패한 토큰 요청에도 HTTP 200으로 답할 수 있다 (오류 응답의 상태 코드는 문서에 없다): 상태 코드와 관계없이
        # error 키가 있으면 오류다 (access_token이 함께 와도 쓰지 않는다).
        status = response.status_code
        if body is not None and "error" in body:
            error_code = body.get("error")
            # 2xx·4xx 응답의 정해진 오류 코드(문자열)만 사용자 쪽 문제(400)로 본다. 3xx·5xx는 본문과 관계없이 제공자 오류다
            # (네이버와 같다). 문자열이 아닌 값(목록·객체 등)도 제공자 오류다.
            if isinstance(error_code, str) and (200 <= status < 300 or 400 <= status < 500):
                if error_code in _GITHUB_CODE_ERRORS:
                    if error_code != "bad_verification_code":
                        # 콜백 주소 불일치·PKCE 불일치 등은 서버 설정 문제일 수 있다: 오류 코드만 남긴다 (값은 남기지 않는다).
                        logger.warning("GitHub OAuth token request rejected: error=%s", error_code)
                    raise OAuthCodeError("Invalid or expired OAuth authorization code")
                if error_code == _GITHUB_UNVERIFIED_EMAIL:
                    # 기본 이메일이 인증되지 않은 GitHub 계정: 제공자 오류가 아니라 사용자에게 알릴 400이다 (api/oauth.py).
                    raise OAuthEmailUnverified("GitHub 계정의 기본 이메일이 인증되지 않았습니다")
            # incorrect_client_credentials(Client ID·secret 틀림) 등: 서버·제공자 쪽 문제다. 오류 코드만 남긴다.
            logger.warning("GitHub OAuth token request rejected: error=%s", _loggable_code(error_code))
            raise self._token_failure(response, body)
        if not 200 <= status < 300:
            raise self._token_failure(response, body)

    async def _fetch_userinfo(self, client: httpx.AsyncClient, access_token: str) -> OAuthUserInfo:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": _GITHUB_API_VERSION,
            "User-Agent": _GITHUB_USER_AGENT,
        }
        user, user_status = await self._get_object(client, _GITHUB_USER_URL, headers=headers)
        raw_id = user.get("id")
        # id는 JSON 정수(int64, 1 ~ 2^63-1)다. 불리언(true는 파이썬에서 int의 하위 형식)·실수·문자열·범위 밖의 값은
        # 쓰지 않는다 (다른 값을 str()로 바꿔 계정 키로 쓰지 않는다). 쓸 수 없으면 이메일 목록을 묻지 않는다.
        if not (type(raw_id) is int and 0 < raw_id <= _GITHUB_MAX_ID):
            raise self._profile_error(user_status)

        response = await self._get(client, _GITHUB_EMAILS_URL, headers=headers,
                                   params={"per_page": _GITHUB_EMAILS_PER_PAGE})
        status = response.status_code
        if status == 401:
            raise OAuthCodeError("Invalid OAuth access token")
        if status in (403, 404):
            # 사용자가 승인할 때 user:email 권한을 빼면(범위를 줄일 수 있다) 이메일 목록을 읽을 수 없다: 이메일 없음(400)이다.
            raise OAuthEmailMissing("GitHub 이메일 목록을 읽을 권한이 없습니다")
        if not 200 <= status < 300:
            raise self._error(f"GitHub 이메일 목록 조회 실패 ({status})", upstream_status=status)
        emails = _json_list(response)
        if emails is None:
            raise self._error("GitHub 이메일 목록 응답 형식이 올바르지 않습니다", upstream_status=status)
        # 기본 이메일(primary가 JSON true인 첫 항목)만 쓴다. 목록의 다른 이메일은 인증됐어도 쓰지 않는다.
        primary = next((entry for entry in map(_object, emails) if entry.get("primary") is True), None)
        if primary is None:
            raise OAuthEmailMissing("GitHub 이메일 목록에 기본 이메일이 없습니다")
        return self._user_info(
            user_id=str(raw_id),
            raw_email=primary.get("email"),
            # verified가 JSON true일 때만 인증된 이메일이다 (아니면 api/oauth.py가 400 'not verified'로 답한다).
            email_verified=primary.get("verified") is True,
            names=(user.get("name"), user.get("login")),
            status=status,
        )


class NaverOAuthAdapter(_OAuthAdapter):
    """네이버 로그인 (기존 OAuth 2.0 엔드포인트 /oauth2.0/*) 클라이언트.

    - PKCE는 이 엔드포인트의 문서에 없어 보내지 않는다 (supports_pkce=False).
    - 토큰 요청에는 인가 요청의 state를 함께 보낸다. redirect_uri는 토큰 요청 매개변수가 아니다.
    - 이메일 인증 여부를 알려 주지 않고, 사용자가 바꿀 수 있는 연락처 이메일이다: 항상 인증되지 않은 이메일로 본다.
    """

    provider_name = "naver"
    label = "Naver"
    supports_pkce = False
    requires_verified_email = False
    auth_url = _NAVER_AUTH_URL
    token_url = _NAVER_TOKEN_URL
    userinfo_url = _NAVER_USERINFO_URL
    profile_fields = "id, email"

    def _authorize_params(self, *, state: str, redirect_uri: str) -> dict[str, str]:
        return {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }

    def _token_form(self, *, code: str, redirect_uri: str, code_verifier: Optional[str],
                    state: Optional[str]) -> dict[str, str]:
        # 문서 예시는 모든 값을 주소(쿼리)에 싣지만, client_secret이 로그에 남지 않게 본문에만 싣는다 (_fetch_access_token).
        form = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": code,
        }
        if state:
            form["state"] = state
        return form

    def _check_token_response(self, response: httpx.Response, body: Optional[dict[str, Any]]) -> None:
        # 네이버는 HTTP 200에 {"error": …, "error_description": …}로 답할 수 있다: 상태 코드와 관계없이 error 키가 있으면 오류다.
        status = response.status_code
        if 200 <= status < 300 and not (body is not None and "error" in body):
            return
        error_code = str((body or {}).get("error") or "")
        # 2xx·4xx 응답의 정해진 오류 코드만 사용자의 코드 문제(400)로 본다. 3xx·5xx는 본문과 관계없이 제공자 오류다.
        if (200 <= status < 300 or 400 <= status < 500) and error_code in _NAVER_CODE_ERRORS:
            if error_code != "invalid_grant":
                # 잘못된 요청·클라이언트(시크릿 등)는 서버 설정 문제일 수 있다: 오류 코드만 남긴다 (값은 남기지 않는다).
                logger.warning("Naver OAuth token request rejected: error=%s", error_code)
            raise OAuthCodeError("Invalid or expired OAuth authorization code")
        raise self._token_failure(response, body)

    def _profile(self, body: dict[str, Any], status: int) -> OAuthUserInfo:
        resultcode = body.get("resultcode")
        if resultcode != "00":
            raise self._error(f"Naver 사용자 정보 조회 실패 (resultcode={str(resultcode)[:20]})",
                              upstream_status=status)
        profile = body.get("response")
        if not isinstance(profile, dict):
            raise self._profile_error(status)
        return self._user_info(
            user_id=_plain_text(profile.get("id")),
            raw_email=profile.get("email"),
            email_verified=False,
            names=(profile.get("name"), profile.get("nickname")),
            status=status,
        )

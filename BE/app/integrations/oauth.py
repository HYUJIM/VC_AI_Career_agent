"""OAuth 2.0 로그인 어댑터 (Google·Kakao·Naver).

제공자의 인가·토큰·사용자 정보 엔드포인트 형식은 이 파일에서만 다룬다. 공통 부분(접속 방식, 2xx만 결과, 헤더에 넣을 수 있는
access_token, 프로필 값 검사·정리)은 _OAuthAdapter에 두고, 제공자마다 다른 부분(인가·토큰 요청 매개변수, 토큰 오류 응답
해석, 프로필 필드 위치)만 각 클래스가 정한다.
"""
import logging
import re
import unicodedata
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.integrations.ports import OAuthCodeError, OAuthEmailMissing, OAuthUserInfo, ProviderError

logger = logging.getLogger(__name__)

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_GOOGLE_SCOPE = "openid email profile"
_CLIENT_CODE_ERRORS = frozenset({"invalid_grant", "invalid_request", "redirect_uri_mismatch"})
_KAKAO_AUTH_URL = "https://kauth.kakao.com/oauth/authorize"
_KAKAO_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
_KAKAO_USERINFO_URL = "https://kapi.kakao.com/v2/user/me"
# 카카오 회원번호(id)는 Long 범위의 양수다 (1 ~ 9223372036854775807, 카카오 보안 가이드라인).
_KAKAO_MAX_ID = 9223372036854775807
# 같은 인가 코드를 다시 썼거나 만료된 경우의 카카오 오류 코드 (사용자 쪽 문제라 경고를 남기지 않는다)
_KAKAO_REUSED_CODE = "KOE320"
_NAVER_AUTH_URL = "https://nid.naver.com/oauth2.0/authorize"
_NAVER_TOKEN_URL = "https://nid.naver.com/oauth2.0/token"
_NAVER_USERINFO_URL = "https://openapi.naver.com/v1/nid/me"
_NAVER_CODE_ERRORS = frozenset({"invalid_request", "unauthorized_client", "invalid_grant"})
# DB 컬럼 길이: users.email·oauth_accounts.email·oauth_accounts.provider_user_id는 VARCHAR(255), users.name은 VARCHAR(100)
_MAX_ID_CHARS = 255
_MAX_NAME_CHARS = 100
# 한 줄 텍스트에 둘 수 없는 문자: 제어 문자(Cc, NUL 포함)·줄 구분자(Zl)·문단 구분자(Zp)·짝 없는 서로게이트(Cs, DB에 저장할 수 없다)
_NOT_PLAIN_TEXT = frozenset({"Cc", "Zl", "Zp", "Cs"})
# 경고 로그에 그대로 남겨도 되는 제공자 오류 코드 모양 (예: KOE303). 다른 모양의 값은 남기지 않는다.
_LOGGABLE_CODE = re.compile(r"[A-Za-z0-9_.-]{1,40}")


def _json_dict(response: httpx.Response) -> Optional[dict[str, Any]]:
    try:
        data = response.json()
    except (ValueError, RecursionError):  # 아주 깊게 중첩된 본문(RecursionError)도 JSON이 아닌 본문으로 본다
        return None
    return data if isinstance(data, dict) else None


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
    (프로필 이름 때문에 로그인이 막히지 않게): 제어 문자·줄/문단 구분자는 공백으로 바꾸고, 이어진 공백은 하나로 줄이고,
    앞뒤 공백을 떼고 100자로 자른다. 후보(values)를 앞에서부터 보고 정리한 결과가 비어 있지 않은 첫 값을 쓰며,
    쓸 이름이 없으면 fallback(이메일의 @ 앞부분)을 쓴다."""
    for value in values:
        text = value if isinstance(value, str) else ""
        text = "".join(" " if unicodedata.category(ch) in _NOT_PLAIN_TEXT else ch for ch in text)
        text = " ".join(text.split())[:_MAX_NAME_CHARS].strip()
        if text:
            return text
    return fallback[:_MAX_NAME_CHARS]


def _confusable_email_char(ch: str) -> bool:
    """이메일에 두지 않는 문자.
    - 보이지 않는 서식 문자(Cf: U+200B 폭 없는 공백, U+202E 방향 바꿈 등): 화면에서 다른 계정 이메일과 똑같아 보인다.
    - 소문자로 바꾸면 ASCII가 되는 비ASCII 문자(U+212A 켈빈 기호 → k, U+0130 İ → i + 점): 이메일은 소문자로 바꿔
      비교·연동하므로, 다른(비ASCII) 메일함이 ASCII 계정과 같은 이메일로 읽힌다."""
    if unicodedata.category(ch) == "Cf":
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
        # 설정 이름은 제공자 이름을 따른다: google_client_id·kakao_client_secret 등. 호출 제한 시간은 세 제공자가 같이 쓴다.
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
        # 오류 코드도 코드 모양일 때만 넣는다 (카카오·네이버 경고 줄과 같은 기준).
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
            response = await client.post(self.token_url, data=form)
        except httpx.HTTPError as exc:
            raise self._error(f"{self.label} OAuth 연결 실패: {type(exc).__name__}") from exc

        body = _json_dict(response)
        self._check_token_response(response, body)

        access_token = body.get("access_token") if body else None
        # 토큰은 다음 요청의 Authorization 헤더에 그대로 들어간다: 헤더에 넣을 수 없는 값(ASCII가 아니거나 제어 문자)은
        # 요청을 만들다 나는 500 대신 제공자 오류(502)로 답한다.
        if not isinstance(access_token, str) or not access_token or not (
                access_token.isascii() and access_token.isprintable()):
            raise self._error(f"{self.label} OAuth 토큰 응답에 쓸 수 있는 access_token이 없습니다",
                              upstream_status=response.status_code)
        return access_token

    async def _fetch_userinfo(self, client: httpx.AsyncClient, access_token: str) -> OAuthUserInfo:
        try:
            response = await client.get(
                self.userinfo_url,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            raise self._error(f"{self.label} OAuth 연결 실패: {type(exc).__name__}") from exc

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
        return self._profile(body, response.status_code)

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


class KakaoOAuthAdapter(_OAuthAdapter):
    """카카오 로그인 (REST API, OAuth 2.0 Authorization Code) 클라이언트.

    - 인가 요청에 scope·prompt를 싣지 않는다: 동의 항목은 카카오 개발자 콘솔의 설정을 따른다.
    - PKCE(S256)를 보낸다: 카카오 OIDC 메타데이터가 code_challenge_methods_supported ["S256"]을 알린다.
    - 이메일은 '카카오계정(이메일)' 동의 항목이 있어야 온다 (비즈 앱만 설정할 수 있다). 없으면 OAuthEmailMissing.
    """

    provider_name = "kakao"
    label = "Kakao"
    supports_pkce = True
    requires_verified_email = True
    auth_url = _KAKAO_AUTH_URL
    token_url = _KAKAO_TOKEN_URL
    userinfo_url = _KAKAO_USERINFO_URL
    profile_fields = "id, email"

    def _authorize_params(self, *, state: str, redirect_uri: str) -> dict[str, str]:
        return {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": state,
        }

    def _token_form(self, *, code: str, redirect_uri: str, code_verifier: Optional[str],
                    state: Optional[str]) -> dict[str, str]:
        # client_secret_post: 클라이언트 시크릿은 REST API 키에 기본으로 켜져 있어 항상 보낸다.
        form = {
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "code": code,
            "client_secret": self.client_secret,
        }
        if code_verifier:
            form["code_verifier"] = code_verifier
        return form

    def _check_token_response(self, response: httpx.Response, body: Optional[dict[str, Any]]) -> None:
        if 200 <= response.status_code < 300:
            return
        error_code = str((body or {}).get("error") or "")
        if 400 <= response.status_code < 500 and error_code == "invalid_grant":
            # invalid_grant: 다시 쓴·만료된 코드(KOE320)나 redirect_uri 불일치(KOE303). KOE320이 아닌 코드는 서버 설정
            # 문제일 수 있어 오류 코드만 경고로 남긴다 (설명 문구·값은 남기지 않는다).
            kakao_code = (body or {}).get("error_code")
            if kakao_code is not None and kakao_code != _KAKAO_REUSED_CODE:
                shown = kakao_code if isinstance(kakao_code, str) and _LOGGABLE_CODE.fullmatch(kakao_code) else "?"
                logger.warning("Kakao OAuth token request rejected: error=%s error_code=%s", error_code, shown)
            raise OAuthCodeError("Invalid or expired OAuth authorization code")
        # invalid_client(KOE010 시크릿, KOE127 허용 IP)·invalid_request(KOE237 요청 한도) 등은 서버·제공자 쪽 문제다.
        raise self._token_failure(response, body)

    def _profile(self, body: dict[str, Any], status: int) -> OAuthUserInfo:
        raw_id = body.get("id")
        # 회원번호는 JSON 정수(Long, 1 ~ 2^63-1)다. 불리언(true는 파이썬에서 int의 하위 형식)·실수·문자열·범위 밖의 값은
        # 쓰지 않는다 (다른 값을 str()로 바꿔 계정 키로 쓰지 않는다).
        user_id = str(raw_id) if type(raw_id) is int and 0 < raw_id <= _KAKAO_MAX_ID else None
        account = _object(body.get("kakao_account"))
        return self._user_info(
            user_id=user_id,
            raw_email=account.get("email"),
            # 유효하고(다른 카카오계정에 쓰여 만료되지 않음) 인증된 이메일일 때만 인증된 이메일로 본다 (둘 다 JSON true).
            email_verified=account.get("is_email_valid") is True and account.get("is_email_verified") is True,
            names=(_object(account.get("profile")).get("nickname"), _object(body.get("properties")).get("nickname")),
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

"""Google OAuth 2.0 / OIDC 어댑터.

Google 인가·토큰·사용자 정보 엔드포인트 형식은 이 파일에서만 다룬다.
"""
import logging
import unicodedata
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.integrations.ports import OAuthCodeError, OAuthUserInfo, ProviderError

logger = logging.getLogger(__name__)

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_GOOGLE_SCOPE = "openid email profile"
_CLIENT_CODE_ERRORS = frozenset({"invalid_grant", "invalid_request", "redirect_uri_mismatch"})
# DB 컬럼 길이: users.email·oauth_accounts.email·oauth_accounts.provider_user_id는 VARCHAR(255), users.name은 VARCHAR(100)
_MAX_ID_CHARS = 255
_MAX_NAME_CHARS = 100
# 한 줄 텍스트에 둘 수 없는 문자: 제어 문자(Cc, NUL 포함)·줄 구분자(Zl)·문단 구분자(Zp)·짝 없는 서로게이트(Cs, DB에 저장할 수 없다)
_NOT_PLAIN_TEXT = frozenset({"Cc", "Zl", "Zp", "Cs"})


def _error(message: str, **kwargs: Any) -> ProviderError:
    """Google 쪽 실패. main.py가 발급·검증 인프라 문구가 아니라 Google 로그인 문구로 답하도록 표시한다."""
    return ProviderError(message, service="google_oauth", **kwargs)


def _json_dict(response: httpx.Response) -> Optional[dict[str, Any]]:
    try:
        data = response.json()
    except (ValueError, RecursionError):  # 아주 깊게 중첩된 본문(RecursionError)도 JSON이 아닌 본문으로 본다
        return None
    return data if isinstance(data, dict) else None


def _plain_text(value: Any) -> Optional[str]:
    """제공자가 준 식별자·이메일: 앞뒤 공백을 뗀 값이 비어 있지 않은 한 줄 문자열이면 그 값, 아니면 None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(unicodedata.category(ch) in _NOT_PLAIN_TEXT for ch in value):
        return None
    return value


def _display_name(value: Any, fallback: str) -> str:
    """Google 프로필 이름 정리. 가입 API는 한 줄이 아닌 이름을 거절하지만, 여기서는 거절하지 않고 고친다
    (프로필 이름 때문에 로그인이 막히지 않게): 제어 문자·줄/문단 구분자는 공백으로 바꾸고, 이어진 공백은 하나로 줄이고,
    앞뒤 공백을 떼고 100자로 자른다. 쓸 이름이 없으면 fallback(이메일의 @ 앞부분)을 쓴다."""
    text = value if isinstance(value, str) else ""
    text = "".join(" " if unicodedata.category(ch) in _NOT_PLAIN_TEXT else ch for ch in text)
    text = " ".join(text.split())[:_MAX_NAME_CHARS].strip()
    return text or fallback[:_MAX_NAME_CHARS]


class GoogleOAuthAdapter:
    """Google OAuth 2.0 Authorization Code + OIDC UserInfo 클라이언트."""

    provider_name = "google"

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
    def from_settings(cls, settings: Settings) -> "GoogleOAuthAdapter":
        return cls(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            timeout=settings.google_oauth_timeout_seconds,
        )

    def _ensure_configured(self) -> None:
        if not self.client_id or not self.client_secret:
            raise _error("Google OAuth is not configured", unavailable=True)

    def build_authorize_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
    ) -> str:
        self._ensure_configured()
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _GOOGLE_SCOPE,
            "state": state,
            "prompt": "select_account",
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = code_challenge_method or "S256"
        return f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None,
    ) -> OAuthUserInfo:
        self._ensure_configured()
        # 주변 프록시 환경변수(HTTP(S)_PROXY·ALL_PROXY)·SSL_CERT_FILE을 쓰지 않고 리다이렉트도 따라가지 않는다
        # (dxworks·ai_service와 같음). 이 요청에는 client_secret·인가 코드·Google 액세스 토큰이 실린다.
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport,
                                     follow_redirects=False, trust_env=False) as client:
            access_token = await self._fetch_access_token(
                client, code=code, redirect_uri=redirect_uri, code_verifier=code_verifier
            )
            return await self._fetch_userinfo(client, access_token)

    async def _fetch_access_token(
        self,
        client: httpx.AsyncClient,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str],
    ) -> str:
        form = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        if code_verifier:
            form["code_verifier"] = code_verifier

        try:
            response = await client.post(_GOOGLE_TOKEN_URL, data=form)
        except httpx.HTTPError as exc:
            raise _error(f"Google OAuth 연결 실패: {type(exc).__name__}") from exc

        body = _json_dict(response)
        # 2xx만 결과로 본다 (dxworks와 같음): 3xx 등은 본문에 access_token이 있어도 오류다.
        if not 200 <= response.status_code < 300:
            error_code = str((body or {}).get("error") or "")
            description = str((body or {}).get("error_description") or response.reason_phrase or "")
            if response.status_code == 400 and error_code in _CLIENT_CODE_ERRORS:
                if error_code != "invalid_grant":
                    # 사용자의 코드가 아니라 서버 설정 문제일 수 있다 (Google 콘솔에 등록한 redirect URI와 다름 등).
                    # 응답은 그대로 400이므로 운영자가 원인을 볼 수 있게 오류 코드만 남긴다 (값은 남기지 않는다).
                    logger.warning("Google OAuth token request rejected: error=%s", error_code)
                raise OAuthCodeError("Invalid or expired OAuth authorization code")
            raise _error(
                f"Google OAuth 토큰 오류 ({error_code or response.status_code}): {description}".strip(),
                upstream_status=response.status_code,
            )

        access_token = body.get("access_token") if body else None
        # 토큰은 다음 요청의 Authorization 헤더에 그대로 들어간다: 헤더에 넣을 수 없는 값(ASCII가 아니거나 제어 문자)은
        # 요청을 만들다 나는 500 대신 제공자 오류(502)로 답한다.
        if not isinstance(access_token, str) or not access_token or not (
                access_token.isascii() and access_token.isprintable()):
            raise _error("Google OAuth 토큰 응답에 쓸 수 있는 access_token이 없습니다", upstream_status=response.status_code)
        return access_token

    async def _fetch_userinfo(self, client: httpx.AsyncClient, access_token: str) -> OAuthUserInfo:
        try:
            response = await client.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            raise _error(f"Google OAuth 연결 실패: {type(exc).__name__}") from exc

        if response.status_code == 401:
            raise OAuthCodeError("Invalid OAuth access token")
        body = _json_dict(response)
        if not 200 <= response.status_code < 300:
            raise _error(
                f"Google 사용자 정보 조회 실패 ({response.status_code})",
                upstream_status=response.status_code,
            )
        if not body:
            raise _error("Google 사용자 정보 응답 형식이 올바르지 않습니다", upstream_status=response.status_code)

        # sub·email은 그대로 DB 키가 된다: 문자열이 아니거나(숫자·객체를 str()로 바꿔 쓰지 않는다) 한 줄이 아니거나
        # 컬럼 길이를 넘으면 저장 단계의 500 대신 제공자 오류(502)로 답한다. 값은 로그에 남기지 않는다.
        sub = _plain_text(body.get("sub"))
        email = (_plain_text(body.get("email")) or "").lower()
        local_part, _, domain = email.partition("@")
        if not sub or len(sub) > _MAX_ID_CHARS or not local_part or not domain or len(email) > _MAX_ID_CHARS:
            raise _error("Google 사용자 정보의 필수 필드(sub, email)가 없거나 형식이 올바르지 않습니다",
                         upstream_status=response.status_code)

        return OAuthUserInfo(
            provider=self.provider_name,
            provider_user_id=sub,
            email=email,
            email_verified=body.get("email_verified") is True,
            name=_display_name(body.get("name"), local_part),
        )

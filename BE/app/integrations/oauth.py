"""Google OAuth 2.0 / OIDC 어댑터.

Google 인가·토큰·사용자 정보 엔드포인트 형식은 이 파일에서만 다룬다.
"""
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.integrations.ports import OAuthCodeError, OAuthUserInfo, ProviderError

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
_GOOGLE_SCOPE = "openid email profile"
_CLIENT_CODE_ERRORS = frozenset({"invalid_grant", "invalid_request", "redirect_uri_mismatch"})


def _json_dict(response: httpx.Response) -> Optional[dict[str, Any]]:
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


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
            raise ProviderError("Google OAuth is not configured", unavailable=True)

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
        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
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
            raise ProviderError(f"Google OAuth 연결 실패: {type(exc).__name__}") from exc

        body = _json_dict(response)
        if response.status_code >= 400:
            error_code = str((body or {}).get("error") or "")
            description = str((body or {}).get("error_description") or response.reason_phrase or "")
            if response.status_code == 400 and error_code in _CLIENT_CODE_ERRORS:
                raise OAuthCodeError("Invalid or expired OAuth authorization code")
            raise ProviderError(
                f"Google OAuth 토큰 오류 ({error_code or response.status_code}): {description}".strip(),
                upstream_status=response.status_code,
            )

        if not body or not isinstance(body.get("access_token"), str) or not body["access_token"]:
            raise ProviderError("Google OAuth 토큰 응답에 access_token이 없습니다", upstream_status=response.status_code)
        return body["access_token"]

    async def _fetch_userinfo(self, client: httpx.AsyncClient, access_token: str) -> OAuthUserInfo:
        try:
            response = await client.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"Google OAuth 연결 실패: {type(exc).__name__}") from exc

        if response.status_code == 401:
            raise OAuthCodeError("Invalid OAuth access token")
        body = _json_dict(response)
        if response.status_code >= 400:
            raise ProviderError(
                f"Google 사용자 정보 조회 실패 ({response.status_code})",
                upstream_status=response.status_code,
            )
        if not body:
            raise ProviderError("Google 사용자 정보 응답 형식이 올바르지 않습니다", upstream_status=response.status_code)

        sub = str(body.get("sub") or "").strip()
        email = str(body.get("email") or "").strip().lower()
        if not sub or not email:
            raise ProviderError("Google 사용자 정보에 필수 필드(sub, email)가 없습니다", upstream_status=response.status_code)

        raw_name = str(body.get("name") or "").strip()
        name = (raw_name or email.split("@", 1)[0])[:100]
        return OAuthUserInfo(
            provider=self.provider_name,
            provider_user_id=sub,
            email=email,
            email_verified=body.get("email_verified") is True,
            name=name,
        )

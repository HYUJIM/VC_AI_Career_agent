"""벤더 격리 경계 (계획서 2.1 Vendor Isolation).

API/서비스 계층은 이 포트에만 의존한다. DXWorks 응답 형식은 app/integrations/dxworks.py 안에서만 다룬다.
다른 발급·검증 인프라로 바꿀 때는 이 포트를 구현하는 어댑터만 추가하면 된다.
"""
from dataclasses import dataclass
import unicodedata
from typing import Optional, Protocol

from app.domain.credential import IssuedBadge, PresentationResult, ProviderBadgeResult

_MAX_MESSAGE_CHARS = 300


def _log_safe(text: str) -> str:
    """로그 한 줄용: 제어·서식 문자(줄바꿈·ESC·방향 제어·짝 없는 서로게이트 등)와 줄·문단 구분자를 공백으로 바꾸고
    길이를 제한한다."""
    text = str(text)[:_MAX_MESSAGE_CHARS]
    return "".join(" " if unicodedata.category(ch)[0] == "C" or ch in "\u2028\u2029" else ch for ch in text)


class ProviderError(Exception):
    """외부 발급·검증·인증 인프라 호출 실패.

    message는 운영 로그 전용이다. 협력사/제공자 오류 문구가 들어갈 수 있어 한 줄·길이 제한으로 정리하고,
    응답에는 싣지 않는다 (main.py가 고정 문구로 답한다).
    """

    def __init__(self, message: str, upstream_status: Optional[int] = None, unavailable: bool = False,
                 ambiguous: bool = False, service: str = "credential"):
        message = _log_safe(message)
        super().__init__(message)
        self.message = message
        self.upstream_status = upstream_status
        # 어느 외부 서비스의 실패인지: "credential"(발급·검증 인프라, 기본값) | "google_oauth".
        # main.py가 응답의 고정 문구를 고를 때만 쓴다 (상태 코드·code·로그 줄은 같다).
        self.service = service
        # True면 설정 누락 등으로 호출 자체를 할 수 없는 상태 (503)
        self.unavailable = unavailable
        # True면 요청이 협력사에 닿았을 수 있는데 결과를 모르는 상태 (응답 대기 중 시간 초과·끊김, 쓸 수 없는 2xx 본문).
        # 발급은 이미 됐을 수 있으므로 모의 발급은 이 경우 한도 기록을 지우지 않는다 (api/mock_issuer.py).
        self.ambiguous = ambiguous


class OAuthCodeError(Exception):
    """유효하지 않거나 만료된 OAuth 인가 코드/토큰."""


@dataclass(frozen=True)
class OAuthUserInfo:
    provider: str
    provider_user_id: str
    email: str
    email_verified: bool
    name: str


class OAuthProvider(Protocol):
    provider_name: str

    def build_authorize_url(
        self,
        *,
        state: str,
        redirect_uri: str,
        code_challenge: Optional[str] = None,
        code_challenge_method: Optional[str] = None,
    ) -> str: ...

    async def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: Optional[str] = None,
    ) -> OAuthUserInfo: ...


class CredentialVerifier(Protocol):
    provider_name: str

    async def verify_badge(self, vc_jwt: str) -> ProviderBadgeResult: ...

    async def verify_presentation(self, vp_token: str) -> PresentationResult: ...


class CredentialIssuer(Protocol):
    async def issue_badge(self, template_id: str, holder_did: str) -> IssuedBadge: ...

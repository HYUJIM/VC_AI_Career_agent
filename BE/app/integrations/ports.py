"""벤더 격리 경계 (계획서 2.1 Vendor Isolation).

API/서비스 계층은 이 포트에만 의존한다. DXWorks 응답 형식은 app/integrations/dxworks.py 안에서만 다룬다.
다른 발급·검증 인프라로 바꿀 때는 이 포트를 구현하는 어댑터만 추가하면 된다.
"""
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
    """외부 발급·검증 인프라 호출 실패.

    message는 운영 로그 전용이다. 협력사 오류 문구가 들어갈 수 있어 한 줄·길이 제한으로 정리하고,
    응답에는 싣지 않는다 (main.py가 고정 문구로 답한다).
    """

    def __init__(self, message: str, upstream_status: Optional[int] = None, unavailable: bool = False):
        message = _log_safe(message)
        super().__init__(message)
        self.message = message
        self.upstream_status = upstream_status
        # True면 설정 누락 등으로 호출 자체를 할 수 없는 상태 (503)
        self.unavailable = unavailable


class CredentialVerifier(Protocol):
    provider_name: str

    async def verify_badge(self, vc_jwt: str) -> ProviderBadgeResult: ...

    async def verify_presentation(self, vp_token: str) -> PresentationResult: ...


class CredentialIssuer(Protocol):
    async def issue_badge(self, template_id: str, holder_did: str) -> IssuedBadge: ...

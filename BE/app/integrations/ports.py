"""벤더 격리 경계 (계획서 2.1 Vendor Isolation).

API/서비스 계층은 이 포트에만 의존한다. DXWorks 응답 형식은 app/integrations/dxworks.py 안에서만 다룬다.
다른 발급·검증 인프라로 바꿀 때는 이 포트를 구현하는 어댑터만 추가하면 된다.
"""
from typing import Optional, Protocol

from app.domain.credential import IssuedBadge, PresentationResult, ProviderBadgeResult


class ProviderError(Exception):
    """외부 발급·검증 인프라 호출 실패."""

    def __init__(self, message: str, upstream_status: Optional[int] = None, unavailable: bool = False):
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

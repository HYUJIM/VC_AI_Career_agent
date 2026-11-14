"""DXWorks 발급·검증 API 어댑터.

협력사 API의 경로·요청·응답 형식은 이 파일에서만 다룬다. 나머지 코드는 ports.py의 포트와
domain 모델만 사용한다. 접속 정보(호스트, API 키, 템플릿 ID)는 비공개 .env에서만 읽는다.
"""
import asyncio
from typing import Any, Optional

import httpx

from app.core.config import Settings
from app.domain.credential import IssuedBadge, PresentationResult, ProviderBadgeResult
from app.integrations.ports import ProviderError

_PATH_ISSUE_BADGE = "/api/v1/issuer/ob3/credentials"
_PATH_VERIFY_BADGE = "/api/v1/verifier/ob3/verify"
_PATH_VERIFY_PRESENTATION = "/api/v1/verifier/holder-presentations/verify"
_CONNECT_RETRIES = 1
_RETRY_DELAY_SECONDS = 0.5
# 평문 http는 같은 기기·태스크 안의 루프백 주소(로컬 테스트 서버 등)만 허용한다 (ai_service.LOOPBACK_HOSTS와 같은 목록).
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _unwrap(body: Any) -> dict:
    # 응답 본문이 {"data": {...}}로 감싸진 경우와 그렇지 않은 경우를 모두 받는다.
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    if isinstance(body, dict):
        return body
    raise ProviderError("DXWorks 응답 형식이 올바르지 않습니다")


def _equals(value: Any, expected: str) -> Optional[bool]:
    if value is None:
        return None
    return str(value).upper() == expected


def _is_secure_host(host: str) -> bool:
    """https 주소이거나 루프백 http 주소인지. 요청에 쓰는 것과 같은 파서(httpx.URL)로 스킴·호스트를 확인한다."""
    try:
        url = httpx.URL(host)
        scheme, hostname = url.scheme, url.host
    except (httpx.InvalidURL, ValueError):  # 해석할 수 없는 주소 (IDNA 오류 포함)
        return False
    if scheme == "https":
        return bool(hostname)
    return scheme == "http" and hostname in _LOOPBACK_HOSTS


class DXWorksAdapter:
    provider_name = "dxworks_ob3"

    def __init__(self, host: str, api_key: str, timeout: float = 15.0,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        self.host = host.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport

    @classmethod
    def from_settings(cls, settings: Settings) -> "DXWorksAdapter":
        return cls(settings.dxworks_api_host, settings.dxworks_api_key, settings.dxworks_timeout_seconds)

    async def _post(self, path: str, payload: dict, *, auth: bool) -> dict:
        if not self.host:
            raise ProviderError("DXWORKS_API_HOST가 설정되지 않았습니다", unavailable=True)
        if not _is_secure_host(self.host):
            # 회사 API 키와 사용자 VC-JWT를 평문으로 보내지 않는다. 메시지에 호스트 값은 넣지 않는다.
            raise ProviderError("DXWORKS_API_HOST는 https:// 주소여야 합니다", unavailable=True)
        headers = {"Content-Type": "application/json"}
        if auth:
            if not self.api_key:
                raise ProviderError("DXWORKS_API_KEY가 설정되지 않았습니다", unavailable=True)
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = None
        for attempt in range(1 + _CONNECT_RETRIES):
            try:
                # 주변 프록시 환경변수(HTTP(S)_PROXY·ALL_PROXY)·SSL_CERT_FILE을 쓰지 않고 리다이렉트도 따라가지 않는다
                # (ai_service와 같음).
                async with httpx.AsyncClient(base_url=self.host, timeout=self.timeout, transport=self.transport,
                                             follow_redirects=False, trust_env=False) as client:
                    response = await client.post(path, json=payload, headers=headers)
                break
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                # 연결 자체가 안 된 경우만 재시도한다. 요청이 서버에 닿지 않았으므로 발급 요청도 중복되지 않는다.
                if attempt < _CONNECT_RETRIES:
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                raise ProviderError(f"DXWorks 연결 실패: {type(exc).__name__}") from exc
            except httpx.HTTPError as exc:
                # 응답 대기 중 시간 초과 등은 서버가 이미 처리했을 수 있어 재시도하지 않는다.
                raise ProviderError(f"DXWorks 연결 실패: {type(exc).__name__}") from exc

        try:
            body = response.json()
        except (ValueError, RecursionError):
            # JSON이 아니거나 너무 깊게 중첩되어 해석할 수 없는 본문은 JSON이 아닌 응답으로 본다 (RecursionError로 500이 나지 않게).
            body = None

        # 2xx만 검증·발급 결과로 읽는다. 리다이렉트(3xx)는 따라가지 않고, 본문이 JSON이어도 결과로 쓰지 않고 오류로 끝낸다.
        if not (200 <= response.status_code < 300):
            error = _dict(_dict(body).get("error"))
            code = error.get("code") or response.status_code
            message = error.get("message") or response.reason_phrase
            raise ProviderError(f"DXWorks 오류 ({code}): {message}", upstream_status=response.status_code)
        if body is None:
            raise ProviderError("DXWorks가 JSON이 아닌 응답을 반환했습니다", upstream_status=response.status_code)
        try:
            return _unwrap(body)
        except ProviderError as exc:
            # 응답은 받았으므로 상태 코드를 붙인다 (upstream_status None은 "응답 없음"에만 쓴다: reverify.is_verifier_outage).
            raise ProviderError(exc.message, upstream_status=response.status_code) from None

    async def verify_badge(self, vc_jwt: str) -> ProviderBadgeResult:
        data = await self._post(_PATH_VERIFY_BADGE, {"vcJwt": vc_jwt}, auth=False)
        # 요약 판정(verification)이 있으면 우선 쓰고, 없으면 항목별 결과(checks)로 판정한다.
        summary = _dict(data.get("verification"))
        checks = _dict(data.get("checks"))
        issuer = _dict(checks.get("issuer"))
        trust = _dict(checks.get("issuerTrust"))
        credential = _dict(checks.get("credentialId"))
        recipient = _dict(checks.get("recipient"))
        badge = _dict(data.get("badge"))

        signature_ok = _equals(summary.get("proof"), "VALID")
        if signature_ok is None:
            signature_ok = _equals(issuer.get("status"), "OK")

        issuer_trusted = _equals(summary.get("trust"), "TRUSTED")
        if issuer_trusted is None and isinstance(trust.get("trusted"), bool):
            issuer_trusted = trust["trusted"]

        return ProviderBadgeResult(
            valid=data.get("valid") is True,
            signature_ok=signature_ok,
            issuer_trusted=issuer_trusted,
            credential_status=summary.get("status") or credential.get("credentialStatus"),
            recipient_did=recipient.get("subjectId") or badge.get("subjectId"),
            credential_id=credential.get("id"),
            issuer_name=issuer.get("issuerName") or badge.get("issuerName"),
            achievement_name=badge.get("name"),
            issued_at=badge.get("issuedAt"),
            raw=data,
        )

    async def verify_presentation(self, vp_token: str) -> PresentationResult:
        data = await self._post(_PATH_VERIFY_PRESENTATION, {"vpToken": vp_token}, auth=True)
        holder_did = data.get("holderDid")
        return PresentationResult(
            verified=data.get("verified") is True,
            # 문자열이 아니거나 비어 있으면(공백뿐인 값 포함) 홀더를 모르는 것으로 둔다 (API가 403으로 거절한다).
            holder_did=holder_did if isinstance(holder_did, str) and holder_did.strip() else None,
            claims=_dict(data.get("claims")),
            raw=data,
        )

    async def issue_badge(self, template_id: str, holder_did: str) -> IssuedBadge:
        data = await self._post(_PATH_ISSUE_BADGE, {"templateId": template_id, "holderDid": holder_did}, auth=True)
        vc_jwt = data.get("jwt")
        if not isinstance(vc_jwt, str) or not vc_jwt:
            raise ProviderError("DXWorks 발급 응답에 jwt가 없습니다")
        return IssuedBadge(vc_jwt=vc_jwt, credential_id=data.get("id"), raw=data)

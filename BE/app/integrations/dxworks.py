"""DXWorks 발급·검증 API 어댑터.

협력사 API의 경로·요청·응답 형식은 이 파일에서만 다룬다. 나머지 코드는 ports.py의 포트와
domain 모델만 사용한다. 접속 정보(호스트, API 키, 템플릿 ID)는 비공개 .env에서만 읽는다.
"""
import asyncio
import json
import math
import re
import weakref
import zlib
from typing import Any, Optional

import httpx

from app.core.config import Settings
from app.domain.credential import (
    IssuedBadge, PresentationResult, ProviderBadgeResult, _finite_float, _reject_constant,
)
from app.integrations.capped_body import BodyTooLarge, CappedBody, UnsupportedEncoding
from app.integrations.ports import ProviderError

_PATH_ISSUE_BADGE = "/api/v1/issuer/ob3/credentials"
_PATH_VERIFY_BADGE = "/api/v1/verifier/ob3/verify"
_PATH_VERIFY_PRESENTATION = "/api/v1/verifier/holder-presentations/verify"
_CONNECT_RETRIES = 1
_RETRY_DELAY_SECONDS = 0.5
# 평문 http는 같은 기기·태스크 안의 루프백 주소(로컬 테스트 서버 등)만 허용한다 (ai_service.LOOPBACK_HOSTS와 같은 목록).
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
# 프로세스(태스크) 하나가 협력사에 동시에 보내는 요청 수 상한 (배지 검증·VP 검증·발급 공통). 요청이 아무리 몰려도 이보다 많이
# 동시에 부르지 않는다. 자리가 날 때까지 기다리는 시간은 timeout × 2까지다 (그 뒤의 교환도 timeout × 2까지: _post 참고).
MAX_IN_FLIGHT = 16
# 협력사 응답 본문 상한(바이트). 실제 검증·발급 응답은 수 KB다. 넘으면 끝까지 읽지 않고 오류로 끝낸다.
# 압축된 응답(gzip·deflate)은 전송된 바이트와 풀어낸 바이트를 각각 이 값까지만 받는다 (capped_body.py).
MAX_RESPONSE_BYTES = 1024 * 1024
# 협력사 오류 문구에 되돌아오면 가리는 요청 값 (서명 원본·VP·홀더 DID). API 키도 함께 가린다.
_SENSITIVE_FIELDS = ("vcJwt", "vpToken", "holderDid")
_REDACTED = "[redacted]"
# 협력사가 값을 잘라서 되돌려 줄 때: 앞에서 이만큼 같으면 그 값의 앞부분으로 보고 이어지는 부분까지 가린다.
_ECHO_PREFIX_CHARS = 16
# 보낸 값과 글자가 똑같지 않아도(토큰에서 꺼낸 DID, 페이로드 조각만, 대문자로 바꾼 값 등) 모양으로 가리는 것:
# DID(did:<method>:<id>, 대소문자 무시), JWT처럼 보이는 문자열(eyJ로 시작하는 base64url과 점), 그리고 32자 이상 이어진
# base64url 가운데 숫자가 하나라도 든 것(_LONG_RUN).
# - 가려지는 것: 키·서명·해시·토큰 조각, 그리고 협력사가 문구에 넣은 UUID·추적 ID·요청 ID처럼 숫자가 섞인 32자 이상의 값
#   (로그에는 [redacted]로 남는다).
# - 남는 것: 글자와 _·-로만 된 낱말 모양의 식별자. 오류 코드·슬러그(CREDENTIAL_STATUS_LIST_VERIFICATION_FAILED,
#   issuer_did_document_resolution_timeout_exceeded 등)는 길어도 운영자가 읽을 수 있게 그대로 둔다.
_REDACT_PATTERNS = (
    re.compile(r"(?<![a-z0-9])did:[a-z0-9]+:[A-Za-z0-9._:%-]+", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]*)*"),
)
_LONG_RUN = re.compile(r"[A-Za-z0-9_-]{32,}")
_DIGIT = re.compile(r"[0-9]")
# asyncio.Semaphore는 처음 기다린 이벤트 루프에 묶이므로(테스트는 루프를 여러 개 만든다) 모듈 전역 하나가 아니라
# 도는 루프마다 하나씩 둔다. 운영에서는 프로세스에 루프가 하나라 프로세스 전체 상한이다.
# 키는 약한 참조지만, 한 번이라도 기다린 호출이 있던 세마포어는 자기 루프를 강하게 붙잡고 있어(값이 키를 참조) 루프가 닫혀도
# 저절로 지워지지 않는다. 그래서 새 루프의 세마포어를 만들 때 닫힌 루프의 항목을 지운다.
_in_flight: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def _in_flight_slots() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    slots = _in_flight.get(loop)
    if slots is None:
        for known in [known for known in _in_flight if known.is_closed()]:
            del _in_flight[known]
        slots = _in_flight[loop] = asyncio.Semaphore(MAX_IN_FLIGHT)
    return slots


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


def is_valid_host_setting(host: str) -> bool:
    """DXWORKS_API_HOST 설정값 검사 (서버를 시작할 때 한 번(core.config), 그리고 호출할 때마다).

    https·루프백 규칙(_is_secure_host)에 더해 사용자 정보(user:pw@), 쿼리(?), 프래그먼트(#), 공백이 없어야 한다:
    사용자 정보는 Basic 인증 헤더로 나가고, 쿼리·프래그먼트가 있으면 경로가 그 뒤에 붙어 다른 주소를 부르게 된다.
    포트는 없거나 1~65535여야 한다 (범위 밖의 포트는 연결 단계에서 httpx 예외가 아닌 오류가 난다).
    """
    if not _is_secure_host(host) or any(ch.isspace() for ch in host) or "?" in host or "#" in host:
        return False
    url = httpx.URL(host)
    return not url.userinfo and (url.port is None or 1 <= url.port <= 65535)


def _redact_echo(text: str, secret: str) -> str:
    """text 안에 되돌아온 secret(전체, 또는 잘린 앞부분 _ECHO_PREFIX_CHARS자 이상)을 [redacted]로 바꾼다."""
    head = secret[:_ECHO_PREFIX_CHARS]
    parts, start = [], 0
    while True:
        at = text.find(head, start)
        if at < 0:
            break
        end = at + len(head)
        while end - at < len(secret) and end < len(text) and text[end] == secret[end - at]:
            end += 1
        parts += [text[start:at], _REDACTED]
        start = end
    parts.append(text[start:])
    return "".join(parts)


def _redact_long_run(match: "re.Match[str]") -> str:
    """_LONG_RUN에 맞은 조각 하나: 숫자가 들어 있으면 가리고, 글자와 _·-뿐이면 그대로 둔다.

    식에 미리보기(look-ahead)를 넣지 않고 맞은 조각을 한 번 더 훑는다: 걸리는 시간이 입력 길이에 비례한다.
    """
    run = match.group()
    return _REDACTED if _DIGIT.search(run) else run


def _declared_length(response: httpx.Response) -> int:
    try:
        return int(response.headers.get("Content-Length", "0"))
    except ValueError:
        return 0


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

    def _redact(self, text: Any, payload: dict) -> str:
        """협력사 오류 코드·문구에 우리가 보낸 값(API 키, 서명 원본·VP·홀더 DID)이 되돌아와 있으면 가린다.

        이 문구는 운영 로그에 남는다 (main.py 'Provider error', reverify 'Credential re-verification failed').
        길이를 자르기(ProviderError) 전에 가려야 잘린 앞부분이 남지 않는다.
        보낸 값 그대로가 아닌 것(토큰에서 꺼낸 DID, 페이로드 조각 등)은 모양으로 한 번 더 가린다 (_REDACT_PATTERNS,
        숫자가 섞인 32자 이상의 base64url). 오류 코드와 문구에 같은 규칙을 쓴다.
        """
        text = str(text)
        for secret in (self.api_key, *(payload.get(name) for name in _SENSITIVE_FIELDS)):
            if isinstance(secret, str) and secret:
                text = _redact_echo(text, secret)
        for pattern in _REDACT_PATTERNS:
            text = pattern.sub(_REDACTED, text)
        return _LONG_RUN.sub(_redact_long_run, text)

    async def _exchange(self, path: str, payload: dict, headers: dict) -> tuple[int, str, bytes]:
        """요청 하나를 보내고 (상태 코드, 사유 문구, 본문)을 돌려준다. 본문은 MAX_RESPONSE_BYTES까지만 읽는다."""
        # 주변 프록시 환경변수(HTTP(S)_PROXY·ALL_PROXY)·SSL_CERT_FILE을 쓰지 않고 리다이렉트도 따라가지 않는다
        # (ai_service와 같음).
        async with httpx.AsyncClient(base_url=self.host, timeout=self.timeout, transport=self.transport,
                                     follow_redirects=False, trust_env=False) as client:
            async with client.stream("POST", path, json=payload, headers=headers) as response:
                status = response.status_code
                # 2xx면 협력사가 요청을 처리했을 수 있다: 본문을 읽지 못해도 발급은 됐을 수 있으므로 결과를 모르는 실패로 표시한다.
                unknown = 200 <= status < 300
                # 압축을 요청하지 않았지만(Accept-Encoding: identity) 협력사·CDN이 gzip·deflate로 답하면 상한 안에서만 직접 푼다
                # (httpx에 맡기면 수백 KiB짜리 gzip 본문이 수백 MiB로 풀린다). 그 밖의 방식(br·zstd·겹친 압축 등)은 받지 않는다.
                try:
                    body = CappedBody(response.headers.get("Content-Encoding", ""), MAX_RESPONSE_BYTES)
                except UnsupportedEncoding:
                    raise ProviderError("DXWorks가 요청하지 않은 압축 응답을 반환했습니다", upstream_status=status,
                                        ambiguous=unknown) from None
                too_large = ProviderError("DXWorks 응답 본문이 너무 큽니다", upstream_status=status, ambiguous=unknown)
                if _declared_length(response) > MAX_RESPONSE_BYTES:
                    raise too_large
                try:
                    if response.is_stream_consumed:
                        # 전송 계층이 본문을 이미 다 읽어 둔 응답 (본문을 통째로 넣어 만든 httpx.Response: 테스트용 MockTransport).
                        # httpx가 그때 이미 풀어 두었으므로 다시 풀지 않고 크기만 본다.
                        body = CappedBody("", MAX_RESPONSE_BYTES)
                        body.feed(response.content)
                    else:
                        async for chunk in response.aiter_raw():  # 전송된 그대로 (httpx가 풀지 않은 바이트)
                            body.feed(chunk)
                    content = body.finish()
                except BodyTooLarge:
                    raise too_large from None
                except zlib.error:
                    content = b""  # 풀 수 없는 압축 본문: JSON이 아닌 응답으로 처리된다 (_post)
                return status, response.reason_phrase, content

    async def _post(self, path: str, payload: dict, *, auth: bool) -> tuple[dict, int]:
        """(응답 본문, HTTP 상태 코드)를 돌려준다. 2xx가 아니거나 본문을 결과로 쓸 수 없으면 ProviderError."""
        if not self.host:
            raise ProviderError("DXWORKS_API_HOST가 설정되지 않았습니다", unavailable=True)
        if not _is_secure_host(self.host):
            # 회사 API 키와 사용자 VC-JWT를 평문으로 보내지 않는다. 메시지에 호스트 값은 넣지 않는다.
            raise ProviderError("DXWORKS_API_HOST는 https:// 주소여야 합니다", unavailable=True)
        if not is_valid_host_setting(self.host):
            # 서버 시작 때(core.config) 이미 거른 값이다. 어댑터를 직접 만든 경우에도 같은 규칙으로 막는다.
            raise ProviderError("DXWORKS_API_HOST에 사용자 정보·쿼리·프래그먼트·공백·범위 밖의 포트를 넣을 수 없습니다",
                                unavailable=True)
        if not (math.isfinite(self.timeout) and self.timeout > 0):
            # 0·음수·inf·nan이면 시간 제한이 없어지거나 모든 호출이 실패한다 (core.config가 1~120초만 받는다).
            raise ProviderError("DXWORKS_TIMEOUT_SECONDS가 올바르지 않습니다", unavailable=True)
        # 압축 응답을 요청하지 않는다 (_exchange 참고).
        headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
        if auth:
            if not self.api_key:
                raise ProviderError("DXWORKS_API_KEY가 설정되지 않았습니다", unavailable=True)
            if not self.api_key.isascii():
                # 헤더에 넣을 수 없는 값이다 (httpx가 UnicodeEncodeError를 내 500이 되지 않게 설정 오류로 답한다).
                raise ProviderError("DXWORKS_API_KEY는 ASCII 문자만 쓸 수 있습니다", unavailable=True)
            headers["Authorization"] = f"Bearer {self.api_key}"
        # 교환(연결·전송·본문 수신. 연결 재시도까지 포함) 전체에 쓰는 시간 상한. httpx의 timeout은 읽기 한 번마다 따로
        # 재므로, 협력사가 본문을 조금씩 흘려보내면 끝없이 늘어난다.
        # 호출 한 번은 최악의 경우 동시 호출 자리 대기(timeout × 2 이하) + 교환(timeout × 2 이하)만큼 걸린다.
        deadline = self.timeout * 2
        slots = _in_flight_slots()
        if slots.locked():
            try:
                await asyncio.wait_for(slots.acquire(), timeout=deadline)
            except asyncio.TimeoutError:
                # 아직 아무것도 보내지 않았다. 동시 호출 상한이 계속 차 있으면 협력사가 느린 것이므로 장애처럼 답한다.
                raise ProviderError("DXWorks 호출 대기 시간 초과 (동시 호출 상한)") from None
        else:
            await slots.acquire()  # 자리가 있으면 기다리지 않고 바로 잡는다
        try:
            loop = asyncio.get_running_loop()
            ends_at = loop.time() + deadline  # 두 번의 연결 시도가 이 상한 하나를 함께 쓴다
            for attempt in range(1 + _CONNECT_RETRIES):
                try:
                    status, reason, content = await asyncio.wait_for(self._exchange(path, payload, headers),
                                                                     timeout=ends_at - loop.time())
                    break
                except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    # 연결 자체가 안 된 경우만 재시도한다. 요청이 서버에 닿지 않았으므로 발급 요청도 중복되지 않는다.
                    # 남은 시간에 시도 한 번(timeout)이 온전히 들어갈 때만 다시 한다: 느리게 실패한 연결(연결 시간 초과)은
                    # 이미 timeout만큼 썼으므로 다시 하지 않고, 바로 실패한 연결(거절·DNS)은 지금처럼 한 번 다시 한다.
                    if attempt < _CONNECT_RETRIES and ends_at - loop.time() >= _RETRY_DELAY_SECONDS + self.timeout:
                        await asyncio.sleep(_RETRY_DELAY_SECONDS)
                        continue
                    raise ProviderError(f"DXWorks 연결 실패: {type(exc).__name__}") from exc
                except httpx.HTTPError as exc:
                    # 응답 대기 중 시간 초과 등은 서버가 이미 처리했을 수 있어 재시도하지 않는다 (결과를 모르는 실패).
                    # 요청을 보내기 전에 난 오류(잘못된 헤더 값 등)만 결과를 아는 실패다.
                    sent = not isinstance(exc, (httpx.LocalProtocolError, httpx.UnsupportedProtocol, httpx.PoolTimeout))
                    raise ProviderError(f"DXWorks 연결 실패: {type(exc).__name__}", ambiguous=sent) from exc
                except asyncio.TimeoutError:
                    # 전체 제한 시간 초과도 같은 이유로 재시도하지 않는다.
                    raise ProviderError("DXWorks 연결 실패: 전체 제한 시간 초과", ambiguous=True) from None
                except (OSError, OverflowError, ValueError, ExceptionGroup, httpx.InvalidURL) as exc:
                    # httpx.HTTPError가 아닌 실패: 주소를 만들거나 연결을 시작하는 단계에서 난 오류다 (범위 밖의 포트는 anyio가
                    # OverflowError를 ExceptionGroup으로 감싸 던진다). 아무것도 보내지 않았으므로 결과를 아는 실패이고,
                    # 500이 아니라 다른 연결 실패처럼 502로 답한다.
                    raise ProviderError(f"DXWorks 연결 실패: {type(exc).__name__}") from exc
        finally:
            slots.release()

        try:
            # NaN·Infinity·float 범위를 넘는 숫자는 JSON 응답으로 다시 내보낼 수 없으므로(500) 해석 단계에서 거절한다.
            body = json.loads(content, parse_float=_finite_float, parse_constant=_reject_constant)
        except (ValueError, RecursionError):
            # JSON이 아니거나 너무 깊게 중첩되어 해석할 수 없는 본문은 JSON이 아닌 응답으로 본다 (RecursionError로 500이 나지 않게).
            body = None

        # 2xx만 검증·발급 결과로 읽는다. 리다이렉트(3xx)는 따라가지 않고, 본문이 JSON이어도 결과로 쓰지 않고 오류로 끝낸다.
        if not (200 <= status < 300):
            error = _dict(_dict(body).get("error"))
            code = self._redact(error.get("code") or status, payload)
            message = self._redact(error.get("message") or reason, payload)
            raise ProviderError(f"DXWorks 오류 ({code}): {message}", upstream_status=status)
        if body is None:
            raise ProviderError("DXWorks가 JSON이 아닌 응답을 반환했습니다", upstream_status=status, ambiguous=True)
        try:
            return _unwrap(body), status
        except ProviderError as exc:
            # 응답은 받았으므로 상태 코드를 붙인다 (upstream_status None은 "응답 없음"에만 쓴다: reverify.is_verifier_outage).
            raise ProviderError(exc.message, upstream_status=status, ambiguous=True) from None

    async def verify_badge(self, vc_jwt: str) -> ProviderBadgeResult:
        data, status = await self._post(_PATH_VERIFY_BADGE, {"vcJwt": vc_jwt}, auth=False)
        valid = data.get("valid")
        if not isinstance(valid, bool):
            # 판정(valid)이 참·거짓이 아닌 2xx는 판정이 아니다(점검 안내·오류 봉투 등): JSON이 아닌 2xx처럼 형식 오류로 끝낸다.
            # 거절(False)로 읽으면 재검증이 저장된 verified를 invalid로 바꿔 써 버린다.
            raise ProviderError("DXWorks 응답 형식이 올바르지 않습니다", upstream_status=status)
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
            valid=valid,
            signature_ok=signature_ok,
            issuer_trusted=issuer_trusted,
            credential_status=summary.get("status") or credential.get("credentialStatus"),
            recipient_did=recipient.get("subjectId") or badge.get("subjectId"),
            credential_id=credential.get("id"),
            issuer_name=issuer.get("issuerName") or badge.get("issuerName"),
            achievement_name=badge.get("name"),
            issued_at=badge.get("issuedAt"),
        )

    async def verify_presentation(self, vp_token: str) -> PresentationResult:
        data, _ = await self._post(_PATH_VERIFY_PRESENTATION, {"vpToken": vp_token}, auth=True)
        holder_did = data.get("holderDid")
        return PresentationResult(
            verified=data.get("verified") is True,
            # 문자열이 아니거나 비어 있으면(공백뿐인 값 포함) 홀더를 모르는 것으로 둔다 (API가 403으로 거절한다).
            holder_did=holder_did if isinstance(holder_did, str) and holder_did.strip() else None,
            claims=_dict(data.get("claims")),
        )

    async def issue_badge(self, template_id: str, holder_did: str) -> IssuedBadge:
        data, status = await self._post(_PATH_ISSUE_BADGE, {"templateId": template_id, "holderDid": holder_did},
                                        auth=True)
        vc_jwt = data.get("jwt")
        if not isinstance(vc_jwt, str) or not vc_jwt:
            # 2xx로 답했으므로 협력사는 발급했을 수 있다: 결과를 모르는 실패로 표시한다 (모의 발급이 한도 기록을 남긴다).
            raise ProviderError("DXWorks 발급 응답에 jwt가 없습니다", upstream_status=status, ambiguous=True)
        return IssuedBadge(vc_jwt=vc_jwt)

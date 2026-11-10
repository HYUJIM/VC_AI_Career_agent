"""자격증명 검증 결과 판정 (순수 로직, I/O 없음).

외부 검증기(DXWorks 등)의 응답은 어댑터가 ProviderBadgeResult로 바꿔 넘긴다.
이 모듈은 그 결과와 원본 VC-JWT 페이로드를 합쳐 DB 상태값과 점검 항목(verification_checks)을 만든다.
"""
import base64
import hashlib
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.text import has_unstorable_text

# DB(timestamptz)에 저장했다가 psycopg로 다시 읽을 수 있는 날짜 범위. 세션 시간대가 UTC가 아니어도 연도 1~9999를
# 넘지 않도록 양 끝에 하루씩 여유를 둔다. 범위 밖의 날짜는 가장 가까운 끝값으로 본다.
_EARLIEST = datetime(1, 1, 2, tzinfo=timezone.utc)
_LATEST = datetime(9999, 12, 30, tzinfo=timezone.utc)
_MAX_TEXT = 255  # credentials.achievement_name·issuer_name·source_external_id VARCHAR(255)
# 페이로드의 dict·list 중첩 단계 상한 (최상위 객체 = 1단계). 실제 OB3/VC 2.0 페이로드는 이보다 훨씬 얕다.
# json.loads가 받아들이는 한계(약 950단계) 바로 아래 깊이는 저장은 되지만, 응답 JSON을 만들 때 스택이 조금 더 깊어
# 재귀 한도에 걸린다 (그 사용자의 GET /careers가 계속 500). 그래서 호출 스택 깊이와 무관한 낮은 고정 상한을 둔다.
MAX_JSON_DEPTH = 64

# DB check_name_type ENUM 값
CHECK_SCHEMA = "schema"
CHECK_DID_RESOLUTION = "did_resolution"
CHECK_SIGNATURE = "signature"
CHECK_ISSUER_TRUST = "issuer_trust"
CHECK_REVOCATION = "revocation"
CHECK_ANCHOR_MATCH = "anchor_match"
CHECK_EXPIRATION = "expiration"
CHECK_RECIPIENT = "recipient"


@dataclass(frozen=True)
class CheckResult:
    name: str
    result: str  # pass | fail | warn | skip
    detail: Optional[str] = None


@dataclass(frozen=True)
class ProviderBadgeResult:
    """검증기(벤더)와 무관한 OB3 배지 검증 결과."""

    valid: bool
    signature_ok: Optional[bool]
    issuer_trusted: Optional[bool]
    credential_status: Optional[str]  # VALID | REVOKED | SUSPENDED | ...
    recipient_did: Optional[str]
    credential_id: Optional[str]
    issuer_name: Optional[str]
    achievement_name: Optional[str]
    issued_at: Optional[str]
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PresentationResult:
    verified: bool
    holder_did: Optional[str]  # 비어 있지 않은 DID 문자열, 모르면 None (로그인 사용자 DID와 다르면 API가 403으로 거절)
    claims: dict
    raw: dict


@dataclass(frozen=True)
class IssuedBadge:
    vc_jwt: str
    credential_id: Optional[str]
    raw: dict


@dataclass(frozen=True)
class BadgeEvaluation:
    status: str  # DB verification_status
    checks: list[CheckResult]
    recipient_did: Optional[str]
    record_id: str  # credentials.record_id (UUID 문자열)
    external_id: str  # credentials.source_external_id
    achievement_name: str
    issuer_name: str
    issuer_trust_level: str
    issued_at: datetime
    expires_at: Optional[datetime]
    vc: dict  # 디코딩한 VC 본문 (credentials.raw_data)

    @property
    def verified(self) -> bool:
        return self.status == "verified"


def _text(value: Any) -> Optional[str]:
    """이름·DID처럼 문자열이어야 하는 값. 문자열이 아니거나 비었거나 저장할 수 없는 문자가 있으면 없는 값으로 본다."""
    return value if isinstance(value, str) and value and not has_unstorable_text(value) else None


def _reject_constant(name: str) -> None:
    raise ValueError(f"JSON에 {name}은 쓸 수 없음")


def _finite_float(text: str) -> float:
    value = float(text)
    if math.isinf(value):  # 1e400처럼 float 범위를 넘는 숫자
        raise ValueError("JSON 숫자가 범위를 벗어남")
    return value


def _too_deep(value: Any) -> bool:
    """dict·list 중첩이 MAX_JSON_DEPTH단계를 넘는지 (최상위 = 1단계).

    깊게 중첩된 JSON에서도 RecursionError가 나지 않도록 재귀 없이 명시적 스택으로 돈다 (has_unstorable_text와 같은 방식).
    """
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            if depth > MAX_JSON_DEPTH:
                return True
            stack.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
    return False


def decode_jwt_payload(token: str) -> Optional[dict]:
    """서명 검증 없이 JWT 페이로드만 디코딩한다. 서명 검증은 검증기(DXWorks)가 맡는다.

    해석할 수 없거나 DB·응답에 그대로 넣을 수 없는 페이로드(NaN·무한대·float 범위를 넘는 숫자,
    MAX_JSON_DEPTH단계보다 깊은 중첩, NUL·짝 없는 서로게이트)는 None으로 돌려 검증 실패로 처리한다.
    """
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"),
                             parse_float=_finite_float, parse_constant=_reject_constant)
    except (IndexError, ValueError, UnicodeDecodeError, RecursionError):
        return None
    if not isinstance(payload, dict) or has_unstorable_text(payload) or _too_deep(payload):
        return None
    return payload


def _parse_datetime(value: Any) -> Optional[datetime]:
    """ISO 8601 문자열 또는 Unix 초. 형식이 틀리면 None.

    범위를 벗어난 날짜는 _EARLIEST/_LATEST로 자른다: 예외로 500이 나지 않고, 아주 먼 과거의 만료일이
    '만료일 없음'으로 바뀌지 않으며, DB에 저장한 값을 다시 읽을 수 있다. 범위 안의 값은 그대로 둔다.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        try:
            parsed = datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return _EARLIEST if value < 0 else _LATEST
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        return None
    # 시간대가 있는 datetime끼리의 비교는 넘침 없이 UTC 기준으로 한다.
    return min(max(parsed, _EARLIEST), _LATEST)


def to_record_id(external_id: str) -> str:
    """외부 자격증명 ID를 DB UUID로 바꾼다. `urn:uuid:` UUID는 그대로, 그 외는 결정적 UUIDv5."""
    candidate = external_id[len("urn:uuid:"):] if external_id.startswith("urn:uuid:") else external_id
    try:
        return str(uuid.UUID(candidate))
    except ValueError:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, external_id))


def _issuer_name_from_vc(vc: dict) -> Optional[str]:
    issuer = vc.get("issuer")
    if isinstance(issuer, dict):
        return issuer.get("name")
    return None


def evaluate_badge(result: ProviderBadgeResult, token: str, now: Optional[datetime] = None) -> BadgeEvaluation:
    now = now or datetime.now(timezone.utc)
    payload = decode_jwt_payload(token)
    vc = payload.get("vc", payload) if payload else None
    if not isinstance(vc, dict):
        vc = None
    subject = vc.get("credentialSubject") if vc else None
    achievement = subject.get("achievement") if isinstance(subject, dict) else None
    achievement = achievement if isinstance(achievement, dict) else {}

    checks: list[CheckResult] = []

    schema_ok = vc is not None and isinstance(subject, dict)
    checks.append(CheckResult(CHECK_SCHEMA, "pass" if schema_ok else "fail",
                              None if schema_ok else "VC-JWT 페이로드에서 credentialSubject를 찾을 수 없음"))

    recipient = _text(result.recipient_did) or (_text(subject.get("id")) if isinstance(subject, dict) else None)
    checks.append(CheckResult(CHECK_RECIPIENT, "pass" if recipient else "fail",
                              recipient or "수신자 DID 없음"))

    if result.signature_ok is None:
        checks.append(CheckResult(CHECK_SIGNATURE, "skip", "검증기 응답에 발급자 서명 결과 없음"))
    else:
        checks.append(CheckResult(CHECK_SIGNATURE, "pass" if result.signature_ok else "fail"))

    if result.issuer_trusted is None:
        checks.append(CheckResult(CHECK_ISSUER_TRUST, "skip", "검증기 응답에 신뢰 레지스트리 결과 없음"))
    else:
        checks.append(CheckResult(CHECK_ISSUER_TRUST, "pass" if result.issuer_trusted else "fail"))

    # 검증기(협력사) 응답의 상태 값이 문자열이 아니어도 500이 아니라 VALID가 아닌 상태(실패)로 판정한다.
    credential_status = str(result.credential_status or "").upper() or None
    if credential_status is None:
        checks.append(CheckResult(CHECK_REVOCATION, "skip", "검증기 응답에 상태 목록 결과 없음"))
    else:
        checks.append(CheckResult(CHECK_REVOCATION, "pass" if credential_status == "VALID" else "fail",
                                  credential_status))

    expires_at = _parse_datetime(
        (vc or {}).get("validUntil") or (vc or {}).get("expirationDate") or (payload or {}).get("exp")
    )
    expired = expires_at is not None and expires_at <= now
    if expires_at is None:
        checks.append(CheckResult(CHECK_EXPIRATION, "skip", "만료일 없음"))
    else:
        checks.append(CheckResult(CHECK_EXPIRATION, "fail" if expired else "pass", expires_at.isoformat()))

    checks.append(CheckResult(CHECK_DID_RESOLUTION, "skip", "검증기(DXWorks) 내부에서 처리"))
    checks.append(CheckResult(CHECK_ANCHOR_MATCH, "skip", "원장 앵커링 대조 미지원"))

    if not schema_ok:
        status = "invalid"
    elif credential_status == "REVOKED":
        status = "revoked"
    elif expired:
        status = "expired"
    elif result.signature_ok is False or result.issuer_trusted is False or (
        credential_status is not None and credential_status != "VALID"
    ):
        status = "invalid"
    elif result.valid:
        status = "verified"
    else:
        status = "invalid"

    external_id = str(
        result.credential_id
        or (vc or {}).get("id")
        or (payload or {}).get("jti")
        or "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
    )
    if len(external_id) > _MAX_TEXT or has_unstorable_text(external_id):
        # DB에 넣을 수 없는 ID(255자 초과·NUL·짝 없는 서로게이트)는 같은 ID면 같은 값이 되도록 해시로 바꾼다.
        external_id = "sha256:" + hashlib.sha256(external_id.encode("utf-8", "surrogatepass")).hexdigest()
    issued_at = (
        _parse_datetime(result.issued_at)
        or _parse_datetime((vc or {}).get("validFrom") or (vc or {}).get("issuanceDate"))
        or _parse_datetime((payload or {}).get("iat"))
        or now
    )

    return BadgeEvaluation(
        status=status,
        checks=checks,
        recipient_did=recipient,
        record_id=to_record_id(external_id),
        external_id=external_id,
        # 문자열이 아니거나 저장할 수 없는 이름은 건너뛰고, DB 열(VARCHAR(255)) 길이로 자른다.
        achievement_name=(_text(result.achievement_name) or _text(achievement.get("name"))
                          or _text((vc or {}).get("name")) or "Unknown Badge")[:_MAX_TEXT],
        issuer_name=(_text(result.issuer_name) or _text(_issuer_name_from_vc(vc or {}))
                     or "Unknown Issuer")[:_MAX_TEXT],
        issuer_trust_level="accredited" if result.issuer_trusted else "unknown",
        issued_at=issued_at,
        expires_at=expires_at,
        vc=vc or {},
    )


def is_issuer_confirmed(result: ProviderBadgeResult, evaluation: BadgeEvaluation) -> bool:
    """검증기가 이 원본을 신뢰된 발급자가 서명한 것으로 확인했는지 (저장해도 되는지).

    확인되지 않은 토큰의 본문(자격증명 ID·수신자·배지 이름)은 누구나 만들 수 있으므로 저장하지 않는다.
    저장하면 남의 레코드를 덮어쓰거나 자격증명 ID를 선점(다른 사용자에게 409)하는 데 쓰인다.
    - verified: 검증기가 받아들인 원본.
    - revoked/expired: 서명과 발급자 신뢰가 모두 통과(True)로 와야 한다. evaluate_badge는 폐기·만료를 서명 결과보다
      먼저 판정하므로, 서명이 틀렸거나 검증기 응답에 서명 결과가 없는 위조 토큰도 revoked/expired가 될 수 있다.
    - 그 밖(invalid 등): 저장하지 않는다.
    """
    if evaluation.verified:
        return True
    return (evaluation.status in ("revoked", "expired")
            and result.signature_ok is True and result.issuer_trusted is True)

"""자격증명 검증 결과 판정 (순수 로직, I/O 없음).

외부 검증기(DXWorks 등)의 응답은 어댑터가 ProviderBadgeResult로 바꿔 넘긴다.
이 모듈은 그 결과와 원본 VC-JWT 페이로드를 합쳐 DB 상태값과 점검 항목(verification_checks)을 만든다.
"""
import base64
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

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
    holder_did: Optional[str]
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


def decode_jwt_payload(token: str) -> Optional[dict]:
    """서명 검증 없이 JWT 페이로드만 디코딩한다. 서명 검증은 검증기(DXWorks)가 맡는다."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode("utf-8"))
    except (IndexError, ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


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

    recipient = result.recipient_did or (subject.get("id") if isinstance(subject, dict) else None)
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

    credential_status = (result.credential_status or "").upper() or None
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

    external_id = (
        result.credential_id
        or (vc or {}).get("id")
        or (payload or {}).get("jti")
        or "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
    )
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
        record_id=to_record_id(str(external_id)),
        external_id=str(external_id),
        achievement_name=result.achievement_name or achievement.get("name") or (vc or {}).get("name") or "Unknown Badge",
        issuer_name=result.issuer_name or _issuer_name_from_vc(vc or {}) or "Unknown Issuer",
        issuer_trust_level="accredited" if result.issuer_trusted else "unknown",
        issued_at=issued_at,
        expires_at=expires_at,
        vc=vc or {},
    )

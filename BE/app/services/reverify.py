"""실행 시점 재검증 (논문 RAG-VC).

DB에 저장된 상태값·본문 사본을 믿지 않고, 요청 시점에 서명 원본(credentials.raw_token)을 검증기에 다시 보내
그 결과와 **서명 원본 안의 본문**으로 근거(Evidence)를 만든다.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from starlette.concurrency import run_in_threadpool

from app.domain.credential import BadgeEvaluation, CheckResult, evaluate_badge
from app.integrations.ports import CredentialVerifier, ProviderError
from app.services.database import db_service

MAX_CONCURRENCY = 5


@dataclass(frozen=True)
class ReverifyOutcome:
    record_id: str
    status: str
    verified: bool
    evidence: dict  # openapi Evidence
    reason: Optional[str]  # 통과하지 못한 사유 (통과하면 None)
    vc_jwt: Optional[str]


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _achievement(vc: dict) -> dict:
    subject = vc.get("credentialSubject") if isinstance(vc, dict) else None
    achievement = subject.get("achievement") if isinstance(subject, dict) else None
    return achievement if isinstance(achievement, dict) else {}


def _criteria_text(achievement: dict) -> Optional[str]:
    criteria = achievement.get("criteria")
    if isinstance(criteria, dict):
        return criteria.get("narrative") or criteria.get("id")
    return criteria if isinstance(criteria, str) else None


def build_evidence(*, record_id: str, status: str, vc: dict, issuer_name: str, achievement_name: str,
                   issued_at: Any, expires_at: Any, verified_at: Optional[datetime],
                   checks: list[CheckResult]) -> dict:
    achievement = _achievement(vc)
    return {
        "record_id": record_id,
        "status": status,
        "verified": status == "verified",
        "achievement_name": achievement.get("name") or achievement_name,
        "achievement_description": achievement.get("description"),
        "criteria": _criteria_text(achievement),
        "issuer_name": issuer_name,
        "issued_at": _iso(issued_at),
        "expires_at": _iso(expires_at),
        "verified_at": _iso(verified_at),
        "checks": [{"name": c.name, "result": c.result, "detail": c.detail} for c in checks],
    }


def _not_verifiable(row: dict, reason: str, detail: str, *, persist: bool = False) -> ReverifyOutcome:
    # 서명 원본으로 검증할 수 없는 경우: persist가 켜져 있고 서명 원본 자체가 없으면 DB 상태도 unverifiable('확인 필요')로 갱신한다.
    checks = [CheckResult("signature", "skip", detail)]
    vc = row["raw_data"] or {}
    if persist and reason == "no_signed_original":
        db_service.update_reverification(str(row["record_id"]), "unverifiable", checks, vc, None)
    evidence = build_evidence(
        record_id=str(row["record_id"]), status="unverifiable", vc=vc,
        issuer_name=row["issuer_name"], achievement_name=row["achievement_name"],
        issued_at=row["issued_at"], expires_at=row["expires_at"], verified_at=None,
        checks=checks,
    )
    return ReverifyOutcome(str(row["record_id"]), "unverifiable", False, evidence, reason, row.get("raw_token"))


def _guard(row: dict, evaluation: BadgeEvaluation) -> Optional[tuple[str, CheckResult]]:
    """서명 원본이 이 레코드·이 사용자의 것이 맞는지. DB의 원본 토큰을 바꿔치기한 경우를 잡는다."""
    if evaluation.recipient_did != row["subject_did"]:
        return "recipient_mismatch", CheckResult("recipient", "fail", "서명 원본의 수신자가 소유자 DID와 다름")
    if evaluation.record_id != str(row["record_id"]):
        return "record_mismatch", CheckResult("schema", "fail", "서명 원본의 자격증명 ID가 레코드와 다름")
    return None


async def _reverify_one(row: dict, verifier: CredentialVerifier, *, persist: bool,
                        raise_provider_errors: bool) -> ReverifyOutcome:
    token = row.get("raw_token")
    if not token:
        return await run_in_threadpool(_not_verifiable, row, "no_signed_original", "서명 원본 없음 (재검증 불가)", persist=persist)
    try:
        result = await verifier.verify_badge(token)
    except ProviderError:
        if raise_provider_errors:
            raise
        return _not_verifiable(row, "verifier_error", "검증기 호출 실패")

    now = datetime.now(timezone.utc)
    evaluation = evaluate_badge(result, token, now)
    checks = list(evaluation.checks)
    status = evaluation.status
    reason = None if status == "verified" else status

    mismatch = _guard(row, evaluation)
    if mismatch:
        reason, check = mismatch
        status = "invalid"
        checks.append(check)

    if persist:
        await run_in_threadpool(db_service.update_reverification, str(row["record_id"]), status, checks,
                                evaluation.vc, now)

    evidence = build_evidence(
        record_id=str(row["record_id"]), status=status, vc=evaluation.vc, issuer_name=evaluation.issuer_name,
        achievement_name=evaluation.achievement_name, issued_at=evaluation.issued_at,
        expires_at=evaluation.expires_at, verified_at=now, checks=checks,
    )
    return ReverifyOutcome(str(row["record_id"]), status, status == "verified", evidence, reason, token)


async def reverify_rows(rows: list[dict], verifier: CredentialVerifier, *, persist: bool = True,
                        raise_provider_errors: bool = False) -> list[ReverifyOutcome]:
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def run(row: dict) -> ReverifyOutcome:
        async with semaphore:
            return await _reverify_one(row, verifier, persist=persist, raise_provider_errors=raise_provider_errors)

    return list(await asyncio.gather(*(run(row) for row in rows)))

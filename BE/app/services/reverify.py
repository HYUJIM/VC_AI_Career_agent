"""실행 시점 재검증 (논문 RAG-VC).

DB에 저장된 상태값·본문 사본을 믿지 않고, 요청 시점에 서명 원본(credentials.raw_token)을 검증기에 다시 보내
그 결과와 **서명 원본 안의 본문**으로 근거(Evidence)를 만든다.
로그인 없는 기업용 조회(공유 링크)만 예외로, public_view_gate로 최근(60초 안) 응답을 다시 쓰고 DB에 저장하지 않는다.
"""
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from starlette.concurrency import run_in_threadpool

from app.domain.credential import BadgeEvaluation, CheckResult, ProviderBadgeResult, evaluate_badge
from app.integrations.ports import CredentialVerifier, ProviderError
from app.services.database import db_service
from app.services.public_view_gate import PublicViewGate

MAX_CONCURRENCY = 5
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReverifyOutcome:
    record_id: str
    status: str
    verified: bool
    evidence: dict  # openapi Evidence
    reason: Optional[str]  # 통과하지 못한 사유 (통과하면 None)
    vc_jwt: Optional[str]
    # 서명 원본을 해석한 결과 (서명 원본이 없거나 검증기 호출에 실패하면 None). 직무 비교 스냅샷에 쓴다.
    evaluation: Optional[BadgeEvaluation] = None
    # 검증기 호출 실패(reason == "verifier_error")일 때만 그 오류. 저장하는 API가 장애인지 가려낼 때 쓴다.
    error: Optional[ProviderError] = None


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


def _not_verifiable(row: dict, reason: str, detail: str, error: Optional[ProviderError] = None,
                    *, persist: bool = False) -> ReverifyOutcome:
    # 서명 원본으로 검증할 수 없는 경우: DB 사본으로 표시만 하고 근거로는 쓰지 않는다 (strict에서 제외).
    # persist가 켜져 있고 서명 원본 자체가 없으면 DB 상태도 unverifiable('확인 필요')로 갱신한다.
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
    return ReverifyOutcome(str(row["record_id"]), "unverifiable", False, evidence, reason, row.get("raw_token"),
                           error=error)


def _guard(row: dict, evaluation: BadgeEvaluation) -> Optional[tuple[str, CheckResult]]:
    """서명 원본이 이 레코드·이 사용자의 것이 맞는지. DB의 원본 토큰을 바꿔치기한 경우를 잡는다."""
    if evaluation.recipient_did != row["subject_did"]:
        return "recipient_mismatch", CheckResult("recipient", "fail", "서명 원본의 수신자가 소유자 DID와 다름")
    if evaluation.record_id != str(row["record_id"]):
        return "record_mismatch", CheckResult("schema", "fail", "서명 원본의 자격증명 ID가 레코드와 다름")
    return None


async def _reverify_one(row: dict, verifier: CredentialVerifier, *, persist: bool, raise_provider_errors: bool,
                        gate: Optional[PublicViewGate] = None,
                        cached: Optional[tuple[datetime, ProviderBadgeResult]] = None) -> ReverifyOutcome:
    token = row.get("raw_token")
    if not token:
        return await run_in_threadpool(_not_verifiable, row, "no_signed_original", "서명 원본 없음 (재검증 불가)",
                                       persist=persist)
    if cached is not None:
        # 기업용 조회: 조금 전에 검증기에 물은 응답을 다시 쓴다. verified_at은 그때 시각, 만료 판정은 지금 시각.
        verified_at, result = cached
        now = datetime.now(timezone.utc)
    else:
        try:
            result = await verifier.verify_badge(token)
        except ProviderError as exc:
            # 운영 로그로 장애를 알 수 있게 남긴다. 서명 원본(raw_token)·DID는 남기지 않는다.
            logger.warning("Credential re-verification failed: record=%s upstream=%s unavailable=%s: %s",
                           row["record_id"], exc.upstream_status, exc.unavailable, exc.message)
            if raise_provider_errors:
                raise
            return _not_verifiable(row, "verifier_error", "검증기 호출 실패", error=exc)
        now = verified_at = datetime.now(timezone.utc)
        if gate is not None:
            gate.store(token, verified_at, result)

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
                                evaluation.vc, verified_at)

    evidence = build_evidence(
        record_id=str(row["record_id"]), status=status, vc=evaluation.vc, issuer_name=evaluation.issuer_name,
        achievement_name=evaluation.achievement_name, issued_at=evaluation.issued_at,
        expires_at=evaluation.expires_at, verified_at=verified_at, checks=checks,
    )
    return ReverifyOutcome(str(row["record_id"]), status, status == "verified", evidence, reason, token,
                           evaluation)


async def reverify_rows(rows: list[dict], verifier: CredentialVerifier, *, persist: bool = True,
                        raise_provider_errors: bool = False,
                        gate: Optional[PublicViewGate] = None) -> list[ReverifyOutcome]:
    """gate는 기업용 조회(로그인 없음) 전용이다: 최근 응답을 다시 쓰고, 새로 부를 몫이 모자라면 검증기를 하나도
    부르지 않고 public_view_gate.BudgetExceeded를 던진다 (public_view_gate.py).
    gate가 없으면 지금처럼 항상 검증기에 새로 묻는다.
    """
    cached: dict[str, tuple[datetime, ProviderBadgeResult]] = {}
    if gate is not None:
        signed = [row for row in rows if row.get("raw_token")]
        for row in signed:
            hit = gate.cached(row["raw_token"])
            if hit is not None:
                cached[str(row["record_id"])] = hit
        # 호출하기 전에 필요한 몫을 한꺼번에 가져간다 (모자라면 여기서 끝: 검증기 호출 0번).
        gate.reserve(len(signed) - len(cached))
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def run(row: dict) -> ReverifyOutcome:
        async with semaphore:
            return await _reverify_one(row, verifier, persist=persist, raise_provider_errors=raise_provider_errors,
                                       gate=gate, cached=cached.get(str(row["record_id"])))

    return list(await asyncio.gather(*(run(row) for row in rows)))


def is_verifier_outage(exc: ProviderError) -> bool:
    """검증기 자체 장애인지: 설정 누락, 응답 상태 없는 실패(연결 실패·시간 초과 등), 5xx, 408, 429.

    그 밖의 응답(특정 서명 원본에 대한 4xx 거절 등)은 그 자격증명 하나의 문제로 보고 지금처럼 제외만 한다.
    """
    status = exc.upstream_status
    return exc.unavailable or status is None or status >= 500 or status in (408, 429)


def raise_on_verifier_outage(outcomes: list[ReverifyOutcome]) -> None:
    """결과를 저장하는 API(직무 비교·포트폴리오 생성)용. 검증기 장애가 하나라도 있으면 그 오류를 다시 던진다.

    모든 재검증이 끝난 뒤에 확인하므로 백그라운드에 남는 호출이 없다. main.py가 502/503으로 답한다.
    """
    for outcome in outcomes:
        if outcome.error is not None and is_verifier_outage(outcome.error):
            raise outcome.error

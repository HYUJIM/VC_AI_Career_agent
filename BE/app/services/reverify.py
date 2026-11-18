"""실행 시점 재검증 (논문 RAG-VC).

DB에 저장된 상태값·본문 사본을 믿지 않고, 요청 시점에 서명 원본(credentials.raw_token)을 검증기에 다시 보내
그 결과와 **서명 원본 안의 본문**으로 근거(Evidence)를 만든다.
로그인 없는 기업용 조회(공유 링크)는 public_view_gate로 최근(60초 안) 응답을 다시 쓰고 DB에 저장하지 않는다.
로그인 사용자도 같은 서명 원본을 REVERIFY_REUSE_SECONDS(기본 60초) 안에 다시 묻지 않고 그때 응답을 쓴다 (recent_results).
화면을 열 때의 지갑 재검증과 곧이은 직무 비교·포트폴리오 재검증이 협력사의 요청 속도 제한(429)에 걸리지 않게 하기 위해서다.
검증기를 부르는 양은 부르기 전에 묶는다: 로그인 사용자·서비스 키는 verifier_budget, 기업용 조회는 public_view_gate.
"""
import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.domain.credential import BadgeEvaluation, CheckResult, ProviderBadgeResult, evaluate_badge
from app.integrations.ports import CredentialVerifier, ProviderError
from app.services.database import db_service
from app.services.public_view_gate import PublicViewGate
from app.services.verifier_budget import log_verifier_calls, reserve_verifier_calls

MAX_CONCURRENCY = 5
RECENT_RESULTS_MAX = 5000  # 최근 응답을 기억하는 서명 원본 수 (넘으면 오래된 것부터 잊는다)
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


class RecentResults:
    """로그인 사용자 재검증의 최근 협력사 응답 (프로세스 메모리, 키는 서명 원본의 SHA-256).

    응답 자체(서명·발급기관·폐기 여부 판정)는 원본마다 같으므로 사용자와 무관하게 다시 쓸 수 있다. 수신자·레코드 대조와
    만료 판정은 쓸 때마다 지금 시각으로 다시 한다 (_reverify_one).
    """

    def __init__(self, max_entries: int = RECENT_RESULTS_MAX, clock=time.monotonic):
        self.max_entries = max_entries
        self._clock = clock
        self._items: "OrderedDict[bytes, tuple[float, datetime, ProviderBadgeResult]]" = OrderedDict()

    @staticmethod
    def _key(vc_jwt: str) -> bytes:
        return hashlib.sha256(vc_jwt.encode("utf-8", "surrogatepass")).digest()

    def get(self, vc_jwt: str, ttl_seconds: float) -> Optional[tuple[datetime, ProviderBadgeResult]]:
        if ttl_seconds <= 0:
            return None
        key = self._key(vc_jwt)
        item = self._items.get(key)
        if item is None:
            return None
        stored, verified_at, result = item
        if self._clock() - stored >= ttl_seconds:
            del self._items[key]
            return None
        return verified_at, result

    def put(self, vc_jwt: str, verified_at: datetime, result: ProviderBadgeResult) -> None:
        key = self._key(vc_jwt)
        self._items[key] = (self._clock(), verified_at, result)
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()


recent_results = RecentResults()


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


def _flight_wait_seconds() -> float:
    """다른 조회가 묻고 있는 원본의 응답을 기다리는 시간 상한: 어댑터의 호출 한 번 상한과 같다
    (동시 호출 자리 대기 timeout × 2 + 교환 timeout × 2, integrations/dxworks.py)."""
    return 4 * get_settings().dxworks_timeout_seconds


def _guard(row: dict, evaluation: BadgeEvaluation) -> Optional[tuple[str, CheckResult]]:
    """서명 원본이 이 레코드·이 사용자의 것이 맞는지. DB의 원본 토큰을 바꿔치기한 경우를 잡는다."""
    if evaluation.recipient_did != row["subject_did"]:
        return "recipient_mismatch", CheckResult("recipient", "fail", "서명 원본의 수신자가 소유자 DID와 다름")
    if evaluation.record_id != str(row["record_id"]):
        return "record_mismatch", CheckResult("schema", "fail", "서명 원본의 자격증명 ID가 레코드와 다름")
    return None


async def _reverify_one(row: dict, verifier: CredentialVerifier, *, persist: bool,
                        stats: dict, gate: Optional[PublicViewGate] = None,
                        step: Optional[tuple[str, Any]] = None) -> ReverifyOutcome:
    """step은 기업용 조회에서 gate.begin이 정한 이 서명 원본의 할 일이다 (cached·wait·call). 없으면 검증기에 새로 묻는다.

    stats는 이 요청이 검증기를 실제로 부른 수(calls)와 그중 실패한 수(failed)다 (감사 로그용).
    """
    token = row.get("raw_token")
    if not token:
        return await run_in_threadpool(_not_verifiable, row, "no_signed_original", "서명 원본 없음 (재검증 불가)",
                                       persist=persist)
    kind, value = step if step is not None else ("call", None)
    answer: Optional[tuple[datetime, ProviderBadgeResult]] = None
    if kind == "cached":
        answer = value
    elif kind == "wait":
        # 기업용 조회: 다른 조회가 같은 원본을 지금 검증기에 묻고 있다. 다시 부르지 않고 그 응답(또는 그 실패)을 함께 쓴다.
        # 기다리는 시간에는 상한을 둔다 (넘으면 검증기 오류로 끝난다: 먼저 묻던 쪽에 무슨 일이 있어도 끝없이 기다리지 않는다).
        waited = await gate.wait(value, _flight_wait_seconds())
        if isinstance(waited, ProviderError):
            return _not_verifiable(row, "verifier_error", "검증기 호출 실패", error=waited)
        answer = waited
    if answer is not None:
        # 기업용 조회: 조금 전에 검증기에 물은 응답을 다시 쓴다. verified_at은 그때 시각, 만료 판정은 지금 시각.
        verified_at, result = answer
        now = datetime.now(timezone.utc)
    else:
        stats["calls"] += 1
        try:
            result = await verifier.verify_badge(token)
        except ProviderError as exc:
            stats["failed"] += 1
            # 운영 로그로 장애를 알 수 있게 남긴다. 서명 원본(raw_token)·DID는 남기지 않는다.
            logger.warning("Credential re-verification failed: record=%s upstream=%s unavailable=%s: %s",
                           row["record_id"], exc.upstream_status, exc.unavailable, exc.message)
            if gate is not None:
                gate.settle(value, exc)  # 이 응답을 기다리던 다른 조회도 같은 실패로 끝난다
            return _not_verifiable(row, "verifier_error", "검증기 호출 실패", error=exc)
        now = verified_at = datetime.now(timezone.utc)
        if gate is not None:
            gate.store(token, verified_at, result)
            gate.settle(value, (verified_at, result))
        else:
            recent_results.put(token, verified_at, result)

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


async def reverify_rows(rows: list[dict], verifier: CredentialVerifier, *, caller: str, route: str,
                        persist: bool = True, gate: Optional[PublicViewGate] = None) -> list[ReverifyOutcome]:
    """caller(사용자 id·"service"·"anonymous")와 route(API 짧은 이름)는 호출 한도와 감사 로그에 쓴다.

    gate가 없으면(로그인 사용자·서비스 키) REVERIFY_REUSE_SECONDS 안에 물은 원본은 그 응답(recent_results)을 쓰고, 나머지만
    검증기에 새로 묻는다. 묻기 전에 새로 물을 원본 수만큼 호출 몫을 한꺼번에 가져가고,
    모자라면 검증기를 하나도 부르지 않고 verifier_budget.VerifierBudgetExceeded를 던진다 (main.py가 429로 답한다).
    gate는 기업용 조회(로그인 없음) 전용이다: 최근 응답을 다시 쓰고, 다른 조회가 묻고 있는 원본은 그 응답을 기다리며,
    새로 부를 몫이 모자라면 검증기를 하나도 부르지 않고 public_view_gate.BudgetExceeded를 던진다 (public_view_gate.py).
    검증기 장애(is_verifier_outage)가 한 번 확인되면 아직 시작하지 않은 행은 검증기를 부르지 않고 그 오류로 '검증 불가'가 된다
    (장애 중이거나 우리를 제한하는 협력사를 남은 자격증명 수만큼 더 부르지 않는다). 다만 이 요청에서 협력사가 이미 다른 원본에
    답했으면(최근 응답 재사용 포함) 429는 그 원본 하나에 대한 제한으로 보고 멈추지 않는다 (DXWorks는 같은 원본을 자주 물으면
    그 원본만 한동안 429로 거절한다).
    """
    signed = [row for row in rows if row.get("raw_token")]
    # 행과 같은 자리의 할 일 (게이트가 없거나 서명 원본이 없는 행은 None). record_id로 찾지 않고 자리로 짝짓는다:
    # 같은 레코드나 같은 서명 원본이 목록에 두 번 있어도 앞 행의 "call"이 뒤 행의 "wait"로 덮이지 않는다.
    steps: list[Optional[tuple[str, Any]]] = [None] * len(rows)
    stats = {"calls": 0, "failed": 0}
    outage: list[ProviderError] = []  # 처음 확인된 검증기 장애
    answered: list[bool] = []  # 이 요청에서 협력사가 답한(또는 최근 응답을 다시 쓴) 서명 원본
    if gate is None:
        ttl = get_settings().reverify_reuse_seconds
        for index, row in enumerate(rows):
            if row.get("raw_token"):
                recent = recent_results.get(row["raw_token"], ttl)
                if recent is not None:
                    steps[index] = ("cached", recent)
        misses = sum(1 for row, step in zip(rows, steps) if row.get("raw_token") and step is None)
        # 호출하기 전에 필요한 몫을 한꺼번에 가져간다 (모자라면 여기서 끝: 검증기 호출 0번).
        reserve_verifier_calls(caller, route, misses)
    else:
        planned = iter(gate.begin([row["raw_token"] for row in signed]))
        steps = [next(planned) if row.get("raw_token") else None for row in rows]
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def run(row: dict, step: Optional[tuple[str, Any]]) -> ReverifyOutcome:
        async with semaphore:
            if outage and row.get("raw_token") and (step is None or step[0] == "call"):
                if step is not None:
                    # 이 행이 묻기로 한 원본을 기다리는 조회(같은 요청의 뒤 행 포함)가 있으면 지금 같은 실패로 끝나게 알린다.
                    gate.settle(step[1], outage[0])
                return _not_verifiable(row, "verifier_error", "검증기 호출 실패", error=outage[0])
            outcome = await _reverify_one(row, verifier, persist=persist, stats=stats, gate=gate, step=step)
            if outcome.error is None and row.get("raw_token"):
                answered.append(True)
            elif outcome.error is not None and not outage and is_verifier_outage(outcome.error):
                if not (_rate_limited(outcome.error) and answered):
                    outage.append(outcome.error)
            return outcome

    try:
        return list(await asyncio.gather(*(run(row, step) for row, step in zip(rows, steps))))
    finally:
        # 부르지 않고 끝난 호출(예외·취소)을 기다리는 조회가 남지 않게 실패로 알린다 (이미 알린 것은 그대로).
        abandoned = outage[0] if outage else ProviderError("검증기 호출이 끝나지 않았습니다")
        for step in steps:
            if step is not None and step[0] == "call":
                gate.settle(step[1], abandoned)
        log_verifier_calls(caller, route, stats["calls"], stats["failed"])


def _rate_limited(exc: ProviderError) -> bool:
    return exc.upstream_status == 429


def is_verifier_outage(exc: ProviderError) -> bool:
    """검증기 자체 장애인지: 설정 누락, 응답 상태 없는 실패(연결 실패·시간 초과 등), 5xx, 408, 429, 401, 403, 모든 3xx.

    401·403·3xx는 서명 원본 하나에 대한 답이 아니다 (API 키·IP 차단, 차단 페이지, 옮겨진 주소 등 우리 호출 전체가 거절된 상태).
    그 밖의 응답(특정 서명 원본에 대한 400·404·422 거절, 형식이 틀린 2xx 등)은 그 자격증명 하나의 문제로 보고 제외만 한다.
    """
    status = exc.upstream_status
    return (exc.unavailable or status is None or status >= 500 or status in (401, 403, 408, 429)
            or 300 <= status < 400)


def raise_on_verifier_outage(outcomes: list[ReverifyOutcome]) -> None:
    """결과를 저장하는 API(직무 비교·포트폴리오 생성)용. 검증기 장애가 하나라도 있으면 그 오류를 다시 던진다.

    서명 원본이 둘 이상인데 하나도 빠짐없이 검증기 오류로 끝났으면, 상태 코드가 자격증명 하나의 거절(400·404 등)처럼 보여도
    장애로 본다 (협력사가 우리 호출을 통째로 거절하는 상태를 '검증된 자격증명 없음'으로 저장하지 않는다).
    모든 재검증이 끝난 뒤에 확인하므로 백그라운드에 남는 호출이 없다. main.py가 502/503으로 답한다.
    429(요청 속도 제한)는 협력사가 같은 요청의 다른 원본에 답했으면 장애로 보지 않는다: 그 자격증명만 '검증 불가'로 빼고
    나머지로 저장한다 (자주 물은 원본 하나가 제한돼 포트폴리오·직무 비교 전체가 실패하지 않게).
    """
    partner_answered = any(outcome.error is None and outcome.vc_jwt for outcome in outcomes)
    for outcome in outcomes:
        if outcome.error is not None and is_verifier_outage(outcome.error):
            if _rate_limited(outcome.error) and partner_answered:
                continue
            raise outcome.error
    signed = [outcome for outcome in outcomes if outcome.vc_jwt]
    if len(signed) >= 2 and all(outcome.error is not None for outcome in signed):
        raise signed[0].error

"""직무 비교·커리큘럼 흐름 (BE가 증거를 책임지고, 계산은 AI 서비스가 한다).

1. 직무 비교: 사용자의 모든 자격증명을 지금 다시 검증(reverify) → 통과한 것만 스냅샷으로 묶음 →
   검토된 공고 버전과 함께 AI에 전달 → AI 결과가 BE가 보낸 근거만 인용하는지 확인 → 저장.
2. 커리큘럼(동기): 저장된 분석(유효기간 내) + 학습자료 → AI 계획 컨텍스트 → AI 생성 →
   결과가 컨텍스트 안의 단계·자료만 쓰는지 확인 → 저장. LLM 모드는 허용된 계정만 (CURRICULUM_LLM_USER_IDS, "*"면 모든 계정).
AI는 VC 서명 원본을 받지 않는다. 검증 판단은 BE의 DXWorks 어댑터와 domain.credential이 맡는다.

사용자별 한도 (넘으면 429 + Retry-After, 검증기·AI·LLM을 부르지 않는다. 감사 로그 vc-be.audit 한 줄):
- 직무 비교·커리큘럼 저장 수: 24시간에 각각 career_store.MAX_ANALYSES_PER_DAY·MAX_CURRICULA_PER_DAY건 (같은 요청 식별자로
  다시 받는 커리큘럼은 세지 않는다).
- LLM 모드 커리큘럼: 24시간에 CURRICULUM_LLM_DAILY_LIMIT건 (기본 20). 저장된 LLM 계획(DB, 재시작·태스크와 무관)과
  외부 LLM을 불렀을 수 있는 시도(verifier_budget.llm_curriculum_attempts, 실패 포함)를 함께 본다.
  모든 계정을 합쳐서도 24시간에 CURRICULUM_LLM_GLOBAL_DAILY_LIMIT건 (기본 200, 같은 방식으로 DB와 시도 창을 본다).
- 묶음 요청(공고·학습자료 묶음을 AI로 보내는 단계): 동시 수와 분·시간 몫 (ai_service.corpus_slot의 caller).
"""
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from app.ai_contract.api import GenerationInput
from app.ai_contract.curriculum import PLANNER_VERSION
from app.ai_contract.verification import (
    CredentialOutcome,
    NormalizedCredential,
    VerificationCheck,
    VerificationSnapshot,
)
from app.core.config import get_settings
from app.domain.credential import BadgeEvaluation, _parse_datetime
from app.integrations.ai_service import AIRejected, AIServiceClient, AIUnavailable, contract_violation
from app.integrations.ai_service import _settings as ensure_ai_configured
from app.integrations.ports import CredentialVerifier
from app.services.career_store import MAX_ANALYSES_PER_DAY, MAX_CURRICULA_PER_DAY, career_store, curriculum_view
from app.services.database import db_service
from app.services.reverify import ReverifyOutcome, raise_on_verifier_outage, reverify_rows
from app.services.verifier_budget import LLM_GLOBAL_KEY, llm_curriculum_attempts, llm_curriculum_global_attempts

SNAPSHOT_TTL = timedelta(minutes=5)  # 분석에 쓰는 재검증 결과의 유효 시간
OUTCOME_STATUSES = {"verified", "invalid", "revoked", "expired", "unverifiable"}
# AI가 다시 시도할 수 있다고 답한 생성 실패(LLM_BUSY·LLM_PROVIDER_UNAVAILABLE·Gemini 429/5xx 등)의 503에 붙이는 Retry-After
GENERATION_RETRY_AFTER_SECONDS = 5
# AI가 모델을 부르기 전에 끝난 생성 실패 코드: 이 요청의 LLM 시도 자리를 돌려준다 (그 밖의 실패는 모델을 불렀을 수 있어 센다).
LLM_NOT_SENT_CODES = frozenset({"LLM_BUSY", "LLM_CONTEXT_TOO_LARGE", "GENERATOR_CONFIGURATION_REQUIRED",
                                "GENERATOR_CONFIGURATION_CHANGED"})
# FE가 이 문구로 안내를 고른다 (FE/src/lib/apiClient.ts). 바꾸면 FE도 함께 바꾼다.
ANALYSIS_DAILY_LIMIT_REACHED = "Daily career analysis limit reached. Try again later."
CURRICULUM_DAILY_LIMIT_REACHED = "Daily curriculum limit reached. Try again later."
LLM_DAILY_LIMIT_REACHED = "Daily LLM curriculum limit reached. Use the template mode or try again later."
LLM_SERVICE_LIMIT_REACHED = "Daily LLM curriculum service limit reached. Use the template mode or try again later."
LLM_IN_FLIGHT_REACHED = "An LLM curriculum is already being generated for this account. Try again later."
# 계정 하나가 동시에 만들 수 있는 LLM 모드 계획 수 (이 프로세스 안). AI의 동시 LLM 생성 자리(CURRICULUM_LLM_MAX_CONCURRENCY,
# 기본 2)를 한 계정이 모두 쥐면 허용된 다른 계정이 10초 기다린 뒤 503 LLM_BUSY가 되므로 한 건으로 묶는다.
LLM_IN_FLIGHT_PER_USER = 1
LLM_IN_FLIGHT_RETRY_AFTER_SECONDS = 5
_llm_in_flight: dict[str, int] = {}  # 사용자 ID → 진행 중인 LLM 모드 계획 수
_llm_in_flight_lock = threading.Lock()  # 확인과 증가를 한 번에 한다 (테스트 클라이언트는 요청마다 다른 스레드의 루프를 쓴다)

audit = logging.getLogger("vc-be.audit")


def _too_many(user_id: str, route: str, detail: str, retry_after: int) -> HTTPException:
    audit.info("event=daily_limit_denied user=%s route=%s retry_after=%d", user_id, route, retry_after)
    return HTTPException(status_code=429, detail=detail, headers={"Retry-After": str(retry_after)})


async def check_daily_limit(kind: str, user_id: str, limit: int, route: str, detail: str) -> None:
    """최근 24시간 저장 수(career_store.daily_limit_retry_after)가 limit 이상이면 429 + Retry-After."""
    retry_after = await run_in_threadpool(career_store.daily_limit_retry_after, kind, user_id, limit)
    if retry_after is not None:
        raise _too_many(user_id, route, detail, retry_after)


async def reserve_llm_generation(user_id: str) -> None:
    """LLM 모드 학습 계획 한 건의 자리를 잡는다 (CURRICULUM_LLM_DAILY_LIMIT, 24시간). 모자라면 429 + Retry-After.

    저장된 LLM 계획 수(DB)가 한도면 거절하고, 아니면 프로세스 메모리의 시도 창(실패한 시도 포함)에 자리를 잡는다.
    잡은 자리는 모델을 부르지 않고 끝난 요청만 llm_curriculum_attempts.release로 돌려준다.
    그보다 먼저 이 계정의 진행 중인 LLM 계획 수(LLM_IN_FLIGHT_PER_USER)를 본다: 넘으면 429 + Retry-After이고 하루 몫은 쓰지 않는다.
    통과하면 진행 중으로 세므로, 부른 쪽이 끝날 때 반드시 leave_llm_generation()을 부른다.
    """
    # 아래 await 동안 같은 계정의 다른 요청이 이 확인을 함께 통과하지 못하게 확인과 함께 센다.
    with _llm_in_flight_lock:
        busy = _llm_in_flight.get(user_id, 0) >= LLM_IN_FLIGHT_PER_USER
        if not busy:
            _llm_in_flight[user_id] = _llm_in_flight.get(user_id, 0) + 1
    if busy:
        audit.info("event=llm_in_flight_denied user=%s", user_id)
        raise HTTPException(status_code=429, detail=LLM_IN_FLIGHT_REACHED,
                            headers={"Retry-After": str(LLM_IN_FLIGHT_RETRY_AFTER_SECONDS)})
    try:
        settings = get_settings()
        limit = settings.curriculum_llm_daily_limit
        await check_daily_limit("llm_curricula", user_id, limit, "curriculum_llm", LLM_DAILY_LIMIT_REACHED)
        if not llm_curriculum_attempts.reserve(user_id, limit):
            raise _too_many(user_id, "curriculum_llm", LLM_DAILY_LIMIT_REACHED,
                            llm_curriculum_attempts.retry_after(user_id))
        try:
            await _reserve_global_llm_generation(user_id, settings.curriculum_llm_global_daily_limit)
        except BaseException:
            llm_curriculum_attempts.release(user_id)  # 서비스 전체 한도로 거절: 이 계정의 하루 몫은 쓰지 않는다
            raise
    except BaseException:
        leave_llm_generation(user_id)
        raise


async def _reserve_global_llm_generation(user_id: str, limit: int) -> None:
    """모든 계정을 합친 LLM 학습 계획 자리 하나 (CURRICULUM_LLM_GLOBAL_DAILY_LIMIT, 24시간). 모자라면 429 + Retry-After.

    계정을 여러 개 만들어 사용자별 한도를 우회해도 외부 LLM(Gemini) 호출 수가 이 값을 넘지 못한다. 사용자별 한도와 같이
    저장된 LLM 계획 수(DB)와 프로세스 메모리의 시도 창(llm_curriculum_global_attempts)을 함께 본다.
    """
    retry_after = await run_in_threadpool(career_store.global_llm_retry_after, limit)
    if retry_after is None and not llm_curriculum_global_attempts.reserve(LLM_GLOBAL_KEY, limit):
        retry_after = llm_curriculum_global_attempts.retry_after(LLM_GLOBAL_KEY)
    if retry_after is not None:
        audit.info("event=llm_service_limit_denied user=%s retry_after=%d", user_id, retry_after)
        raise HTTPException(status_code=429, detail=LLM_SERVICE_LIMIT_REACHED,
                            headers={"Retry-After": str(retry_after)})


def leave_llm_generation(user_id: str) -> None:
    """reserve_llm_generation()이 센 진행 중 LLM 계획 한 건을 뺀다."""
    with _llm_in_flight_lock:
        remaining = _llm_in_flight.get(user_id, 0) - 1
        if remaining > 0:
            _llm_in_flight[user_id] = remaining
        else:
            _llm_in_flight.pop(user_id, None)


def clear_llm_in_flight() -> None:
    """진행 중 LLM 계획 수를 비운다 (테스트용: conftest가 테스트마다 부른다)."""
    with _llm_in_flight_lock:
        _llm_in_flight.clear()


def require_researcher(user_id: str) -> None:
    if user_id not in get_settings().researcher_user_ids:
        raise HTTPException(status_code=403, detail="Researcher permission required")


def _checks(outcome: ReverifyOutcome) -> list[VerificationCheck]:
    return [
        VerificationCheck(name=c["name"], result=c["result"], code=c["detail"] or f"{c['name']}:{c['result']}")
        for c in outcome.evidence.get("checks", [])
    ]


def _normalized(evaluation: BadgeEvaluation) -> NormalizedCredential:
    vc = evaluation.vc
    issuer = vc.get("issuer")
    issuer_id = issuer.get("id") if isinstance(issuer, dict) else issuer if isinstance(issuer, str) else None
    subject = vc.get("credentialSubject") if isinstance(vc.get("credentialSubject"), dict) else {}
    achievement = subject.get("achievement") if isinstance(subject.get("achievement"), dict) else {}
    valid_from = _parse_datetime(vc.get("validFrom")) or evaluation.issued_at
    return NormalizedCredential(
        external_id=evaluation.external_id,
        issuer_id=issuer_id or evaluation.issuer_name,
        issuer_name=evaluation.issuer_name,
        subject_did=evaluation.recipient_did,
        achievement_name=evaluation.achievement_name,
        achievement=achievement,
        issued_at=evaluation.issued_at,
        valid_from=valid_from,
        expires_at=evaluation.expires_at,
    )


async def build_snapshot(user: dict, verifier: CredentialVerifier) -> VerificationSnapshot:
    """DB 상태값을 믿지 않고 지금 서명 원본을 다시 검증한 결과로 AI 입력 스냅샷을 만든다."""
    started = datetime.now(timezone.utc)
    rows = await run_in_threadpool(db_service.get_credential_rows, str(user["id"]), None)
    outcomes = await reverify_rows(rows, verifier, caller=str(user["id"]), route="career_analysis")
    # 검증기 장애(설정 누락·연결 실패·5xx·401·403·3xx 등)로는 분석을 만들지 않는다: 502/503으로 끝나고 아무것도 저장하지 않는다.
    # 특정 서명 원본에 대한 거절(400·404·422)은 지금처럼 그 자격증명만 제외한다 (서명 원본이 둘 이상인데 모두 실패하면 장애).
    raise_on_verifier_outage(outcomes)
    accepted, excluded = [], []
    for outcome in outcomes:
        if outcome.verified and outcome.evaluation is not None:
            accepted.append(CredentialOutcome(record_id=outcome.record_id, status="verified", accepted=True,
                                              checks=_checks(outcome), credential=_normalized(outcome.evaluation)))
        else:
            status = outcome.status if outcome.status in OUTCOME_STATUSES else "unverifiable"
            excluded.append(CredentialOutcome(record_id=outcome.record_id, status=status, accepted=False,
                                              checks=_checks(outcome)))
    expiries = [item.credential.expires_at for item in accepted if item.credential.expires_at]
    return VerificationSnapshot(
        verification_snapshot_id=uuid.uuid4(), subject_did=user["subject_did"], scope="all", as_of=started,
        valid_until=min([started + SNAPSHOT_TTL, *expiries]),
        accepted_credentials=accepted, excluded_credentials=excluded,
    )


def check_analysis(output, snapshot, request, candidates, target) -> None:
    """AI 결과는 BE가 보낸 스냅샷·공고 버전만 인용해야 한다."""
    result = output.analysis
    supplied = {(v.posting_id, v.version_id, v.content_hash) for v in [*candidates, *([target] if target else [])]}
    cited = {(s.posting_id, s.version_id) for s in result.sources}
    accepted = {item.record_id for item in snapshot.accepted_credentials}
    if (
        result.verification_snapshot_id != snapshot.verification_snapshot_id
        or result.subject_did != snapshot.subject_did
        or result.request != request
        or result.valid_until > snapshot.valid_until
        or result.catalog_version != output.catalog.catalog_version
        or any((s.posting_id, s.version_id, s.content_hash) not in supplied for s in result.sources)
        or (target is not None and target.posting_id not in {s.posting_id for s in result.sources})
        or any((c.posting_id, c.version_id) not in cited for c in result.comparisons)
        or any(e.record_id not in accepted or e.verification_snapshot_id != snapshot.verification_snapshot_id
               for e in result.skill_evidence)
    ):
        raise contract_violation("AI_ANALYSIS_INVARIANT_FAILED")


def check_context(context, analysis, resources) -> None:
    supplied = {version.version_id: version for version in resources}
    option_ids = {option.step_id for option in context.options}
    if (
        context.planner_version != PLANNER_VERSION
        or context.valid_until is None
        or context.valid_until > analysis.valid_until
        or any(supplied.get(v.version_id) != v for option in context.options for v in option.resource_options)
        or any(step.step_id not in option_ids for step in context.baseline.steps)
    ):
        raise contract_violation("AI_CONTEXT_INVARIANT_FAILED")


def check_result(result, context, analysis, job_id, request) -> None:
    options = {option.step_id: option for option in context.options}
    if (
        result.job_id != job_id
        or result.analysis_id != analysis.analysis_id
        or result.valid_until > analysis.valid_until
        or (result.generation_method == "template" and result.model is not None)
        or (request.mode == "template" and result.generation_method != "template")
        or any(step.step_id not in options
               or (step.resource is not None and step.resource not in options[step.step_id].resource_options)
               for step in result.steps)
    ):
        raise contract_violation("AI_RESULT_INVARIANT_FAILED")


def _audit_summary(audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 모델 원문 출력은 저장하지 않는다 (개인 이력·근거 문구가 섞일 수 있음). 재현용 해시·사용량·검사 결과만 남긴다.
    keep = ("prompt_hash", "provider_error", "usage", "validation_errors", "output_encoding")
    return [{key: call[key] for key in keep if key in call} for call in audit]


class CareerService:
    async def analyze(self, request, user_id: str, verifier: CredentialVerifier, ai: AIServiceClient):
        user = await run_in_threadpool(db_service.get_user_by_id, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        # 어차피 실패할 요청은 자격증명 수만큼 검증기(DXWorks)를 부르기 전에 끝낸다:
        # AI 서비스 설정이 없으면 503, 24시간 저장 한도에 닿았으면 429, 없는 공고를 가리키면 404.
        ensure_ai_configured()
        await check_daily_limit("analyses", user_id, MAX_ANALYSES_PER_DAY, "career_analysis",
                                ANALYSIS_DAILY_LIMIT_REACHED)
        target = None
        if request.target_posting_id:
            target = await run_in_threadpool(career_store.get_job, str(request.target_posting_id))
        # 재검증(검증기 호출)은 AI 묶음 요청 자리를 쥐지 않고 한다. 자리를 쥔 채 검증기를 기다리면, 협력사가 느리거나 멈춘
        # 동안 분석 몇 건이 자리(AI_MAX_CONCURRENCY개)를 모두 잡아 공고 검색·학습자료 검색·커리큘럼까지 503이 됐다.
        # 그 대신 자리가 없어 503(AI_SERVICE_BUSY)으로 끝나는 분석은 검증기를 이미 부른 뒤다 (그 양은 호출자별 호출 몫에 묶인다).
        # 사용자별 묶음 요청 한도(동시 수·분·시간 몫)는 재검증 전에 본다: 한도를 넘는 요청은 검증기를 부르지 않고 429다.
        async with ai.caller_slot("POST /v1/career-analyses", user_id):
            snapshot = await build_snapshot(user, verifier)
            # 공고 묶음을 읽기 전에 AI 묶음 요청 자리를 잡는다 (공고 검색·커리큘럼과 같다: 기다리는 요청은 묶음을 올려 두지 않는다).
            async with ai.corpus_slot("POST /v1/career-analyses"):
                now = datetime.now(timezone.utc)
                candidates = await run_in_threadpool(career_store.market_candidates, request.target_role_id, now)
                output = await ai.analyze(snapshot, request, candidates, target)
        check_analysis(output, snapshot, request, candidates, target)
        await run_in_threadpool(career_store.save_analysis, user_id, output.analysis, snapshot)
        return output.analysis

    async def create_curriculum(self, request, user_id: str, ai: AIServiceClient) -> dict:
        existing = await run_in_threadpool(career_store.find_curriculum, user_id, str(request.idempotency_key))
        if existing:
            view = curriculum_view(existing)
            if view["request"] != request.model_dump(mode="json"):
                raise HTTPException(status_code=409, detail="Idempotency key was used for a different request")
            return view
        await check_daily_limit("curricula", user_id, MAX_CURRICULA_PER_DAY, "curriculum",
                                CURRICULUM_DAILY_LIMIT_REACHED)
        analysis = await run_in_threadpool(career_store.get_analysis, user_id, str(request.analysis_id))
        if analysis.valid_until <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="Career analysis expired; create a new analysis first")
        generator: dict = {"mode": "template"}
        llm = request.mode == "llm"
        if llm:
            if not get_settings().llm_allowed(user_id):
                raise HTTPException(status_code=403, detail="External LLM access is not enabled for this account")
            await reserve_llm_generation(user_id)  # 한도면 429 (생성기 정보도 묻지 않는다)
        # 모델을 부르지 않고 끝나면 LLM 시도 자리를 돌려준다. 생성 요청을 보낸 뒤로는 AI가 모델을 부르기 전에 끝났다고
        # 답한 경우(LLM_NOT_SENT_CODES, 단계가 없어 템플릿으로 끝남)만 돌려준다 (연결이 끊긴 등 결과를 모르면 센다).
        release_llm = llm
        try:
            if llm:
                generator = await ai.generator_info()
            # 학습자료 묶음을 읽기 전에 AI 묶음 요청 자리를 잡는다 (자리를 기다리는 요청이 묶음을 메모리에 올려 두지 않게).
            async with ai.corpus_slot("POST /v1/curricula/context", caller=user_id):
                resources = await run_in_threadpool(career_store.resources)
                context = await ai.planning_context(analysis, resources, request)
            check_context(context, analysis, resources)
            job_id = uuid.uuid4()
            release_llm = False
            outcome = await ai.generate_curriculum(GenerationInput(
                job_id=job_id, analysis_id=analysis.analysis_id, analysis_valid_until=analysis.valid_until,
                request=request, context=context, generator=generator,
            ))
            if outcome.state == "failed":
                # 모델 호출 기록(audit)이 하나라도 있으면 같은 코드여도(예: 수정 단계의 LLM_CONTEXT_TOO_LARGE) 센다.
                release_llm = llm and outcome.error_code in LLM_NOT_SENT_CODES and not outcome.audit
                status = 503 if outcome.retryable or outcome.error_code.startswith("GENERATOR_") else 422
                # 상태·detail은 예전과 같다. AI 연동 오류로 기록되고 응답에 code가 붙는다 (main.py 핸들러).
                error_class = AIUnavailable if status == 503 else AIRejected
                error = error_class(status, outcome.error_code, outcome.error_code, outcome.retryable)
                if outcome.retryable:  # LLM_BUSY·LLM_PROVIDER_UNAVAILABLE 등: 잠시 뒤 다시 하면 된다
                    error.headers = {"Retry-After": str(GENERATION_RETRY_AFTER_SECONDS)}
                raise error
            release_llm = llm and outcome.result.generation_method == "template"
        finally:
            if release_llm:
                llm_curriculum_attempts.release(user_id)
                llm_curriculum_global_attempts.release(LLM_GLOBAL_KEY)
            if llm:
                leave_llm_generation(user_id)
        check_result(outcome.result, context, analysis, job_id, request)
        if outcome.result.valid_until <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="Evidence expired during generation; create a new analysis")
        row = await run_in_threadpool(lambda: career_store.save_curriculum(
            user_id=user_id, request=request, result=outcome.result, audit=_audit_summary(outcome.audit)))
        return curriculum_view(row)


career_service = CareerService()

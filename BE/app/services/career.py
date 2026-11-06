"""직무 비교·커리큘럼 흐름 (BE가 증거를 책임지고, 계산은 AI 서비스가 한다).

1. 직무 비교: 사용자의 모든 자격증명을 지금 다시 검증(reverify) → 통과한 것만 스냅샷으로 묶음 →
   검토된 공고 버전과 함께 AI에 전달 → AI 결과가 BE가 보낸 근거만 인용하는지 확인 → 저장.
2. 커리큘럼(동기): 저장된 분석(유효기간 내) + 학습자료 → AI 계획 컨텍스트 → AI 생성 →
   결과가 컨텍스트 안의 단계·자료만 쓰는지 확인 → 저장. LLM 모드는 허용된 계정만.
AI는 VC 서명 원본을 받지 않는다. 검증 판단은 BE의 DXWorks 어댑터와 domain.credential이 맡는다.
"""
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
from app.integrations.ai_service import AIServiceClient, contract_violation
from app.integrations.ports import CredentialVerifier
from app.services.career_store import career_store, curriculum_view
from app.services.database import db_service
from app.services.reverify import ReverifyOutcome, reverify_rows

SNAPSHOT_TTL = timedelta(minutes=5)  # 분석에 쓰는 재검증 결과의 유효 시간
OUTCOME_STATUSES = {"verified", "invalid", "revoked", "expired", "unverifiable"}


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
    outcomes = await reverify_rows(rows, verifier)
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
        snapshot = await build_snapshot(user, verifier)
        now = datetime.now(timezone.utc)
        candidates = await run_in_threadpool(career_store.market_candidates, request.target_role_id, now)
        target = None
        if request.target_posting_id:
            target = await run_in_threadpool(career_store.get_job, str(request.target_posting_id))
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
        analysis = await run_in_threadpool(career_store.get_analysis, user_id, str(request.analysis_id))
        if analysis.valid_until <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="Career analysis expired; create a new analysis first")
        generator: dict = {"mode": "template"}
        if request.mode == "llm":
            if user_id not in get_settings().curriculum_llm_user_ids:
                raise HTTPException(status_code=403, detail="External LLM access is not enabled for this account")
            generator = await ai.generator_info()
        resources = await run_in_threadpool(career_store.resources)
        context = await ai.planning_context(analysis, resources, request)
        check_context(context, analysis, resources)
        job_id = uuid.uuid4()
        outcome = await ai.generate_curriculum(GenerationInput(
            job_id=job_id, analysis_id=analysis.analysis_id, analysis_valid_until=analysis.valid_until,
            request=request, context=context, generator=generator,
        ))
        if outcome.state == "failed":
            status = 503 if outcome.retryable or outcome.error_code.startswith("GENERATOR_") else 422
            raise HTTPException(status_code=status, detail=outcome.error_code)
        check_result(outcome.result, context, analysis, job_id, request)
        if outcome.result.valid_until <= datetime.now(timezone.utc):
            raise HTTPException(status_code=409, detail="Evidence expired during generation; create a new analysis")
        row = await run_in_threadpool(lambda: career_store.save_curriculum(
            user_id=user_id, request=request, result=outcome.result, audit=_audit_summary(outcome.audit)))
        return curriculum_view(row)


career_service = CareerService()

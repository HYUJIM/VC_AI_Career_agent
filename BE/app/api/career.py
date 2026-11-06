"""직무 비교·커리큘럼 API. 계산은 AI 서비스(ai/), 증거·저장·권한은 BE가 맡는다.

- 사용자 API는 로그인 토큰이 반드시 필요하다 (AUTH_REQUIRED와 무관).
- 공고·학습자료 반입은 RESEARCHER_USER_IDS에 있는 연구자만 한다.
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from starlette.concurrency import run_in_threadpool

from app.ai_contract.career import (
    CareerAnalysisRequest,
    JobPostingInput,
    PreviewRequest,
    Seniority,
    SkillID,
)
from app.ai_contract.curriculum import CurriculumRequest, LearningResourceInput
from app.core.auth import Principal, require_user
from app.integrations import get_verifier
from app.integrations.ai_service import AIServiceClient, get_ai_service
from app.integrations.ports import CredentialVerifier
from app.services.career import career_service, require_researcher
from app.services.career_store import career_store, curriculum_view

router = APIRouter(tags=["career"])


def _ok(data, message: Optional[str] = None) -> dict:
    body = {"success": True, "data": data}
    if message:
        body["message"] = message
    return body


@router.get("/skills")
async def get_skill_catalog(_: Principal = Depends(require_user), ai: AIServiceClient = Depends(get_ai_service)):
    return _ok((await ai.skill_catalog()).model_dump(mode="json"))


# ── 연구자: 공고·학습자료 반입 ────────────────────────────────────

@router.post("/research/job-postings/preview")
async def preview_job_requirements(req: PreviewRequest, principal: Principal = Depends(require_user),
                                   ai: AIServiceClient = Depends(get_ai_service)):
    """원문에서 기술 후보만 뽑는다. 필수/우대·경력 판단은 연구자가 검토해 주석으로 넣는다."""
    require_researcher(principal.user_id)
    candidates = (await ai.preview_requirements(req.raw_text)).candidates
    return _ok({"candidates": [c.model_dump(mode="json") for c in candidates[:50]],
                "truncated": len(candidates) > 50})


@router.post("/research/job-postings", status_code=201)
async def import_job_posting(req: JobPostingInput, principal: Principal = Depends(require_user),
                             ai: AIServiceClient = Depends(get_ai_service)):
    """원문 인용·주석을 검토한 공고를 새 버전으로 저장한다 (URL을 방문·인증하지는 않음)."""
    require_researcher(principal.user_id)
    await ai.validate_job(req)
    version = await run_in_threadpool(career_store.import_job, req, principal.user_id)
    return _ok(version.model_dump(mode="json"), "Job posting version saved.")


@router.post("/research/learning-resources", status_code=201)
async def import_learning_resource(req: LearningResourceInput, principal: Principal = Depends(require_user),
                                   ai: AIServiceClient = Depends(get_ai_service)):
    require_researcher(principal.user_id)
    await ai.validate_resource(req)
    version = await run_in_threadpool(career_store.import_resource, req, principal.user_id)
    return _ok(version.model_dump(mode="json"), "Learning resource version saved.")


# ── 사용자: 공고·자료 조회 ─────────────────────────────────────────

@router.get("/job-postings")
async def search_job_postings(
    region: Optional[str] = Query(default=None, min_length=1, max_length=80),
    seniority: Optional[Seniority] = None,
    _: Principal = Depends(require_user),
    ai: AIServiceClient = Depends(get_ai_service),
):
    request = CareerAnalysisRequest(region=region, seniority=seniority)
    candidates = await run_in_threadpool(career_store.market_candidates, request.target_role_id,
                                         datetime.now(timezone.utc))
    return _ok((await ai.search_jobs(request, candidates)).model_dump(mode="json"))


@router.get("/job-postings/{posting_id}")
async def get_job_posting(posting_id: UUID, version_id: Optional[UUID] = None,
                          _: Principal = Depends(require_user)):
    version = await run_in_threadpool(career_store.get_job, str(posting_id),
                                      str(version_id) if version_id else None)
    return _ok(version.model_dump(mode="json"))


@router.get("/learning-resources")
async def search_learning_resources(
    skill_id: Optional[SkillID] = None,
    budget_krw: Optional[int] = Query(default=None, ge=0, le=100000000),
    _: Principal = Depends(require_user),
    ai: AIServiceClient = Depends(get_ai_service),
):
    resources = await run_in_threadpool(career_store.resources)
    found = await ai.search_resources(resources, skill_id, budget_krw)
    return _ok([item.model_dump(mode="json") for item in found])


# ── 사용자: 직무 비교 ─────────────────────────────────────────────

@router.post("/career-analyses", status_code=201)
async def create_career_analysis(
    req: CareerAnalysisRequest,
    principal: Principal = Depends(require_user),
    verifier: CredentialVerifier = Depends(get_verifier),
    ai: AIServiceClient = Depends(get_ai_service),
):
    """모든 자격증명을 지금 다시 검증한 뒤 통과한 것만 근거로 공고 요구사항과 비교한다."""
    analysis = await career_service.analyze(req, principal.user_id, verifier, ai)
    return _ok(analysis.model_dump(mode="json"), "Career analysis created.")


@router.get("/career-analyses/{analysis_id}")
async def get_career_analysis(analysis_id: UUID, principal: Principal = Depends(require_user)):
    analysis = await run_in_threadpool(career_store.get_analysis, principal.user_id, str(analysis_id))
    return _ok({"analysis": analysis.model_dump(mode="json"), "historical_only": True})


# ── 사용자: 커리큘럼 (동기 생성) ───────────────────────────────────

@router.post("/curricula", status_code=201)
async def create_curriculum(req: CurriculumRequest, principal: Principal = Depends(require_user),
                            ai: AIServiceClient = Depends(get_ai_service)):
    """템플릿은 바로, LLM 모드(허용 계정 + 동의)는 최대 수십 초 뒤에 결과를 돌려준다."""
    return _ok(await career_service.create_curriculum(req, principal.user_id, ai), "Curriculum created.")


@router.get("/curricula/{curriculum_id}")
async def get_curriculum(curriculum_id: UUID, principal: Principal = Depends(require_user)):
    row = await run_in_threadpool(career_store.get_curriculum, principal.user_id, str(curriculum_id))
    return _ok({**curriculum_view(row), "historical_only": True})

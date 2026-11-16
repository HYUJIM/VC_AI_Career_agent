"""직무별 채용 시장 통계 조회 API.

통계는 배치(python -m app.jobs.market)가 수집한 공고를 AI 서비스가 집계해 저장한 것이다. 여기서는 저장된 최신 통계만
읽는다 (AI 호출 없음). 응답에 공고 원문·수집 내부 정보는 없다. 로그인 토큰이 필요하다.
"""
from fastapi import APIRouter, Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from app.ai_contract.market import MarketRoleID
from app.core.auth import Principal, require_user
from app.services.market_store import market_store

router = APIRouter(tags=["market"])
SUMMARY_SKILLS = 10


@router.get("/market/snapshots")
async def list_market_snapshots(_: Principal = Depends(require_user)):
    """통계가 있는 직무 목록과 직무별 상위 기술 요약."""
    snapshots = await run_in_threadpool(market_store.latest_all)
    data = [{
        "role_id": s.role_id, "role_label": s.role_label, "as_of": s.as_of, "sample_size": s.sample_size,
        "companies": s.companies, "warnings": s.warnings,
        "top_skills": [skill.model_dump(mode="json") for skill in s.skills[:SUMMARY_SKILLS]],
    } for s in snapshots]
    return {"success": True, "data": data}


@router.get("/market/snapshots/{role_id}")
async def get_market_snapshot(role_id: MarketRoleID, _: Principal = Depends(require_user)):
    """직무 하나의 최신 통계 전체."""
    snapshot = await run_in_threadpool(market_store.latest, role_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Market snapshot not found")
    return {"success": True, "data": snapshot.model_dump(mode="json")}

from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, ensure_can_read_subject, get_principal, require_user
from app.core.text import StorableText
from app.integrations import get_verifier
from app.integrations.ports import CredentialVerifier
from app.services.database import db_service
from app.services.reverify import is_verifier_outage, reverify_rows
from app.services.verifier_budget import caller_key

router = APIRouter()


@router.post("/credentials/{record_id}/reverify")
async def reverify_credential(
    record_id: UUID,
    principal: Principal = Depends(require_user),
    verifier: CredentialVerifier = Depends(get_verifier),
):
    row = await run_in_threadpool(db_service.get_credential_row, str(record_id))
    # 남의 자격증명이면 존재 여부도 알리지 않는다.
    if not row or str(row["user_id"]) != principal.user_id:
        raise HTTPException(status_code=404, detail="Credential not found")
    [outcome] = await reverify_rows([row], verifier, caller=principal.user_id, route="reverify")
    if outcome.error is not None and is_verifier_outage(outcome.error):
        # 검증기 장애(설정 누락·연결 실패·5xx·401·403·3xx 등)는 지금처럼 502/503으로 답한다 (main.py).
        raise outcome.error
    # 그 밖의 검증기 오류(이 서명 원본 하나에 대한 400·404·422 거절, 형식이 틀린 2xx)는 이 자격증명의 결과다: 근거 조회·
    # 포트폴리오와 같은 분류(unverifiable / verifier_error)로 200으로 답하고 저장된 상태는 바꾸지 않는다.
    if outcome.verified:
        message = "Credential re-verified."
    elif outcome.reason == "no_signed_original":
        message = "No signed original to re-verify."
    else:
        message = f"Re-verification failed: {outcome.reason}"
    return {"success": outcome.verified, "message": message, "data": outcome.evidence}


@router.get("/subjects/{did}/evidence")
async def get_evidence(
    # NUL(%00)이 든 DID는 DB 조회에서 500이 나지 않게 422로 거절한다.
    did: StorableText,
    mode: Literal["strict", "labeled"] = "strict",
    record_id: Optional[list[UUID]] = Query(default=None),
    principal: Principal = Depends(get_principal),
    verifier: CredentialVerifier = Depends(get_verifier),
):
    ensure_can_read_subject(principal, did)
    user = await run_in_threadpool(db_service.get_user_by_did, did)
    if not user:
        raise HTTPException(status_code=404, detail="User not found or not verified yet.")

    ids = sorted({str(r) for r in record_id}) if record_id else None
    rows = await run_in_threadpool(db_service.get_credential_rows, str(user["id"]), ids)
    if ids is not None and len(rows) != len(ids):
        raise HTTPException(status_code=404, detail="Credential not found")

    # 자격증명 수만큼 검증기를 부르므로 호출자(사용자 또는 서비스 키)의 호출 몫 안에서만 부른다 (모자라면 429).
    outcomes = await reverify_rows(rows, verifier, caller=caller_key(principal), route="evidence")
    if mode == "labeled":
        evidence, excluded = [o.evidence for o in outcomes], []
    else:
        evidence = [o.evidence for o in outcomes if o.verified]
        excluded = [{"record_id": o.record_id, "status": o.status, "reason": o.reason}
                    for o in outcomes if not o.verified]
    return {"subject_did": did, "mode": mode, "evidence": evidence, "excluded": excluded}

from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, ensure_can_read_subject, get_principal, require_user
from app.integrations import get_verifier
from app.integrations.ports import CredentialVerifier
from app.services.database import db_service
from app.services.reverify import reverify_rows

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
    [outcome] = await reverify_rows([row], verifier, raise_provider_errors=True)
    if outcome.verified:
        message = "Credential re-verified."
    elif outcome.reason == "no_signed_original":
        message = "No signed original to re-verify."
    else:
        message = f"Re-verification failed: {outcome.reason}"
    return {"success": outcome.verified, "message": message, "data": outcome.evidence}


@router.get("/subjects/{did}/evidence")
async def get_evidence(
    did: str,
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

    outcomes = await reverify_rows(rows, verifier)
    if mode == "labeled":
        evidence, excluded = [o.evidence for o in outcomes], []
    else:
        evidence = [o.evidence for o in outcomes if o.verified]
        excluded = [{"record_id": o.record_id, "status": o.status, "reason": o.reason}
                    for o in outcomes if not o.verified]
    return {"subject_did": did, "mode": mode, "evidence": evidence, "excluded": excluded}

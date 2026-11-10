import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, require_user
from app.core.text import ensure_storable
from app.integrations import get_verifier
from app.integrations.ports import CredentialVerifier
from app.services.database import db_service
from app.services.public_view_gate import BudgetExceeded, public_view_gate
from app.services.reverify import reverify_rows

router = APIRouter()
public_router = APIRouter()
logger = logging.getLogger(__name__)

NOT_FOUND = "Share link not found or expired"
VERIFIER_BUSY = "Share verification is busy. Try again later."


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ShareCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_ids: list[UUID] = Field(min_length=1, max_length=50)
    # StorableText와 같은 검사(NUL·짝 없는 서로게이트 → 500 대신 422). 길이 제한을 검사기보다 앞에 두어야
    # 너무 긴 값의 422 메시지가 지금과 같다 (Optional[StorableText] + Field(max_length)는 메시지가 바뀜).
    label: Optional[Annotated[str, Field(max_length=100), AfterValidator(ensure_storable)]] = None
    expires_in_days: int = Field(default=14, ge=1, le=90)


def _summary(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "label": row["label"],
        "record_ids": [str(r) for r in row["record_ids"]],
        "expires_at": row["expires_at"].isoformat(),
        "revoked": row["revoked_at"] is not None,
        "view_count": row["view_count"],
        "last_viewed_at": row["last_viewed_at"].isoformat() if row["last_viewed_at"] else None,
        "created_at": row["created_at"].isoformat(),
    }


@router.post("", status_code=201)
def create_share(req: ShareCreateRequest, principal: Principal = Depends(require_user)):
    ids = sorted({str(r) for r in req.record_ids})
    if len(db_service.get_credential_rows(principal.user_id, ids)) != len(ids):
        raise HTTPException(status_code=404, detail="Credential not found")

    token = secrets.token_urlsafe(32)
    label = (req.label or "").strip() or None
    row = db_service.create_share(
        user_id=principal.user_id, token_hash=_hash(token), record_ids=ids, label=label,
        expires_at=datetime.now(timezone.utc) + timedelta(days=req.expires_in_days),
    )
    return {
        "success": True,
        "message": "Share link created. The token is shown only once.",
        "data": {**_summary(row), "token": token, "share_path": f"/share/{token}"},
    }


@router.get("")
def list_shares(principal: Principal = Depends(require_user)):
    return {"success": True, "data": [_summary(r) for r in db_service.list_shares(principal.user_id)]}


@router.delete("/{share_id}")
def revoke_share(share_id: UUID, principal: Principal = Depends(require_user)):
    if not db_service.revoke_share(principal.user_id, str(share_id)):
        raise HTTPException(status_code=404, detail="Share not found")
    return {"success": True, "message": "Share link revoked."}


@public_router.get("/{token}")
async def view_share(token: str, verifier: CredentialVerifier = Depends(get_verifier)):
    """기업용 조회. 로그인 없이 링크로 열고, 열 때 서명 원본을 다시 검증한다.

    로그인 없는 요청이라 검증기 호출 양을 묶고(public_view_gate.py: 같은 원본은 60초 동안 다시 묻지 않음, 태스크마다
    분당 300번까지, 모자라면 503 + Retry-After), 결과를 학생의 DB 사본에 저장하지 않는다(익명 요청이 남의 레코드를
    고쳐 쓰지 않게). 학생 지갑의 상태는 본인의 재검증·근거 조회·포트폴리오 생성·직무 비교 때 갱신된다.
    """
    if len(token) > 128:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    share = await run_in_threadpool(db_service.open_share, _hash(token))
    if not share:
        raise HTTPException(status_code=404, detail=NOT_FOUND)

    user_id = str(share["user_id"])
    user = await run_in_threadpool(db_service.get_user_by_id, user_id)
    rows = await run_in_threadpool(db_service.get_credential_rows, user_id, [str(r) for r in share["record_ids"]])
    try:
        outcomes = await reverify_rows(rows, verifier, persist=False, gate=public_view_gate)
    except BudgetExceeded as exc:
        # 링크 토큰은 로그에 남기지 않는다 (공유 ID만).
        logger.warning("Public share view rejected: verifier call budget exceeded (share=%s retry_after=%s)",
                       share["id"], exc.retry_after)
        raise HTTPException(status_code=503, detail=VERIFIER_BUSY,
                            headers={"Retry-After": str(exc.retry_after)}) from None
    return {
        # 이메일 등 다른 개인정보는 노출하지 않는다.
        "holder": {"name": user["name"], "did": user["subject_did"]},
        "credentials": [{**o.evidence, "vc_jwt": o.vc_jwt} for o in outcomes],
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": share["expires_at"].isoformat(),
    }

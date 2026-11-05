from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, ensure_can_write, get_principal
from app.core.config import get_settings
from app.integrations import get_issuer
from app.integrations.ports import CredentialIssuer
from app.services.database import db_service

router = APIRouter()


class IssueRequest(BaseModel):
    # 비우면 서버 .env의 DXWORKS_BADGE_TEMPLATE_ID를 쓴다 (템플릿 ID를 FE 코드에 두지 않기 위함).
    templateId: Optional[str] = None
    holderDid: str = Field(min_length=5)


@router.post("/issue")
async def issue_mock_badge(
    req: IssueRequest,
    principal: Principal = Depends(get_principal),
    issuer: CredentialIssuer = Depends(get_issuer),
):
    settings = get_settings()
    if not settings.mock_issuer_enabled:
        raise HTTPException(status_code=404, detail="Mock issuer is disabled")
    ensure_can_write(principal)

    template_id = (req.templateId or "").strip() or settings.dxworks_badge_template_id
    if not template_id:
        raise HTTPException(status_code=503, detail="DXWORKS_BADGE_TEMPLATE_ID가 설정되지 않았습니다")

    # 회사 API 키로 발급하는 엔드포인트이므로 가입된 본인 DID로만 발급한다.
    if principal.is_user and req.holderDid != principal.did:
        raise HTTPException(status_code=403, detail="Forbidden: can only issue to your own DID")
    if not await run_in_threadpool(db_service.get_user_by_did, req.holderDid):
        raise HTTPException(status_code=404, detail="Holder DID is not registered")

    issued = await issuer.issue_badge(template_id, req.holderDid)
    return {
        "success": True,
        "message": "Successfully issued a mock badge",
        "vcJwt": issued.vc_jwt,
    }

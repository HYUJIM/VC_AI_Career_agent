import hashlib
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, require_user
from app.core.config import get_settings
from app.core.text import StorableText, ensure_storable
from app.integrations import get_issuer
from app.integrations.ports import CredentialIssuer, ProviderError
from app.services.database import db_service

router = APIRouter()
logger = logging.getLogger(__name__)


class IssueRequest(BaseModel):
    # 비우면 서버 .env의 DXWORKS_BADGE_TEMPLATE_ID를 쓴다 (템플릿 ID를 FE 코드에 두지 않기 위함).
    # 다른 값은 MOCK_ISSUER_TEMPLATE_IDS에 넣은 템플릿만 받는다 (회사 API 키로 아무 템플릿이나 발급하지 않게).
    # 길이 제한을 검사기보다 앞에 두어 너무 긴 값의 422 메시지를 pydantic 기본 문구로 유지한다 (shares.label과 같은 형식).
    templateId: Optional[Annotated[str, Field(max_length=255), AfterValidator(ensure_storable)]] = None
    holderDid: StorableText = Field(min_length=5, max_length=255)  # users.subject_did VARCHAR(255)


@router.post("/issue")
async def issue_mock_badge(
    req: IssueRequest,
    # 회사 API 키로 발급하므로 로그인 사용자 토큰만 받는다 (서비스 키는 401).
    principal: Principal = Depends(require_user),
    issuer: CredentialIssuer = Depends(get_issuer),
):
    settings = get_settings()
    if not settings.mock_issuer_enabled:
        raise HTTPException(status_code=404, detail="Mock issuer is disabled")

    template_id = (req.templateId or "").strip() or settings.dxworks_badge_template_id
    if not template_id:
        # 다른 설정 누락과 같은 503 고정 응답으로 답하고, 설정 이름은 운영 로그에만 남긴다 (main.py).
        raise ProviderError("DXWORKS_BADGE_TEMPLATE_ID가 설정되지 않았습니다", unavailable=True)
    if template_id not in settings.mock_issuer_template_ids:
        # 허용 목록(기본 템플릿 + MOCK_ISSUER_TEMPLATE_IDS) 밖의 템플릿은 협력사 API를 부르지 않고 거절한다.
        raise HTTPException(status_code=403, detail="Template not allowed")

    # 회사 API 키로 발급하는 엔드포인트이므로 가입된 본인 DID로만 발급한다.
    if req.holderDid != principal.did:
        raise HTTPException(status_code=403, detail="Forbidden: can only issue to your own DID")
    if not await run_in_threadpool(db_service.get_user_by_did, req.holderDid):
        raise HTTPException(status_code=404, detail="Holder DID is not registered")

    # 사용자별 24시간 발급 한도. 발급 전에 기록을 남겨 동시 요청으로도 넘지 못하게 하고, 발급이 실패하면 기록을 지운다
    # (실패한 발급은 한도에 세지 않는다).
    issuance_id = await run_in_threadpool(
        db_service.reserve_mock_issuance, principal.user_id, template_id, settings.mock_issuer_daily_limit)
    if issuance_id is None:
        raise HTTPException(status_code=429, detail="Mock issuance limit reached; try again later")
    try:
        issued = await issuer.issue_badge(template_id, req.holderDid)
    except Exception:
        try:
            await run_in_threadpool(db_service.cancel_mock_issuance, issuance_id)
        except Exception as exc:
            # 기록을 지우지 못해도(DB 오류) 원래 오류(ProviderError → 502/503)로 답한다. 지우지 못한 기록은 한도에 센다.
            # 로그에는 발급 기록 id와 예외 클래스 이름만 남긴다 (토큰·DID·예외 메시지는 남기지 않는다).
            logger.warning("Mock issuance cancel failed; the reservation stays counted: issuance=%s error=%s",
                           issuance_id, type(exc).__name__)
        raise
    # 원문 대신 해시만 남긴다 (어떤 배지가 모의 발급으로 만들어졌는지 확인용).
    # 협력사 발급은 되돌릴 수 없으므로 이 기록이 DB 오류로 실패해도 배지는 돌려준다 (한도는 위의 예약 기록으로 이미 셌다).
    try:
        await run_in_threadpool(db_service.complete_mock_issuance, issuance_id,
                                hashlib.sha256(issued.vc_jwt.encode("utf-8")).hexdigest())
    except Exception as exc:
        logger.warning("Mock issuance token hash not recorded: issuance=%s error=%s", issuance_id, type(exc).__name__)
    return {
        "success": True,
        "message": "Successfully issued a mock badge",
        "vcJwt": issued.vc_jwt,
    }

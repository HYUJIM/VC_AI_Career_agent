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
from app.services.verifier_budget import MOCK_ISSUE_ATTEMPTS, mock_issue_attempts, mock_issue_breaker

router = APIRouter()
logger = logging.getLogger(__name__)

LIMIT_REACHED = "Mock issuance limit reached; try again later"
# 전체 한도(모든 사용자를 합친 발급 수: DB. 무엇을 세는지는 database.reserve_mock_issuance)에 닿았을 때와, 실패 차단기의
# 창이 진행 중인 요청만으로 찼을 때: 사용자 자신의 한도와 구분해 답한다 (FE가 이 문구로 '전체 한도' 안내를 고른다).
SERVICE_LIMIT_REACHED = "Mock issuance service limit reached; try again later."
# 협력사 발급이 잇달아 실패해 발급을 잠시 멈춘 동안(실패 차단기: verifier_budget.FailureBreaker. 창에 실패로 끝난 요청이
# 있을 때만). Retry-After(다음 탐침을 보낼 수 있을 때까지의 초)와 함께 답한다. 한도에 닿은 것이 아니라 잠시 뒤 다시 하면
# 되는 상태라 문구를 따로 둔다.
ISSUANCE_PAUSED = "Mock issuance is paused after repeated failures. Try again shortly."


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

    # 실패한 발급까지 세는 사용자별 24시간 시도 한도 (프로세스 메모리, verifier_budget.py). 아래 DB 한도는 성공한 발급만
    # 세므로, 협력사가 실패하거나 우리를 제한하는 동안 한 계정이 회사 API 키로 끝없이 다시 부르는 것을 이 한도가 막는다.
    # 성공한 발급도 시도로 세므로 한도는 발급 한도의 3배 이상으로 둔다 (MOCK_ISSUER_DAILY_LIMIT을 올린 설정을 가리지 않게).
    if not mock_issue_attempts.reserve(principal.user_id,
                                       max(MOCK_ISSUE_ATTEMPTS, 3 * settings.mock_issuer_daily_limit)):
        raise HTTPException(status_code=429, detail=LIMIT_REACHED)
    # 태스크 전체의 실패 차단기 (verifier_budget.FailureBreaker, 한도는 MOCK_ISSUER_GLOBAL_DAILY_LIMIT). 사용자별 시도 한도는
    # 계정 수만큼 곱해지므로, 협력사가 실패하거나 우리를 제한하는 동안 여러 계정이 회사 API 키로 부르는 양을 합쳐서 묶는다.
    # - 부르기 전에 자리를 잡고, 발급에 성공했거나(성공은 DB 한도가 센다) 아무것도 보내지 않은 요청만 자리를 돌려준다.
    # - 실패가 24시간에 한도만큼 쌓이면 발급을 멈추고 60초마다 요청 하나만 협력사로 보내 본다(탐침). 그 밖의 요청은 여기서
    #   429 + Retry-After로 끝난다 (협력사를 부르지 않고, 사용자별 시도로도 세지 않는다).
    # - 탐침이 성공하면 실패 기록을 모두 지운다: 협력사가 돌아오면 재시작이나 24시간을 기다리지 않고 모두가 다시 발급한다.
    # - 창이 진행 중인 요청의 자리만으로 찼으면(아무것도 실패하지 않았다: 한도가 작을 때 한꺼번에 몰린 요청) 남은 몫을 그
    #   요청들이 잡고 있는 것이므로 '멈춤'이 아니라 전체 한도 문구로 답한다 (Retry-After 없음, 탐침도 보내지 않는다).
    # 그래서 협력사가 계속 실패하는 동안 프로세스 하나가 회사 API 키로 보내는 실패한 발급 요청은 한도만큼, 그 뒤로는 분당 한 번이다.
    ticket, paused = mock_issue_breaker.admit(settings.mock_issuer_global_daily_limit)
    if ticket is None:
        mock_issue_attempts.release(principal.user_id)
        if not paused:
            raise HTTPException(status_code=429, detail=SERVICE_LIMIT_REACHED)
        raise HTTPException(status_code=429, detail=ISSUANCE_PAUSED,
                            headers={"Retry-After": str(mock_issue_breaker.retry_after())})
    # 차단기에 알릴 결과: 협력사를 부르지 않고 끝나면 not_sent, 협력사까지 간 실패와 결과를 모르는 실패는 failure, 발급되면 success
    outcome = "not_sent"
    try:
        # 사용자별 24시간 발급 한도와 모든 사용자를 합친 한도 (무엇을 세는지는 database.reserve_mock_issuance). 발급 전에
        # 기록을 남겨 동시 요청으로도 넘지 못하게 하고, 발급이 실패하면 기록을 지운다 (실패한 발급은 이 한도에 세지 않는다.
        # 결과를 모르는 실패는 아래에서 남긴다).
        try:
            issuance_id, refused_by = await run_in_threadpool(
                db_service.reserve_mock_issuance, principal.user_id, template_id, settings.mock_issuer_daily_limit,
                settings.mock_issuer_global_daily_limit)
        except Exception:
            mock_issue_attempts.release(principal.user_id)  # 협력사를 부르지 않았으므로 시도로 세지 않는다
            raise
        if issuance_id is None:
            mock_issue_attempts.release(principal.user_id)
            raise HTTPException(status_code=429,
                                detail=SERVICE_LIMIT_REACHED if refused_by == "global" else LIMIT_REACHED)
        outcome = "failure"  # 여기부터는 협력사를 부른 요청이다 (응답을 기다리다 취소돼도 실패로 남긴다)
        try:
            issued = await issuer.issue_badge(template_id, req.holderDid)
        except Exception as error:
            if isinstance(error, ProviderError) and error.ambiguous:
                # 요청이 협력사에 닿았을 수 있는데 결과를 모른다(응답 대기 중 시간 초과·끊김, jwt 없는 2xx 등). 배지가 이미
                # 발급됐을 수 있으므로 기록을 지우지 않고 한도에 센다 (해시는 비어 있다): 사용자별 한도에는 24시간 동안,
                # 전체 한도에는 진행 중 창(database.MOCK_ISSUANCE_PENDING_SECONDS) 동안. 원래 오류(502)로 답한다.
                logger.warning("Mock issuance outcome unknown; the reservation stays counted: issuance=%s", issuance_id)
                raise
            if isinstance(error, ProviderError) and error.unavailable:
                # 설정 누락 등으로 협력사를 부르지 못했다: 시도로 세지 않는다
                mock_issue_attempts.release(principal.user_id)
                outcome = "not_sent"
            try:
                await run_in_threadpool(db_service.cancel_mock_issuance, issuance_id)
            except Exception as exc:
                # 기록을 지우지 못해도(DB 오류) 원래 오류(ProviderError → 502/503)로 답한다. 지우지 못한 기록은 한도에 센다.
                # 로그에는 발급 기록 id와 예외 클래스 이름만 남긴다 (토큰·DID·예외 메시지는 남기지 않는다).
                logger.warning("Mock issuance cancel failed; the reservation stays counted: issuance=%s error=%s",
                               issuance_id, type(exc).__name__)
            raise
        outcome = "success"
    finally:
        mock_issue_breaker.leave(ticket, outcome)
    # 원문 대신 해시만 남긴다 (어떤 배지가 모의 발급으로 만들어졌는지 확인용).
    # 협력사 발급은 되돌릴 수 없으므로 이 기록이 DB 오류로 실패해도 배지는 돌려준다 (한도는 위의 예약 기록으로 이미 셌다.
    # 해시가 없는 그 기록은 사용자별 한도에는 24시간, 전체 한도에는 진행 중 창 동안만 센다. 차단기에는 이미 성공으로 알렸으므로
    # 창이 지난 뒤 이런 배지의 양은 사용자별 하루 발급 한도 × 계정 수만 묶는다).
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

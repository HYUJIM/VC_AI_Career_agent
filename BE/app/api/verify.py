from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, ensure_can_write, get_principal
from app.core.config import get_settings
from app.core.text import StorableText
from app.domain.credential import (
    BadgeEvaluation, ProviderBadgeResult, evaluate_badge, is_compact_jws, is_issuer_confirmed, quarantine_evaluation,
)
from app.integrations import get_verifier
from app.integrations.ports import CredentialVerifier, ProviderError
from app.services.database import db_service
from app.services.reverify import is_verifier_outage
from app.services.verifier_budget import (
    audit, caller_key, log_verifier_calls, release_rejected_verification, reserve_rejected_verification,
    reserve_verifier_calls,
)

router = APIRouter()

# 서명 원본(VC-JWT·VP) 최대 길이. 실제 토큰은 수 KB이며, 이 값은 검증기(DXWorks)로 보내고 credentials.raw_token에
# 저장하는 크기의 상한이다.
MAX_TOKEN_CHARS = 256 * 1024
NOT_SAVED = "Badge verification failed."
# 검증기가 발급자 서명·신뢰를 확인하지 못했지만 본인 DID에 발급됐다고 적힌 VC: '미검증 기록'으로 격리 보관했다.
SAVED_AS_UNVERIFIED = "Badge verification failed. Saved as unverified record."
UNVERIFIED_LIMIT_REACHED = "Badge verification failed. Unverified record limit reached."


def _badge_data(result: ProviderBadgeResult, evaluation: BadgeEvaluation, record_id,
                unverified_upload: bool = False) -> dict:
    """FE용 공통 형식. 외부 검증기 응답 원문은 내보내지 않는다 (협력사 API 형식은 어댑터 안에만 둔다).

    unverified_upload: 이 요청으로 '미검증 기록'(격리 보관, 근거로 쓰지 않음)으로 저장했는지.
    """
    return {
        "record_id": record_id,
        "unverified_upload": unverified_upload,
        "status": evaluation.status,
        "valid": evaluation.verified,
        "achievement_name": evaluation.achievement_name,
        "issuer_name": evaluation.issuer_name,
        "issuer_trusted": result.issuer_trusted,
        "recipient_did": evaluation.recipient_did,
        "issued_at": evaluation.issued_at.isoformat(),
        "expires_at": evaluation.expires_at.isoformat() if evaluation.expires_at else None,
        "checks": [{"name": c.name, "result": c.result, "detail": c.detail} for c in evaluation.checks],
    }


class VerifyRequest(BaseModel):
    type: str
    # NUL·짝 없는 서로게이트는 검증기 요청과 credentials.raw_token(TEXT)에 넣을 수 없으므로 422로 거절한다.
    token: StorableText = Field(min_length=1, max_length=MAX_TOKEN_CHARS)

    @field_validator("token")
    @classmethod
    def strip_token(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


@router.post("/verify")
async def verify_credential(
    req: VerifyRequest,
    principal: Principal = Depends(get_principal),
    verifier: CredentialVerifier = Depends(get_verifier),
):
    if req.type not in ("identity", "badge"):
        raise HTTPException(status_code=400, detail='Invalid type. Use "identity" or "badge"')
    ensure_can_write(principal)
    if req.type == "identity":
        # 신원 VP 검증은 이 제품의 화면·흐름이 쓰지 않는다 (VP를 만드는 곳이 없다). 사용자 입력에 회사 API 키를 붙여 협력사로
        # 보내는 경로라서 서버 설정(IDENTITY_VERIFICATION_ENABLED)으로 켠 경우에만 받는다.
        if not get_settings().identity_verification_enabled:
            raise HTTPException(status_code=400, detail="Identity verification is disabled")
        return await _verify_identity(req.token, principal, verifier)
    return await _verify_badge(req.token, principal, verifier)


async def _ask_verifier(principal: Principal, route: str, call):
    """호출 몫을 가져간 뒤 검증기를 한 번 부르고(call) 감사 로그를 남긴다.

    몫이 모자라면 검증기를 부르지 않고 verifier_budget.VerifierBudgetExceeded를 던진다 (main.py가 429로 답한다).
    """
    caller = caller_key(principal)
    reserve_verifier_calls(caller, route, 1)
    failed = 1
    try:
        result = await call()
        failed = 0
        return result
    finally:
        log_verifier_calls(caller, route, 1, failed)


async def _verify_identity(token: str, principal: Principal, verifier: CredentialVerifier) -> dict:
    if not is_compact_jws(token):
        # compact JWS 모양이 아닌 값은 회사 API 키와 함께 협력사로 보내지 않는다.
        raise HTTPException(status_code=422, detail="Invalid token format")
    result = await _ask_verifier(principal, "verify_identity", lambda: verifier.verify_presentation(token))
    # 홀더 DID는 검증기가 돌려준 비어 있지 않은 문자열이고 로그인 사용자 본인 DID와 같아야 한다.
    # 홀더 DID가 없으면 누구의 신원인지(본인인지) 정할 수 없으므로 다른 사람의 VP와 같이 거절하고 클레임도 돌려주지 않는다.
    holder_did = result.holder_did
    if not isinstance(holder_did, str) or not holder_did or holder_did != principal.did:
        raise HTTPException(status_code=403, detail="Forbidden: presentation holder is not you")
    # 신원 VP는 검증 결과만 돌려준다. 계정은 회원가입으로만 만든다 (예전의 사용자 자동 생성 제거).
    return {
        "success": result.verified,
        "message": "Identity verified." if result.verified else "Identity verification failed.",
        "data": {"verified": result.verified, "holder_did": result.holder_did, "claims": result.claims},
    }


def _addressed_to(evaluation: BadgeEvaluation, did: str) -> bool:
    """해석한 VC에 credentialSubject가 있고 수신자가 이 DID라고 적혀 있는지 (확인되지 않은 값일 수 있다)."""
    return isinstance(evaluation.vc.get("credentialSubject"), dict) and evaluation.recipient_did == did


async def _verify_badge(token: str, principal: Principal, verifier: CredentialVerifier) -> dict:
    caller = caller_key(principal)
    if not is_compact_jws(token):
        # compact JWS 모양이 아닌 값(아무 문자열, 주입 시도 문자열, alg=none 등)은 검증기(DXWorks)로 넘기지 않는다.
        # 검증기가 거절한 토큰과 같은 형식으로 답한다 (서명 확인 실패, 저장하지 않음).
        # 검증기를 부르지 않아 event=verifier_calls 줄이 없으므로, 모양 때문에 거절했다는 흔적을 한 줄 남긴다 (토큰·길이 없음).
        audit.info("event=verify_shape_refused user=%s", caller)
        result = ProviderBadgeResult(valid=False, signature_ok=False, issuer_trusted=None, credential_status=None,
                                     recipient_did=None, credential_id=None, issuer_name=None, achievement_name=None,
                                     issued_at=None)
        return {
            "success": False,
            "message": NOT_SAVED,
            "data": _badge_data(result, evaluate_badge(result, token), None),
        }
    # 토큰이 거절된 검증은 호출자마다 10분에 10번까지다 (verifier_budget.rejected_verifications). 부르기 전에 자리를 잡고,
    # 창이 찼으면 여기서 429로 끝난다 (호출 몫을 쓰지 않고 검증기도 부르지 않는다).
    reserve_rejected_verification(caller)
    # 잡은 자리를 남길지. 남기는 것은 이 토큰이 거절된 요청뿐이고, 그 자리마다 아래에서 rejected = True로 적는다:
    # 자격증명 하나에 대한 검증기 오류, 저장하지 않는 success: false(해석할 수 없는 원본·수신자 없음·발급자 미확인),
    # 다른 DID에 발급된 배지(403), 다른 사용자가 등록한 배지(409). 검증기에 묻는 동안 취소되거나 다른 오류로 끝난
    # 요청도 남긴다 (답을 모른다: 요청을 끊는 것으로 창을 피해 가지 못하게).
    # 돌려주는 것: 통과한 요청, 저장된 배지(폐기·만료로 저장돼도 이 사용자의 진짜 배지다), 검증기 장애, 호출 몫 부족(429),
    # 검증기를 부르기 전에 난 오류, 그리고 검증기가 답한 뒤에 난 우리 쪽 실패(DB가 바쁨·그 밖의 오류·DB 단계에서의 취소).
    rejected = False

    async def call():
        nonlocal rejected
        rejected = True  # 검증기에 묻는 동안: 취소되거나 검증기 오류가 아닌 예외로 끝나면 남긴다
        return await verifier.verify_badge(token)

    try:
        try:
            result = await _ask_verifier(principal, "verify_badge", call)
        except ProviderError as exc:
            rejected = not is_verifier_outage(exc)  # 장애는 돌려주고, 자격증명 하나에 대한 오류는 남긴다
            raise
        rejected = False  # 검증기가 답했다: 여기부터는 아래의 거절로 끝날 때만 남긴다 (우리 쪽 실패는 사용자 탓이 아니다)
        evaluation = evaluate_badge(result, token)

        if not evaluation.vc or not evaluation.recipient_did or not is_issuer_confirmed(result, evaluation):
            # 원본을 해석할 수 없거나 수신자를 알 수 없으면 누구의 자격증명인지 정할 수 없으므로 저장하지 않는다.
            # 검증기가 발급자 서명·신뢰를 확인하지 못한 토큰(위조·변조 의심)은 본문(자격증명 ID·수신자)을 믿을 수 없으므로
            # 진짜 배지처럼 저장하지 않는다 (남의 레코드 덮어쓰기·ID 선점 차단: 소유 여부를 DB에서 찾지 않는다).
            # 다만 본인 DID에 발급됐다고 적힌 VC는 '미검증 기록'으로 격리 보관한다: 키는 사용자 + 원본 해시이고 상태는
            # invalid이며, 포트폴리오·직무 비교를 실행할 때마다 다시 검증하지만 근거로는 쓰지 않고 '미검증 기록'으로 표시한다.
            # 거절된 검증이므로 rejected-verification 자리는 그대로 남긴다 (위조 업로드를 반복하는 양도 묶인다).
            rejected = True
            if _addressed_to(evaluation, principal.did):
                quarantined = quarantine_evaluation(evaluation, principal.user_id, token)
                saved = await run_in_threadpool(db_service.save_unverified_upload, user_id=principal.user_id,
                                                evaluation=quarantined, vc_jwt=token)
                return {
                    "success": False,
                    "message": SAVED_AS_UNVERIFIED if saved else UNVERIFIED_LIMIT_REACHED,
                    "data": _badge_data(result, quarantined, saved["record_id"] if saved else None,
                                        unverified_upload=saved is not None),
                }
            return {
                "success": False,
                "message": NOT_SAVED,
                "data": _badge_data(result, evaluation, None),
            }

        # 본인(토큰 DID)에게 발급된 자격증명만 본인 지갑에 저장한다.
        if evaluation.recipient_did != principal.did:
            rejected = True
            raise HTTPException(status_code=403, detail="Forbidden: credential was issued to a different DID")
        user_id = principal.user_id

        owner = await run_in_threadpool(db_service.find_credential_owner, evaluation.record_id)
        if owner is not None and owner != user_id:
            rejected = True
            raise HTTPException(status_code=409, detail="Credential already registered to another user")

        saved = await run_in_threadpool(
            db_service.save_badge,
            user_id=user_id,
            provider=getattr(verifier, "provider_name", "unknown"),
            evaluation=evaluation,
            vc_jwt=token,
        )
        # 저장한 배지는 상태가 무엇이든(verified·revoked·expired) 자리를 돌려준다 (rejected는 False 그대로다).
        return {
            "success": evaluation.verified,
            "message": "Badge verified and saved." if evaluation.verified
            else f"Badge verification failed. Saved with status '{saved['status']}'.",
            "data": _badge_data(result, evaluation, saved["record_id"]),
        }
    finally:
        if not rejected:
            release_rejected_verification(caller)

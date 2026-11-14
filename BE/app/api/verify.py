from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, ensure_can_write, get_principal
from app.core.text import StorableText
from app.domain.credential import BadgeEvaluation, ProviderBadgeResult, evaluate_badge, is_issuer_confirmed
from app.integrations import get_verifier
from app.integrations.ports import CredentialVerifier
from app.services.database import db_service

router = APIRouter()

# 서명 원본(VC-JWT·VP) 최대 길이. 실제 토큰은 수 KB이며, 이 값은 검증기(DXWorks)로 보내고 credentials.raw_token에
# 저장하는 크기의 상한이다.
MAX_TOKEN_CHARS = 256 * 1024


def _badge_data(result: ProviderBadgeResult, evaluation: BadgeEvaluation, record_id) -> dict:
    """FE용 공통 형식. 외부 검증기 응답 원문은 내보내지 않는다 (협력사 API 형식은 어댑터 안에만 둔다)."""
    return {
        "record_id": record_id,
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
        return await _verify_identity(req.token, principal, verifier)
    return await _verify_badge(req.token, principal, verifier)


async def _verify_identity(token: str, principal: Principal, verifier: CredentialVerifier) -> dict:
    result = await verifier.verify_presentation(token)
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


async def _verify_badge(token: str, principal: Principal, verifier: CredentialVerifier) -> dict:
    result = await verifier.verify_badge(token)
    evaluation = evaluate_badge(result, token)

    if not evaluation.vc or not evaluation.recipient_did or not is_issuer_confirmed(result, evaluation):
        # 원본을 해석할 수 없거나 수신자를 알 수 없으면 누구의 자격증명인지 정할 수 없으므로 저장하지 않는다.
        # 검증기가 발급자 서명·신뢰를 확인하지 못한 토큰도 본문(자격증명 ID·수신자)을 믿을 수 없으므로
        # 계정·소유 여부를 따지지 않고(DB 조회 없이) 저장하지 않는다 (위조 토큰으로 남의 레코드 덮어쓰기·ID 선점 차단).
        return {
            "success": False,
            "message": "Badge verification failed.",
            "data": _badge_data(result, evaluation, None),
        }

    # 본인(토큰 DID)에게 발급된 자격증명만 본인 지갑에 저장한다.
    if evaluation.recipient_did != principal.did:
        raise HTTPException(status_code=403, detail="Forbidden: credential was issued to a different DID")
    user_id = principal.user_id

    owner = await run_in_threadpool(db_service.find_credential_owner, evaluation.record_id)
    if owner is not None and owner != user_id:
        raise HTTPException(status_code=409, detail="Credential already registered to another user")

    saved = await run_in_threadpool(
        db_service.save_badge,
        user_id=user_id,
        provider=getattr(verifier, "provider_name", "unknown"),
        evaluation=evaluation,
        vc_jwt=token,
    )
    return {
        "success": evaluation.verified,
        "message": "Badge verified and saved." if evaluation.verified
        else f"Badge verification failed. Saved with status '{saved['status']}'.",
        "data": _badge_data(result, evaluation, saved["record_id"]),
    }

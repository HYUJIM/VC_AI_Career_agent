from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, ensure_can_write, get_principal
from app.domain.credential import BadgeEvaluation, ProviderBadgeResult, evaluate_badge
from app.integrations import get_verifier
from app.integrations.ports import CredentialVerifier
from app.services.database import db_service

router = APIRouter()


def _badge_data(result: ProviderBadgeResult, evaluation: BadgeEvaluation, record_id) -> dict:
    """FE/AI용 공통 형식. 외부 검증기 응답 원문은 내보내지 않는다 (협력사 API 형식은 어댑터 안에만 둔다)."""
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
    token: str = Field(min_length=1)

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
    if principal.is_user and result.holder_did and result.holder_did != principal.did:
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

    if not evaluation.vc or not evaluation.recipient_did:
        # 원본을 해석할 수 없거나 수신자를 알 수 없으면 누구의 자격증명인지 정할 수 없으므로 저장하지 않는다.
        return {
            "success": False,
            "message": "Badge verification failed.",
            "data": _badge_data(result, evaluation, None),
        }

    if principal.is_user:
        if evaluation.recipient_did != principal.did:
            raise HTTPException(status_code=403, detail="Forbidden: credential was issued to a different DID")
        user_id = principal.user_id
    else:
        # 전환 기간(AUTH_REQUIRED=false) 익명 요청: 수신자 DID로 가입한 사용자에게 저장한다.
        user = await run_in_threadpool(db_service.get_user_by_did, evaluation.recipient_did)
        if not user:
            raise HTTPException(status_code=404, detail="Recipient DID is not registered")
        user_id = str(user["id"])

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

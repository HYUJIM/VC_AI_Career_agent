from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import Principal, ensure_can_read_subject, get_principal
from app.core.text import StorableText
from app.services.database import db_service

router = APIRouter()


# did: NUL(%00)이 든 경로는 DB 조회 단계의 500이 아니라 422로 거절한다.
@router.get("/{did}/careers")
def get_user_profile_and_careers(did: StorableText, principal: Principal = Depends(get_principal)):
    ensure_can_read_subject(principal, did)
    data = db_service.get_user_and_credentials(did)
    if not data:
        raise HTTPException(status_code=404, detail="User not found or not verified yet.")
    return data

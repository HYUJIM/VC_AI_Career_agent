from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import Principal, ensure_can_read_subject, get_principal
from app.services.database import db_service

router = APIRouter()


@router.get("/{did}/careers")
def get_user_profile_and_careers(did: str, principal: Principal = Depends(get_principal)):
    ensure_can_read_subject(principal, did)
    data = db_service.get_user_and_credentials(did)
    if not data:
        raise HTTPException(status_code=404, detail="User not found or not verified yet.")
    return data

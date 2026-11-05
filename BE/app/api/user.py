from fastapi import APIRouter, HTTPException
from app.services.database import db_service

router = APIRouter()

@router.get("/{did}/careers")
async def get_user_profile_and_careers(did: str):
    try:
        data = db_service.get_user_and_credentials(did)
        if not data:
            raise HTTPException(status_code=404, detail="User not found or not verified yet.")
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

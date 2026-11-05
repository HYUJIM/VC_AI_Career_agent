from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services.dxworks import dxworks_service

router = APIRouter()

class IssueRequest(BaseModel):
    templateId: str
    holderDid: str

@router.post("/issue")
async def issue_mock_badge(req: IssueRequest):
    try:
        response = await dxworks_service.issue_mock_badge(req.templateId, req.holderDid)
        
        jwt_string = response.get("data", {}).get("jwt") or response.get("jwt") or response
        
        return {
            "success": True,
            "message": "Successfully issued a mock badge",
            "vcJwt": jwt_string
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

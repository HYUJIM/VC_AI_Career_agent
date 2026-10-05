from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import base64
import json
import uuid
from datetime import datetime, timezone
from app.services.dxworks import dxworks_service
from app.services.database import db_service, UserCreate, CredentialCreate

router = APIRouter()

class VerifyRequest(BaseModel):
    type: str
    token: str

def decode_jwt_payload(token: str):
    try:
        payload_b64 = token.split('.')[1]
        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
        json_str = base64.urlsafe_b64decode(payload_b64).decode('utf-8')
        return json.loads(json_str)
    except Exception:
        return None

@router.post("/verify")
async def verify_credential(req: VerifyRequest):
    if req.type not in ["identity", "badge"]:
        raise HTTPException(status_code=400, detail='Invalid type. Use "identity" or "badge"')
    
    try:
        if req.type == "identity":
            verify_result = await dxworks_service.verify_identity(req.token)
            
            if verify_result.get("verified") and verify_result.get("claims"):
                claims = verify_result["claims"]
                
                user_data = UserCreate(
                    id=str(uuid.uuid4()),
                    subject_did=verify_result.get("holderDid", ""),
                    email=claims.get("email", "unknown@test.com"),
                    name=claims.get("name", ""),
                    created_at=datetime.now(timezone.utc).isoformat(),
                    updated_at=datetime.now(timezone.utc).isoformat()
                )
                db_service.save_user(user_data)
                
                return {"success": True, "message": "Identity verified and saved.", "data": verify_result}
            else:
                return {"success": False, "message": "Identity verification failed.", "data": verify_result}
                
        elif req.type == "badge":
            verify_result = await dxworks_service.verify_badge(req.token)
            
            if verify_result.get("valid") and verify_result.get("checks"):
                checks = verify_result["checks"]
                
                payload = decode_jwt_payload(req.token)
                vc = payload.get("vc", payload) if payload else {}
                credential_subject = vc.get("credentialSubject", {})
                achievement = credential_subject.get("achievement", {})
                
                badge_info = verify_result.get("badge", {})
                issuer_info = checks.get("issuer", {})
                issuer_trust = checks.get("issuerTrust", {})
                credential_id_info = checks.get("credentialId", {})
                recipient_info = checks.get("recipient", {})

                cred_id = credential_id_info.get("id") or vc.get("id") or str(uuid.uuid4())
                if cred_id.startswith("urn:uuid:"):
                    cred_id = cred_id.replace("urn:uuid:", "")
                    
                user_id = recipient_info.get("subjectId") or "unknown_did"
                
                cred_data = CredentialCreate(
                    record_id=cred_id,
                    user_id=user_id,
                    source_provider="dxworks_ob3",
                    source_external_id=cred_id,
                    format="OB3.0",
                    achievement_name=badge_info.get("name") or achievement.get("name") or vc.get("name") or "Unknown Badge",
                    issuer_name=issuer_info.get("issuerName") or vc.get("issuer", {}).get("name") or "Unknown Issuer",
                    issuer_trust_level="accredited" if issuer_trust.get("trusted") else "unknown",
                    status="verified" if credential_id_info.get("credentialStatus") == "VALID" else "invalid",
                    issued_at=badge_info.get("issuedAt") or datetime.now(timezone.utc).isoformat(),
                    raw_data=vc,
                    created_at=datetime.now(timezone.utc).isoformat()
                )
                
                db_service.save_credential(cred_data)
                
                return {"success": True, "message": "Badge verified and saved.", "data": verify_result}
            else:
                return {"success": False, "message": "Badge verification failed.", "data": verify_result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

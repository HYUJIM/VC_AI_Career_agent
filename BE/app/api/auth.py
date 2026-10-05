from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.services.database import get_db_connection
import uuid
from datetime import datetime, timezone

router = APIRouter()

class RegisterRequest(BaseModel):
    name: str
    email: str

class LoginRequest(BaseModel):
    email: str

@router.post("/register")
async def register_user(req: RegisterRequest):
    try:
        fake_did = f"did:key:z6MkMock{uuid.uuid4().hex[:12]}"
        now_str = datetime.now(timezone.utc).isoformat()
        new_uuid = str(uuid.uuid4())
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE email = %s", (req.email,))
                if cur.fetchone():
                    raise HTTPException(status_code=400, detail="Email already registered")
                
                cur.execute(
                    "INSERT INTO users (id, email, name, subject_did, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
                    (new_uuid, req.email, req.name, fake_did, now_str, now_str)
                )
                user = cur.fetchone()
                
        return {
            "success": True,
            "message": "회원가입 완료. 발급된 DID를 프론트엔드에서 보관하세요.",
            "data": {
                "id": str(user['id']),
                "name": user['name'],
                "email": user['email'],
                "did": user['subject_did']
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/login")
async def login_user(req: LoginRequest):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM users WHERE email = %s", (req.email,))
                user = cur.fetchone()
                
                if not user:
                    raise HTTPException(status_code=404, detail="User not found")
                    
        return {
            "success": True,
            "message": "로그인 성공",
            "data": {
                "id": str(user['id']),
                "name": user['name'],
                "email": user['email'],
                "did": user['subject_did']
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

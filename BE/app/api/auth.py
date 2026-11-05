from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from app.services.database import get_db_connection
from app.services.security import MAX_PASSWORD_BYTES, hash_password, verify_password
import uuid
from datetime import datetime, timezone

router = APIRouter()

class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def check_password_bytes(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
        return v

class LoginRequest(BaseModel):
    email: str
    password: str

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
                    "INSERT INTO users (id, email, name, subject_did, password_hash, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                    (new_uuid, req.email, req.name, fake_did, hash_password(req.password), now_str, now_str)
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

        # 이메일 존재 여부가 드러나지 않도록 실패 사유를 하나로 통일한다.
        # password_hash가 없는 사용자(신원 VP 검증으로 생성된 사용자 등)는 비밀번호 로그인 불가.
        if not user or not user['password_hash'] or not verify_password(req.password, user['password_hash']):
            raise HTTPException(status_code=401, detail="Invalid email or password")

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

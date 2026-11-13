import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.auth import Principal, create_access_token, require_user
from app.services.database import EmailAlreadyRegistered, db_service
from app.services.key_custody import KeyCustody, StoredHolderKey, get_key_custody
from app.services.security import MAX_PASSWORD_BYTES, hash_password, verify_password

router = APIRouter()
logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8)

    @field_validator("name", "email")
    @classmethod
    def strip_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("Invalid email")
        return v.lower()

    @field_validator("password")
    @classmethod
    def check_password_bytes(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
        return v


class LoginRequest(BaseModel):
    email: str
    password: str


def _profile(user: dict) -> dict:
    return {
        "id": str(user["id"]),
        "name": user["name"],
        "email": user["email"],
        "did": user["subject_did"],
    }


def build_auth_response(user: dict, message: str) -> dict:
    token, expires_in = create_access_token(str(user["id"]), user["subject_did"])
    # 기존 FE 계약(success/message/data)은 유지하고 토큰은 최상위 필드로 추가한다.
    return {
        "success": True,
        "message": message,
        "data": _profile(user),
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
    }


def issue_user_identity(user_id: str, custody: Optional[KeyCustody]) -> tuple[str, Optional[StoredHolderKey]]:
    """신규 사용자에게 관리형 did:key(+암호화 홀더 키) 또는 전환 기간 임시 DID를 발급한다."""
    if custody is not None:
        # 관리형 지갑: BE가 Ed25519 키를 만들어 암호화 보관하고, 공개키로 진짜 did:key를 만든다.
        holder_key = custody.generate(user_id)
        return holder_key.did, holder_key
    # 마스터 키 미설정(전환 기간): 홀더 키 없이 임시 DID. 신원 자격증명 기능은 쓸 수 없다.
    logger.warning("HOLDER_KEY_ENCRYPTION_KEY 미설정: 홀더 키 없이 임시 DID로 가입시킵니다.")
    return f"did:key:z6MkMock{uuid.uuid4().hex[:12]}", None


@router.post("/register")
def register_user(req: RegisterRequest, custody: Optional[KeyCustody] = Depends(get_key_custody)):
    user_id = str(uuid.uuid4())
    did, holder_key = issue_user_identity(user_id, custody)
    try:
        user = db_service.create_user(
            user_id=user_id, email=req.email, name=req.name, did=did,
            password_hash=hash_password(req.password), holder_key=holder_key,
        )
    except EmailAlreadyRegistered:
        raise HTTPException(status_code=400, detail="Email already registered")
    return build_auth_response(user, "회원가입 완료. 발급된 DID를 프론트엔드에서 보관하세요.")


@router.post("/login")
def login_user(req: LoginRequest):
    user = db_service.get_user_by_email(req.email.strip())
    # 이메일 존재 여부가 드러나지 않도록 실패 사유를 하나로 통일한다.
    # password_hash가 없는 사용자는 비밀번호 로그인 불가.
    if not user or not user["password_hash"] or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return build_auth_response(user, "로그인 성공")


@router.get("/me")
def get_me(principal: Principal = Depends(require_user)):
    user = db_service.get_user_by_id(principal.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found or not verified yet.")
    holder_key = db_service.get_holder_key(principal.user_id)
    oauth_providers = db_service.get_user_oauth_providers(principal.user_id)
    return {
        "success": True,
        "message": "OK",
        "data": _profile(user),
        # managed: BE가 홀더 키를 보관(신원 자격증명 사용 가능) / none: 키 없는 기존 사용자
        "wallet": {
            "custody": "managed" if holder_key else "none",
            "key_type": "Ed25519" if holder_key else None,
        },
        "oauth_providers": oauth_providers,
    }

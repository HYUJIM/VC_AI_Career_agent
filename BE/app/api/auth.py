import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, create_access_token, require_user
from app.core.text import StorableText, ensure_single_line
from app.services.database import EmailAlreadyRegistered, db_service
from app.services.key_custody import KeyCustody, get_key_custody
from app.services.login_throttle import TOO_MANY_ATTEMPTS, login_throttle
from app.services.security import MAX_PASSWORD_BYTES, hash_password_async, verify_password_async

router = APIRouter()
logger = logging.getLogger(__name__)


class RegisterRequest(BaseModel):
    # StorableText: NUL·짝 없는 서로게이트는 DB 단계의 500이 아니라 422로 거절한다.
    name: StorableText = Field(min_length=1, max_length=100)
    email: StorableText = Field(min_length=3, max_length=255)
    # 실제 상한은 아래 72바이트 검사다. 이 상한은 그보다 훨씬 긴 값을 바이트 계산 전에 바로 422로 돌려보낸다.
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("name", "email")
    @classmethod
    def strip_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        # 이름·이메일은 한 줄 텍스트다 (이름은 포트폴리오 본문·기업용 공유 화면에 한 줄로 들어간다).
        # 앞뒤 공백·줄바꿈은 위에서 잘라 내고, 안쪽의 줄바꿈·탭 등 제어 문자와 U+2028·U+2029는 422로 거절한다.
        return ensure_single_line(v)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        if "@" not in v:
            raise ValueError("Invalid email")
        v = v.lower()
        # 위 255자 상한은 소문자로 바꾸기 전 값에 적용된다. 'İ'(U+0130)는 lower()로 두 글자('i' + U+0307)가 되므로
        # 다시 재서 DB 컬럼(VARCHAR(255))을 넘으면 500이 아니라 422로 거절한다.
        if len(v) > 255:
            raise ValueError("Email must be at most 255 characters")
        return v

    @field_validator("password")
    @classmethod
    def check_password_bytes(cls, v: str) -> str:
        if len(v.encode("utf-8")) > MAX_PASSWORD_BYTES:
            raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
        return v


class LoginRequest(BaseModel):
    # 가입 값보다 넉넉한 상한 (가입은 이메일 255자, 비밀번호 72바이트). 넘는 값은 어차피 로그인할 수 없다.
    email: StorableText = Field(max_length=1024)
    password: str = Field(max_length=1024)


def _profile(user: dict) -> dict:
    return {
        "id": str(user["id"]),
        "name": user["name"],
        "email": user["email"],
        "did": user["subject_did"],
    }


def _auth_response(user: dict, message: str) -> dict:
    # 토큰에 지금의 token_version을 넣는다. 로그아웃하면 값이 올라가 이 토큰은 무효가 된다 (app/core/auth.py).
    token, expires_in = create_access_token(str(user["id"]), user["subject_did"], user["token_version"])
    # 기존 FE 계약(success/message/data)은 유지하고 토큰은 최상위 필드로 추가한다.
    return {
        "success": True,
        "message": message,
        "data": _profile(user),
        "access_token": token,
        "token_type": "bearer",
        "expires_in": expires_in,
    }


# 가입·로그인은 async: bcrypt는 전용 제한기(security.py), DB 호출은 run_in_threadpool로 돌려 공용 스레드 풀을 오래 잡지 않는다.
@router.post("/register")
async def register_user(req: RegisterRequest, custody: Optional[KeyCustody] = Depends(get_key_custody)):
    user_id = str(uuid.uuid4())
    if custody is not None:
        # 관리형 지갑: BE가 Ed25519 키를 만들어 암호화 보관하고, 공개키로 진짜 did:key를 만든다.
        holder_key = custody.generate(user_id)
        did = holder_key.did
    else:
        # 마스터 키 미설정(전환 기간): 홀더 키 없이 임시 DID. 신원 자격증명 기능은 쓸 수 없다.
        holder_key = None
        did = f"did:key:z6MkMock{uuid.uuid4().hex[:12]}"
        logger.warning("HOLDER_KEY_ENCRYPTION_KEY 미설정: 홀더 키 없이 임시 DID로 가입시킵니다.")
    password_hash = await hash_password_async(req.password)
    try:
        user = await run_in_threadpool(
            db_service.create_user, user_id=user_id, email=req.email, name=req.name, did=did,
            password_hash=password_hash, holder_key=holder_key,
        )
    except EmailAlreadyRegistered:
        # FE 가입 화면의 '이미 가입된 이메일' 안내가 이 응답에 기대므로 그대로 둔다 (가입 여부가 드러나는 것은 의도된 동작).
        raise HTTPException(status_code=400, detail="Email already registered")
    return _auth_response(user, "회원가입 완료. 발급된 DID를 프론트엔드에서 보관하세요.")


def _reserve_login_attempt(email: str) -> None:
    """이 이메일의 실패 한도를 넘었으면 비밀번호를 확인하지 않고 429 (가입 여부와 관계없이 같음, login_throttle.py)."""
    retry_after = login_throttle.reserve(email)
    if retry_after is not None:
        raise HTTPException(status_code=429, detail=TOO_MANY_ATTEMPTS, headers={"Retry-After": str(retry_after)})


@router.post("/login")
async def login_user(req: LoginRequest):
    email = req.email.strip()
    _reserve_login_attempt(email)
    try:
        user = await run_in_threadpool(db_service.get_user_by_email, email)
    except Exception:
        # 조회가 실패해도(예: DB 연결 수 초과) 이미 실패로 기록한 시도다. 다른 실패처럼 더미 bcrypt 비교를 한 번 하고 같은 오류를
        # 그대로 다시 던진다(응답은 지금처럼 500). 비용 없이 키만 만드는 요청으로 잠긴 키를 밀어내지 못하게 하기 위해서다.
        await verify_password_async(req.password, None)
        raise
    if user and not login_throttle.same_key(email, user["email"]):
        # 실패 기록의 키는 PostgreSQL lower()(libc 정렬)처럼 글자마다 접으므로 보통은 찾은 계정의 키와 같다. DB 정렬이 다르게
        # 접으면(예: ICU) 다른 키의 표기로 같은 계정을 찾을 수 있어, 그때는 저장된 이메일로도 세어 표기를 바꿔 가며 한도를 피하지
        # 못하게 한다.
        if login_throttle.reserve(user["email"]) is not None:
            # 저장된 이메일이 잠겼다. 여기서 429를 주면 이 표기로는 가입된 이메일만 429가 되어 가입 여부가 드러나므로,
            # 비밀번호는 확인하지 않고 미가입 이메일과 같은 401·같은 비용(더미 bcrypt 비교 한 번)으로 답한다.
            await verify_password_async(req.password, None)
            raise HTTPException(status_code=401, detail="Invalid email or password")
    # 이메일 존재 여부가 드러나지 않도록 실패 사유를 하나로 통일하고, 응답 시간도 맞춘다:
    # 사용자가 없거나 password_hash가 없어도(비밀번호 로그인 불가) 같은 비용의 bcrypt 비교를 한 번 한다.
    password_ok = await verify_password_async(req.password, user["password_hash"] if user else None)
    if not user or not password_ok:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    # 성공하면 찾은 계정(저장된 이메일)의 기록만 지운다 (DB가 키와 같게 접으면 입력한 표기의 키도 이 키다). 입력한 표기의 키를
    # 따로 지우지 않는다: DB 정렬이 키와 다르게 접으면 그 키는 다른 계정의 저장된 이메일 키일 수 있어, 자기 계정에 로그인해
    # 남의 실패 기록을 지울 수 있기 때문이다.
    login_throttle.succeeded(user["email"])
    return _auth_response(user, "로그인 성공")


@router.post("/logout", status_code=204)
def logout_user(principal: Principal = Depends(require_user)) -> Response:
    """이 사용자에게 발급한 액세스 토큰을 모두 무효로 만든다 (다른 탭·기기 포함). 다시 쓰려면 새로 로그인한다."""
    db_service.bump_token_version(principal.user_id)
    return Response(status_code=204)


@router.get("/me")
def get_me(principal: Principal = Depends(require_user)):
    user = db_service.get_user_by_id(principal.user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found or not verified yet.")
    holder_key = db_service.get_holder_key(principal.user_id)
    return {
        "success": True,
        "message": "OK",
        "data": _profile(user),
        # managed: BE가 홀더 키를 보관(신원 자격증명 사용 가능) / none: 키 없는 기존 사용자
        "wallet": {
            "custody": "managed" if holder_key else "none",
            "key_type": "Ed25519" if holder_key else None,
        },
    }

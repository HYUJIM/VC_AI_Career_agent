"""JWT 액세스 토큰 발급/검증과 요청 주체(Principal) 판별.

요청 주체는 세 종류다.
- user: `Authorization: Bearer <JWT>` 로그인 사용자. 본인 DID의 데이터만 접근할 수 있다.
- service: `X-Service-Key` 헤더의 내부 서비스(AI). 읽기 API만 허용한다.
- anonymous: 인증 정보가 없는 요청. AUTH_REQUIRED=false(전환 기간)일 때만 허용한다.
"""
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

ALGORITHM = "HS256"

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "service", "anonymous"]
    user_id: Optional[str] = None
    did: Optional[str] = None

    @property
    def is_user(self) -> bool:
        return self.kind == "user"


def create_access_token(user_id: str, did: str) -> tuple[str, int]:
    """(토큰, 만료까지 남은 초)를 돌려준다."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_in = settings.jwt_expires_minutes * 60
    payload = {
        "sub": user_id,
        "did": did,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), expires_in


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode(token: str) -> Principal:
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "did", "exp"]},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token expired")
    except jwt.InvalidTokenError:
        raise _unauthorized("Invalid token")
    if payload.get("type") != "access":
        raise _unauthorized("Invalid token")
    return Principal(kind="user", user_id=str(payload["sub"]), did=str(payload["did"]))


def get_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    x_service_key: Optional[str] = Header(default=None),
) -> Principal:
    if credentials is not None:
        return _decode(credentials.credentials)

    settings = get_settings()
    if x_service_key is not None:
        if settings.service_api_key and hmac.compare_digest(
            x_service_key.encode("utf-8"), settings.service_api_key.encode("utf-8")
        ):
            return Principal(kind="service")
        raise _unauthorized("Invalid service key")

    if settings.auth_required:
        raise _unauthorized("Authentication required")
    return Principal(kind="anonymous")


def require_user(principal: Principal = Depends(get_principal)) -> Principal:
    """로그인 사용자 토큰이 반드시 필요한 API용 (AUTH_REQUIRED와 무관)."""
    if not principal.is_user:
        raise _unauthorized("Authentication required")
    return principal


def ensure_can_read_subject(principal: Principal, did: str) -> None:
    """로그인 사용자는 본인 DID만 읽을 수 있다. 서비스/전환 기간 익명 요청은 통과."""
    if principal.is_user and principal.did != did:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: not your DID")


def ensure_can_write(principal: Principal) -> None:
    """쓰기 API는 서비스 키로 호출할 수 없다."""
    if principal.kind == "service":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Service key cannot call write APIs")

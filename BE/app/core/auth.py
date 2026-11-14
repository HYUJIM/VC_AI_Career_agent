"""JWT 액세스 토큰 발급/검증과 요청 주체(Principal) 판별.

요청 주체는 두 종류다.
- user: `Authorization: Bearer <JWT>` 로그인 사용자. 본인 DID의 데이터만 접근할 수 있다.
- service: `X-Service-Key` 헤더의 내부 서비스 (선택·현재 쓰는 곳 없음: AI 서비스는 BE를 호출하지 않는다). 읽기 API만 허용한다.
인증 정보가 없는 요청은 APP_ENV와 관계없이 항상 401이다 (공개 API인 회원가입·로그인·/health·공유 링크 조회는 이 의존성을
쓰지 않는다). 예전 AUTH_REQUIRED=false(전환 기간)의 익명 요청 허용은 없앴다.

사용자 토큰은 서명·만료와 함께 `ver` 클레임이 users.token_version과 같아야 받아들인다.
로그아웃(POST /api/v1/users/logout)하면 token_version이 올라가 그 사용자에게 발급한 토큰이 모두 무효가 된다.
"""
import hmac
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.services.database import db_service

ALGORITHM = "HS256"

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    kind: Literal["user", "service"]
    user_id: Optional[str] = None
    did: Optional[str] = None

    @property
    def is_user(self) -> bool:
        return self.kind == "user"


def create_access_token(user_id: str, did: str, token_version: int = 0) -> tuple[str, int]:
    """(토큰, 만료까지 남은 초)를 돌려준다. token_version은 발급 시점의 users.token_version."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_in = settings.jwt_expires_minutes * 60
    payload = {
        "sub": user_id,
        "did": did,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "type": "access",
        "ver": token_version,
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
    try:
        user_id = str(uuid.UUID(str(payload["sub"])))
    except ValueError:
        raise _unauthorized("Invalid token")
    # 로그아웃으로 무효가 된 토큰과 없는 사용자(None)의 토큰은 거절한다. ver가 없는 예전 토큰은 0으로 본다.
    # bool은 int의 하위 클래스라 isinstance로는 True가 1로 통과하므로 type으로 정확히 비교한다.
    version = payload.get("ver", 0)
    if type(version) is not int or db_service.get_token_version(user_id) != version:
        raise _unauthorized("Invalid token")
    return Principal(kind="user", user_id=user_id, did=str(payload["did"]))


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

    # 토큰도 서비스 키도 없으면 설정과 관계없이 거절한다.
    raise _unauthorized("Authentication required")


def require_user(principal: Principal = Depends(get_principal)) -> Principal:
    """로그인 사용자 토큰이 반드시 필요한 API용 (서비스 키는 401)."""
    if not principal.is_user:
        raise _unauthorized("Authentication required")
    return principal


def ensure_can_read_subject(principal: Principal, did: str) -> None:
    """로그인 사용자는 본인 DID만 읽을 수 있다. 서비스 키만 모든 DID를 읽는다."""
    if principal.kind != "service" and principal.did != did:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden: not your DID")


def ensure_can_write(principal: Principal) -> None:
    """쓰기 API는 서비스 키로 호출할 수 없다."""
    if principal.kind == "service":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Service key cannot call write APIs")

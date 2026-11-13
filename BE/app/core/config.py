"""환경변수 기반 설정. 모든 모듈은 os.getenv 대신 get_settings()를 쓴다."""
import logging
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # 발급·검증 인프라 접속 정보는 협력사 제공 자료이므로 코드에 기본값을 두지 않고 비공개 .env로만 받는다.
    dxworks_api_host: str
    dxworks_api_key: str
    dxworks_timeout_seconds: float
    # 모의 발급 요청에 templateId가 없을 때 쓰는 기본 배지 템플릿 ID
    dxworks_badge_template_id: str
    jwt_secret: str
    jwt_expires_minutes: int
    # True면 사용자 토큰(또는 서비스 키) 없는 요청을 401로 거절한다.
    # FE/AI가 토큰을 보내도록 바뀌기 전까지는 false로 운영한다 (README "인증" 참고).
    auth_required: bool
    # AI 등 내부 서비스가 X-Service-Key 헤더로 읽기 API를 호출할 때 쓰는 키. 비우면 서비스 키 인증을 끈다.
    service_api_key: str
    cors_origins: list[str]
    mock_issuer_enabled: bool
    # 관리형 홀더 키 암호화용 마스터 키(32바이트). None이면 키를 만들지 않고 가짜 DID로 가입시킨다(전환 기간).
    holder_key_encryption_key: Optional[bytes]
    # Google OAuth 2.0 / OIDC 설정
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str
    google_allowed_redirect_uris: list[str]
    google_oauth_timeout_seconds: float


@lru_cache
def get_settings() -> Settings:
    jwt_secret = os.getenv("JWT_SECRET", "").strip()
    if not jwt_secret:
        # 개발 편의용. 서버가 재시작되면 이전 토큰은 모두 무효가 된다. 운영에서는 반드시 JWT_SECRET을 설정한다.
        jwt_secret = secrets.token_urlsafe(32)
        logger.warning("JWT_SECRET이 설정되지 않아 임시 키를 생성했습니다. 서버 재시작 시 기존 토큰이 무효화됩니다.")

    raw_master_key = os.getenv("HOLDER_KEY_ENCRYPTION_KEY", "").strip()
    holder_key = None
    if raw_master_key:
        from app.services.key_custody import parse_master_key

        holder_key = parse_master_key(raw_master_key)  # 형식이 틀리면 서버 시작 시 바로 실패
    else:
        logger.warning("HOLDER_KEY_ENCRYPTION_KEY가 설정되지 않아 새 가입자에게 홀더 키 없이 임시 DID를 발급합니다.")

    google_redirect_uri = os.getenv(
        "GOOGLE_REDIRECT_URI", "http://localhost:5173/auth/google/callback"
    ).strip()
    raw_allowed_redirects = _env_list("GOOGLE_ALLOWED_REDIRECT_URIS", "")
    allowed_redirects = list(
        dict.fromkeys(([google_redirect_uri] if google_redirect_uri else []) + raw_allowed_redirects)
    )

    settings = Settings(
        dxworks_api_host=os.getenv("DXWORKS_API_HOST", "").strip().rstrip("/"),
        dxworks_api_key=os.getenv("DXWORKS_API_KEY", "").strip(),
        dxworks_timeout_seconds=float(os.getenv("DXWORKS_TIMEOUT_SECONDS", "15")),
        dxworks_badge_template_id=os.getenv("DXWORKS_BADGE_TEMPLATE_ID", "").strip(),
        jwt_secret=jwt_secret,
        jwt_expires_minutes=int(os.getenv("JWT_EXPIRES_MINUTES", "1440")),
        auth_required=_env_bool("AUTH_REQUIRED", False),
        service_api_key=os.getenv("SERVICE_API_KEY", "").strip(),
        cors_origins=_env_list("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"),
        mock_issuer_enabled=_env_bool("MOCK_ISSUER_ENABLED", True),
        holder_key_encryption_key=holder_key,
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", "").strip(),
        google_redirect_uri=google_redirect_uri,
        google_allowed_redirect_uris=allowed_redirects,
        google_oauth_timeout_seconds=float(os.getenv("GOOGLE_OAUTH_TIMEOUT_SECONDS", "10")),
    )
    if not settings.auth_required:
        logger.warning("AUTH_REQUIRED=false: 토큰 없는 요청도 허용합니다 (FE/AI 토큰 연동 전 전환 기간용).")
    return settings

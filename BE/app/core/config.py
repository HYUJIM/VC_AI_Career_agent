"""환경변수 기반 설정. 모든 모듈은 os.getenv 대신 get_settings()를 쓴다."""
import logging
import os
import secrets
from dataclasses import dataclass
from functools import lru_cache

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


@lru_cache
def get_settings() -> Settings:
    jwt_secret = os.getenv("JWT_SECRET", "").strip()
    if not jwt_secret:
        # 개발 편의용. 서버가 재시작되면 이전 토큰은 모두 무효가 된다. 운영에서는 반드시 JWT_SECRET을 설정한다.
        jwt_secret = secrets.token_urlsafe(32)
        logger.warning("JWT_SECRET이 설정되지 않아 임시 키를 생성했습니다. 서버 재시작 시 기존 토큰이 무효화됩니다.")

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
    )
    if not settings.auth_required:
        logger.warning("AUTH_REQUIRED=false: 토큰 없는 요청도 허용합니다 (FE/AI 토큰 연동 전 전환 기간용).")
    return settings

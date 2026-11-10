"""환경변수 기반 설정. 모든 모듈은 os.getenv 대신 get_settings()를 쓴다."""
import logging
import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations
from typing import Optional

logger = logging.getLogger(__name__)

APP_ENVS = ("development", "production")
MIN_JWT_SECRET_BYTES = 32  # HS256 서명 키 최소 길이 (RFC 7518 3.2)
# 액세스 토큰 수명(분)의 상한이자 기본값. 로그아웃하지 않고 떠난 세션(공용 PC 등)이 살아 있는 시간의 상한이다.
MAX_JWT_EXPIRES_MINUTES = 1440
# 모의 발급의 사용자별 24시간 발급 한도 기본값 (MOCK_ISSUER_DAILY_LIMIT을 비우면 이 값)
DEFAULT_MOCK_ISSUER_DAILY_LIMIT = 10
# production 비밀값의 서로 다른 바이트 수 하한. "x"*32·0x01*32 같은 자리표시 값을 거부한다
# (README대로 secrets·os.urandom으로 만든 32바이트 이상 값은 사실상 항상 넘는다).
MIN_SECRET_DISTINCT_BYTES = 8
# 이 공개 저장소에 들어 있는 테스트 비밀값. 누구나 아는 값이므로 production에서는 길이·형식이 맞아도 받지 않는다.
# 테스트에 고정 비밀값을 새로 넣으면 여기에도 더한다 (tests/test_config.py가 conftest 값으로 확인한다).
PUBLISHED_SECRETS = frozenset({
    b"test-secret-for-pytest-only-0123456789",  # tests/conftest.py JWT_SECRET
    b"pytest-only-jwt-secret-0123456789abcdef",  # tests/test_config.py (예전 값)
    b"test-service-key",  # tests/conftest.py·test_api.py·test_features.py SERVICE_API_KEY
    b"\x01" * 32,  # tests/conftest.py HOLDER_KEY_ENCRYPTION_KEY
    b"\x02" * 32,  # tests/test_config.py (예전 값)
    bytes(range(32)),  # tests/test_key_custody.py MASTER
})
# 로컬 개발 DB 비밀번호 (docker-compose.yml·.env.example·테스트·CI에 공개된 값). production에서는 받지 않는다.
DEV_DB_PASSWORD = "password"
_RANDOM_NOT_PUBLISHED = "random, not a test/example value from this repository"
_DB_PASSWORD_RULE = "DB_PASSWORD (not empty and not the local development default)"


def _weak_secret(value: bytes) -> bool:
    """이 저장소에 공개된 테스트 값이거나, 서로 다른 바이트가 너무 적은 자리표시 값이면 True."""
    return value in PUBLISHED_SECRETS or len(set(value)) < MIN_SECRET_DISTINCT_BYTES


def _app_env() -> str:
    app_env = os.getenv("APP_ENV", "").strip().lower() or "development"
    if app_env not in APP_ENVS:
        raise RuntimeError(f"APP_ENV must be one of: {', '.join(APP_ENVS)} (empty means development)")
    return app_env


def db_password() -> str:
    """DB 접속 비밀번호. get_db_connection(앱 요청·마이그레이션 실행기 공통)이 접속할 때마다 읽는다.

    development는 지금처럼 없으면 로컬 기본값을 쓴다. production은 비었거나 로컬 기본값이면 접속하지 않고
    RuntimeError를 낸다 (마이그레이션 태스크는 JWT_SECRET 등 앱 비밀값 없이 돌므로 get_settings()와 따로 검사한다).
    """
    password = os.getenv("DB_PASSWORD", DEV_DB_PASSWORD)
    if _app_env() == "production" and password.strip() in ("", DEV_DB_PASSWORD):
        raise RuntimeError(f"APP_ENV=production requires {_DB_PASSWORD_RULE}")
    return password


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_int(name: str, default: int, *, minimum: int, maximum: Optional[int] = None) -> int:
    """정수 환경변수 (없거나 비우면 default). 정수가 아니거나 범위를 벗어나면 서버 시작을 거부한다."""
    raw = os.getenv(name, "").strip()
    allowed = f"between {minimum} and {maximum}" if maximum is not None else f">= {minimum}"
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = None
    if value is None or value < minimum or (maximum is not None and value > maximum):
        raise RuntimeError(f"{name} must be an integer {allowed}")
    return value


@dataclass(frozen=True)
class Settings:
    # 비밀값 필드는 field(repr=False)로 선언한다: 설정 객체가 로그·오류 보고·디버거 출력(repr/str)에 찍혀도 값이 나오지 않는다.
    # 코드는 지금처럼 속성으로 읽는다. 새 비밀값도 같은 방식으로 추가한다 (tests/test_config.py가 확인).
    # dataclasses.asdict()·vars()에는 값이 그대로 들어가므로 그 결과는 로그에 쓰지 않는다.
    # APP_ENV (development | production, 비우면 development).
    # development는 비밀값이 없을 때 임시 값으로 대신하고, production은 비밀값이 없거나 공개된 값이면 서버 시작을 거부한다.
    app_env: str
    # 발급·검증 인프라 접속 정보는 협력사 제공 자료이므로 코드에 기본값을 두지 않고 비공개 .env로만 받는다.
    dxworks_api_host: str
    dxworks_api_key: str = field(repr=False)
    dxworks_timeout_seconds: float
    # 모의 발급 요청에 templateId가 없을 때 쓰는 기본 배지 템플릿 ID
    dxworks_badge_template_id: str
    jwt_secret: str = field(repr=False)
    # 액세스 토큰 수명(분). 1~1440만 받는다 (기본 1440).
    jwt_expires_minutes: int
    # (선택·현재 쓰는 곳 없음) 내부 서비스가 X-Service-Key 헤더로 읽기 API를 호출할 때 쓰는 키. 비우면 서비스 키 인증을 끈다.
    service_api_key: str = field(repr=False)
    cors_origins: list[str]
    # 모의 발급 API 사용 여부. 비우면 development는 켜고 production은 끈다 (운영에서는 명시적으로 켤 때만).
    mock_issuer_enabled: bool
    # 모의 발급에 쓸 수 있는 템플릿 ID: DXWORKS_BADGE_TEMPLATE_ID + MOCK_ISSUER_TEMPLATE_IDS. 그 밖의 templateId는 403.
    mock_issuer_template_ids: frozenset[str]
    # 사용자별 24시간 모의 발급 한도 (회사 DXWorks API 키 사용량 보호, 1 이상)
    mock_issuer_daily_limit: int
    # 관리형 홀더 키 암호화용 마스터 키(32바이트). None이면 키를 만들지 않고 가짜 DID로 가입시킨다(전환 기간).
    holder_key_encryption_key: Optional[bytes] = field(repr=False)
    # AI 서비스(ai/, `uvicorn src.server.app:app`). 직무 비교·커리큘럼 계산을 맡는다.
    ai_service_url: str
    # AI의 AI_SERVICE_TOKEN과 같은 32바이트 이상 공유 비밀. 비면 AI 기능 요청은 503.
    ai_service_token: str = field(repr=False)
    # 루프백이 아닌 http:// AI 주소는 이 값이 true일 때만 허용한다 (Docker 내부망 등).
    ai_service_allow_http: bool
    ai_timeout_seconds: float
    ai_generation_timeout_seconds: float
    # 공고·학습자료를 반입할 수 있는 연구자 사용자 UUID 목록.
    researcher_user_ids: frozenset[str]
    # 커리큘럼 LLM 모드를 쓸 수 있는 사용자 UUID 목록 (LLM 설정 자체는 AI 서비스 .env).
    curriculum_llm_user_ids: frozenset[str]


@lru_cache
def get_settings() -> Settings:
    app_env = _app_env()
    production = app_env == "production"
    # production에서 빠졌거나 쓸 수 없는 값(공개된 테스트 값·자리표시 값·로컬 기본값).
    # 오류 메시지에는 변수 이름만 넣고 값은 절대 넣지 않는다.
    missing: list[str] = []

    jwt_secret = os.getenv("JWT_SECRET", "").strip()
    if production:
        # 작업(태스크)·워커마다 임시 키를 만들면 서로의 토큰을 거절하므로 운영에서는 대신하지 않는다.
        if len(jwt_secret.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
            missing.append(f"JWT_SECRET (>= {MIN_JWT_SECRET_BYTES} bytes)")
        elif _weak_secret(jwt_secret.encode("utf-8")):
            missing.append(f"JWT_SECRET ({_RANDOM_NOT_PUBLISHED})")
    elif not jwt_secret:
        # 개발 편의용. 서버가 재시작되면 이전 토큰은 모두 무효가 된다. 운영에서는 반드시 JWT_SECRET을 설정한다.
        jwt_secret = secrets.token_urlsafe(32)
        logger.warning("JWT_SECRET이 설정되지 않아 임시 키를 생성했습니다. 서버 재시작 시 기존 토큰이 무효화됩니다.")

    raw_master_key = os.getenv("HOLDER_KEY_ENCRYPTION_KEY", "").strip()
    holder_key = None
    if raw_master_key:
        from app.services.key_custody import parse_master_key

        holder_key = parse_master_key(raw_master_key)  # 형식이 틀리면 서버 시작 시 바로 실패
        # 디코딩한 바이트로 비교하므로 base64 표기(표준·URL-safe·패딩 없음)가 달라도 같은 키로 본다.
        if production and _weak_secret(holder_key):
            missing.append(f"HOLDER_KEY_ENCRYPTION_KEY ({_RANDOM_NOT_PUBLISHED})")
    elif production:
        missing.append("HOLDER_KEY_ENCRYPTION_KEY")
    else:
        logger.warning("HOLDER_KEY_ENCRYPTION_KEY가 설정되지 않아 새 가입자에게 홀더 키 없이 임시 DID를 발급합니다.")

    service_api_key = os.getenv("SERVICE_API_KEY", "").strip()
    if production and service_api_key and (
        len(service_api_key.encode("utf-8")) < MIN_JWT_SECRET_BYTES or _weak_secret(service_api_key.encode("utf-8"))
    ):
        # 모든 DID를 읽는 키이므로 JWT_SECRET과 같은 기준을 둔다 (비우면 서비스 키 인증을 끈다).
        missing.append(f"SERVICE_API_KEY (empty, or >= {MIN_JWT_SECRET_BYTES} bytes {_RANDOM_NOT_PUBLISHED})")

    if production:
        # 비밀값은 용도마다 따로 만든다: DXWORKS_API_KEY(협력사로 보낸다)나 AI_SERVICE_TOKEN(AI 서비스도 가진다)을 아는 쪽이
        # 같은 값으로 사용자 토큰을 만들거나(JWT_SECRET) 홀더 키를 복호화하거나(HOLDER_KEY_ENCRYPTION_KEY) 모든 DID를 읽지
        # (SERVICE_API_KEY) 못하게 다섯 값의 모든 쌍을 비교한다. 비운 값은 비교하지 않는다. 바꾸기 어려운 값부터 두고
        # (협력사가 발급한 키, 바꾸면 기존 홀더 키를 복호화할 수 없는 마스터 키, 바꾸면 모두 로그아웃되는 JWT_SECRET 순),
        # 같은 쌍은 뒤쪽(바꿀 값)의 이름을 앞에 써서 알린다.
        values = {"DXWORKS_API_KEY": os.getenv("DXWORKS_API_KEY", "").strip(),
                  "HOLDER_KEY_ENCRYPTION_KEY": raw_master_key, "JWT_SECRET": jwt_secret,
                  "SERVICE_API_KEY": service_api_key, "AI_SERVICE_TOKEN": os.getenv("AI_SERVICE_TOKEN", "").strip()}
        for other, name in combinations(values, 2):
            if values[name] and values[name] == values[other]:
                missing.append(f"{name} (not the same value as {other})")

    if production:
        # 운영에서는 DB 접속값을 개발용 기본값(localhost, postgres/password)으로 대신하지 않는다 (database.get_db_connection).
        missing += [name for name in ("DB_HOST", "DB_NAME", "DB_USER") if not os.getenv(name, "").strip()]
        if os.getenv("DB_PASSWORD", "").strip() in ("", DEV_DB_PASSWORD):
            # 시작할 때 바로 알린다. 접속할 때도 db_password()가 다시 막는다 (마이그레이션 실행기 포함).
            missing.append(_DB_PASSWORD_RULE)

    if missing:
        raise RuntimeError(
            "APP_ENV=production requires " + ", ".join(missing)
            + "; set the same values on every task/worker (e.g. AWS Secrets Manager or SSM)"
        )

    jwt_expires_minutes = _env_int("JWT_EXPIRES_MINUTES", MAX_JWT_EXPIRES_MINUTES,
                                   minimum=1, maximum=MAX_JWT_EXPIRES_MINUTES)
    mock_issuer_daily_limit = _env_int("MOCK_ISSUER_DAILY_LIMIT", DEFAULT_MOCK_ISSUER_DAILY_LIMIT, minimum=1)
    badge_template_id = os.getenv("DXWORKS_BADGE_TEMPLATE_ID", "").strip()
    settings = Settings(
        app_env=app_env,
        dxworks_api_host=os.getenv("DXWORKS_API_HOST", "").strip().rstrip("/"),
        dxworks_api_key=os.getenv("DXWORKS_API_KEY", "").strip(),
        dxworks_timeout_seconds=float(os.getenv("DXWORKS_TIMEOUT_SECONDS", "15")),
        dxworks_badge_template_id=badge_template_id,
        jwt_secret=jwt_secret,
        jwt_expires_minutes=jwt_expires_minutes,
        service_api_key=service_api_key,
        cors_origins=_env_list("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"),
        mock_issuer_enabled=_env_bool("MOCK_ISSUER_ENABLED", not production),
        mock_issuer_template_ids=frozenset([badge_template_id, *_env_list("MOCK_ISSUER_TEMPLATE_IDS", "")]) - {""},
        mock_issuer_daily_limit=mock_issuer_daily_limit,
        holder_key_encryption_key=holder_key,
        ai_service_url=os.getenv("AI_SERVICE_URL", "http://127.0.0.1:8100").strip().rstrip("/"),
        ai_service_token=os.getenv("AI_SERVICE_TOKEN", ""),
        ai_service_allow_http=_env_bool("AI_SERVICE_ALLOW_HTTP", False),
        ai_timeout_seconds=float(os.getenv("AI_TIMEOUT_SECONDS", "30")),
        ai_generation_timeout_seconds=float(os.getenv("AI_GENERATION_TIMEOUT_SECONDS", "90")),
        researcher_user_ids=frozenset(_env_list("RESEARCHER_USER_IDS", "")),
        curriculum_llm_user_ids=frozenset(_env_list("CURRICULUM_LLM_USER_IDS", "")),
    )
    if production and settings.mock_issuer_enabled:
        logger.warning(
            "MOCK_ISSUER_ENABLED=true (production): 로그인 사용자가 허용된 템플릿으로 본인 DID에 배지를 발급할 수 있습니다"
            " (사용자별 24시간 %d건).", settings.mock_issuer_daily_limit,
        )
    if not _env_bool("AUTH_REQUIRED", True):
        # 예전 전환 기간 설정이 남은 .env·작업 정의. 인증을 끄는 설정은 없어졌다 (README "인증").
        logger.warning("AUTH_REQUIRED=false 설정은 더 이상 쓰지 않습니다: 토큰 없는 요청은 이 값과 관계없이 항상 401로 거절합니다.")
    return settings

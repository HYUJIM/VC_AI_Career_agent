"""환경변수 기반 설정. 앱 모듈은 os.getenv 대신 get_settings()를 쓴다.

앱 비밀값 없이 도는 진입점(마이그레이션 실행기·시장 통계 배치)도 쓰는 값은 get_settings()와 따로 읽는다: DB 접속값
(app/services/database.py의 DB_*, 아래 db_password·db_tls_options·db_max_connections)과 로그 수준(log_level).
"""
import logging
import math
import os
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations
from typing import Optional
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

APP_ENVS = ("development", "production")
# LOG_LEVEL이 받는 수준 이름 (log_level). 비우면 INFO.
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
MIN_JWT_SECRET_BYTES = 32  # HS256 서명 키 최소 길이 (RFC 7518 3.2)
# 액세스 토큰 수명(분)의 상한이자 기본값. 로그아웃하지 않고 떠난 세션(공용 PC 등)이 살아 있는 시간의 상한이다.
MAX_JWT_EXPIRES_MINUTES = 1440
# 모의 발급의 사용자별 24시간 발급 한도 기본값 (MOCK_ISSUER_DAILY_LIMIT을 비우면 이 값)
DEFAULT_MOCK_ISSUER_DAILY_LIMIT = 10
# 모의 발급의 모든 사용자를 합친 24시간 발급 한도 기본값 (MOCK_ISSUER_GLOBAL_DAILY_LIMIT을 비우면 이 값)
DEFAULT_MOCK_ISSUER_GLOBAL_DAILY_LIMIT = 200
# 프로세스(BE 컨테이너) 하나가 동시에 여는 앱 DB 연결 수의 상한 기본값 (DB_MAX_CONNECTIONS를 비우면 이 값)
DEFAULT_DB_MAX_CONNECTIONS = 20
# 프로세스 하나가 AI 서비스에 동시에 보내는 공고·학습자료 묶음 요청 수의 상한 기본값 (AI_MAX_CONCURRENCY를 비우면 이 값)
DEFAULT_AI_MAX_CONCURRENCY = 4
# LLM 모드 학습 계획의 사용자별 24시간 한도 기본값 (CURRICULUM_LLM_DAILY_LIMIT을 비우면 이 값)
DEFAULT_CURRICULUM_LLM_DAILY_LIMIT = 20
# LLM 모드 학습 계획의 서비스 전체(모든 계정 합계) 24시간 한도 기본값 (CURRICULUM_LLM_GLOBAL_DAILY_LIMIT을 비우면 이 값).
# CURRICULUM_LLM_USER_IDS=*(가입한 모든 계정)일 때 계정을 여러 개 만들어 사용자별 한도를 우회하는 것을 막는다.
DEFAULT_CURRICULUM_LLM_GLOBAL_DAILY_LIMIT = 200
# CURRICULUM_LLM_USER_IDS에 이 값이 있으면 로그인한 모든 계정이 LLM 모드를 쓸 수 있다.
ALL_USERS = "*"
# production 비밀값의 서로 다른 바이트 수 하한. "x"*32·0x01*32 같은 자리표시 값을 거부한다
# (docs/GETTING_STARTED.md 2번대로 secrets·os.urandom으로 만든 32바이트 이상 값은 사실상 항상 넘는다).
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
# production DB 연결의 TLS 규칙 (libpq 변수 PGSSLMODE·PGSSLROOTCERT를 앱이 읽어 검사한다. 넘기는 방법은 db_tls_options).
# 운영 postgres(deploy/postgres/pg_hba.conf의 hostssl)·RDS(rds.force_ssl=1)는 TLS를 강제해 암호화는 되지만, libpq 기본값(prefer)·
# require는 서버 인증서와 호스트 이름을 확인하지 않는다. 그래서 production은 인증서를 확인하는 두 값만 받고, 확인에 쓰는 CA 파일
# (운영 compose는 /run/vc-db-tls/ca.crt, 이미지 기본값은 RDS용 /app/certs/rds-global-bundle.pem, BE/certs/README.md)을 읽을 수
# 있어야 한다. 값은 앞뒤 공백만 떼고 libpq가 받는 글자 그대로 비교한다 (대소문자를 바꾸지 않는다: libpq는 `VERIFY-FULL`을 받지 않는다).
VERIFYING_SSLMODES = ("verify-full", "verify-ca")
_PGSSLMODE_RULE = "PGSSLMODE (verify-full or verify-ca in production)"
_PGSSLROOTCERT_RULE = "PGSSLROOTCERT (readable CA bundle file)"
# PGSSLMODE를 비워 둔 줄(`PGSSLMODE=`)은 없는 것으로 보고 이 libpq 기본값을 넘긴다. 빈 값을 libpq에 그대로 두면
# 모든 접속이 `invalid sslmode value: ""`로 실패한다 (서버는 뜨고 /health도 200인데 DB를 쓰는 요청마다 500).
LIBPQ_DEFAULT_SSLMODE = "prefer"


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


def _readable_file(path: str) -> bool:
    """path가 읽을 수 있는 일반 파일인지 (비었거나 없거나 디렉터리이거나 읽기 권한이 없으면 False)."""
    if not path or not os.path.isfile(path):
        return False
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def _db_tls_problems() -> list[str]:
    """production DB 연결의 TLS 설정 문제 (변수 이름만, 값은 넣지 않는다). get_settings()와 db_tls_options()가 같이 쓴다.

    PGSSLROOTCERT는 libpq가 환경변수에서 읽는 값 그대로(공백을 떼지 않고) 파일인지 본다 (db_tls_options 참고).
    """
    problems = []
    if os.getenv("PGSSLMODE", "").strip() not in VERIFYING_SSLMODES:
        problems.append(_PGSSLMODE_RULE)
    if not _readable_file(os.getenv("PGSSLROOTCERT", "")):
        problems.append(_PGSSLROOTCERT_RULE)
    return problems


def db_tls_options() -> dict[str, str]:
    """libpq에 넘기는 TLS 접속 인자. get_db_connection(앱 요청·시작 점검·마이그레이션 실행기·시장 통계 배치 공통)이
    접속할 때마다 읽는다 (접속 인자가 libpq 환경변수보다 우선한다).

    - sslmode: 앞뒤 공백을 뗀 PGSSLMODE. 비워 둔 값(공백만 있는 값 포함)은 없는 것으로 보고 libpq 기본값(prefer)을 넘긴다.
    - sslrootcert: 지금처럼 libpq가 PGSSLROOTCERT 환경변수를 직접 읽는다 (접속 인자로 넘기지 않는다: 한글처럼 ASCII가 아닌
      글자가 든 경로는 macOS의 UTF-8 로캘에서 libpq가 접속 문자열을 잘못 나눠 접속이 실패한다). 공백만 있는 값만 빈 경로로
      덮는다 (libpq는 빈 경로를 설정하지 않은 것으로 본다).
    - development는 그 밖에 검사하지 않는다 (로컬 Compose·테스트 DB는 TLS가 없어 PGSSLMODE를 넣지 않으면 지금처럼 prefer).
    - production은 db_password()처럼 규칙(verify-full·verify-ca + 읽을 수 있는 CA 묶음 파일)에 맞지 않으면 접속하지 않고
      RuntimeError를 낸다 (변수 이름만). 서버는 시작할 때 get_settings()가 같은 규칙으로 먼저 거부한다.
    """
    if _app_env() == "production":
        problems = _db_tls_problems()
        if problems:
            raise RuntimeError("APP_ENV=production requires " + ", ".join(problems))
    options = {"sslmode": os.getenv("PGSSLMODE", "").strip() or LIBPQ_DEFAULT_SSLMODE}
    root_cert = os.getenv("PGSSLROOTCERT")
    if root_cert is not None and not root_cert.strip():
        options["sslrootcert"] = ""
    return options


def log_level() -> str:
    """LOG_LEVEL (없거나 비우면 INFO). 서버(main.py)·마이그레이션 실행기·시장 통계 배치가 같은 규칙으로 읽는다.

    `LOG_LEVEL=`처럼 비워 둔 줄(.env·작업 정의)이 logging의 'Unknown level' traceback으로 시작을 막지 않게 한다.
    대소문자는 가리지 않고 LOG_LEVELS의 다섯 이름만 받는다. 그 밖의 값(`verbose`, 숫자, logging만 아는 `NOTSET`·`WARN`·
    `FATAL` 등)은 시작할 때 RuntimeError로 알린다: 서버·마이그레이션 실행기는 그 한 줄과 종료 코드 1로 끝나고, 시장 통계
    배치는 인자 오류처럼 한 줄과 종료 코드 2로 끝난다. 메시지에는 변수 이름과 받는 값만 넣고 넣은 값은 싣지 않는다.
    """
    level = os.getenv("LOG_LEVEL", "").strip().upper() or "INFO"
    if level not in LOG_LEVELS:
        raise RuntimeError(f"LOG_LEVEL must be one of: {', '.join(LOG_LEVELS)} (empty means INFO)")
    return level


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _is_plain_origin(value: str) -> bool:
    """CORS 출처 한 항목이 scheme://host[:port] 꼴인지. `*`·`null`, 경로나 끝의 `/`·쿼리·사용자 정보가 붙은 값은 아니다.

    브라우저가 보내는 Origin과 글자 그대로 비교하므로(CORSMiddleware) 이런 값은 모든 출처를 허용하거나(`*`) 어느 출처와도
    맞지 않는다 (끝에 `/`가 붙은 주소는 조용히 FE를 막는다).
    """
    try:
        parts = urlsplit(value)
        parts.port  # 포트가 숫자가 아니면 ValueError
    except ValueError:
        return False
    return (parts.scheme in ("http", "https") and bool(parts.hostname) and parts.username is None
            and not (parts.path or parts.query or parts.fragment) and not value.endswith(("?", "#"))
            and "*" not in value and not any(ch.isspace() for ch in value))


def _is_https_url(value: str) -> bool:
    """https 주소인지 (OAuth 콜백 주소 검사). 글자 비교가 아니라 주소로 읽어서 본다: 스킴은 대소문자를 가리지 않고
    (`HTTPS://`), 호스트가 있어야 한다 (`https://`만 있거나 `https:///path`인 값은 아니다)."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.hostname)


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


def db_max_connections() -> int:
    """프로세스 하나가 동시에 여는 앱 DB 연결 수의 상한 (DB_MAX_CONNECTIONS, 2 이상). database.app_db_connection이 읽는다.

    db_password()처럼 get_settings()와 따로 읽는다: 시장 통계 배치처럼 앱 비밀값(JWT_SECRET 등) 없이 도는 프로세스도 같은
    DB 접근자를 쓴다. 서버는 시작할 때 get_settings()가 같은 값을 검사하므로 잘못된 값이면 뜨지 않는다.
    1이면 모든 DB 호출이 한 줄로 서므로 2 이상만 받는다.
    """
    return _env_int("DB_MAX_CONNECTIONS", DEFAULT_DB_MAX_CONNECTIONS, minimum=2)


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    """실수 환경변수 (없거나 비우면 default). 숫자가 아니거나 NaN·무한대이거나 범위를 벗어나면 서버 시작을 거부한다."""
    raw = os.getenv(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        value = None
    if value is None or not math.isfinite(value) or value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be a number between {minimum:g} and {maximum:g}")
    return value


def _oauth_timeout_seconds() -> float:
    """OAuth 제공자(Google·GitHub·Naver) 호출 제한 시간(초, 1~120, 기본 10). 세 제공자가 같이 쓴다.

    OAUTH_TIMEOUT_SECONDS가 없거나 비었으면 예전 이름 GOOGLE_OAUTH_TIMEOUT_SECONDS를 읽는다 (예전 .env·작업 정의도 그대로
    뜨게). 오류 메시지에는 실제로 읽은 변수 이름을 넣는다.
    """
    name = "OAUTH_TIMEOUT_SECONDS" if os.getenv("OAUTH_TIMEOUT_SECONDS", "").strip() else "GOOGLE_OAUTH_TIMEOUT_SECONDS"
    return _env_float(name, 10.0, minimum=1, maximum=120)


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
    # 신원 VP 검증(POST /api/v1/verify type=identity) 사용 여부. 이 제품의 화면은 쓰지 않으므로 기본은 끈다 (꺼져 있으면 400).
    identity_verification_enabled: bool
    jwt_secret: str = field(repr=False)
    # 액세스 토큰 수명(분). 1~1440만 받는다 (기본 1440).
    jwt_expires_minutes: int
    # (선택·현재 쓰는 곳 없음) 내부 서비스가 X-Service-Key 헤더로 읽기 API를 호출할 때 쓰는 키. 비우면 서비스 키 인증을 끈다.
    service_api_key: str = field(repr=False)
    # CORS 허용 출처. 변수가 없으면 development는 FE 개발 서버 주소, production은 빈 목록(다른 출처 없음)이다.
    cors_origins: list[str]
    # 모의 발급 API 사용 여부. 비우면 development는 켜고 production은 끈다 (운영에서는 명시적으로 켤 때만).
    mock_issuer_enabled: bool
    # 모의 발급에 쓸 수 있는 템플릿 ID: DXWORKS_BADGE_TEMPLATE_ID + MOCK_ISSUER_TEMPLATE_IDS. 그 밖의 templateId는 403.
    mock_issuer_template_ids: frozenset[str]
    # 사용자별 24시간 모의 발급 한도 (회사 DXWorks API 키 사용량 보호, 1 이상)
    mock_issuer_daily_limit: int
    # 모든 사용자를 합친 24시간 모의 발급 한도 (계정을 여러 개 만들어 사용자별 한도를 곱하지 못하게, 1 이상)
    mock_issuer_global_daily_limit: int
    # 관리형 홀더 키 암호화용 마스터 키(32바이트). None이면 키를 만들지 않고 가짜 DID로 가입시킨다(전환 기간).
    holder_key_encryption_key: Optional[bytes] = field(repr=False)
    # Google OAuth 2.0 / OIDC 설정. production에서 켜려면 ID·시크릿을 함께 넣고 콜백 주소(https)를 명시해야 한다.
    google_client_id: str
    google_client_secret: str = field(repr=False)
    # 기본 콜백 주소. 변수가 없으면 development만 FE 개발 서버 주소로 대신한다 (production은 대신하지 않는다).
    google_redirect_uri: str
    google_allowed_redirect_uris: list[str]
    # GitHub(OAuth App)·네이버 로그인 설정 (Google과 같은 규칙: production에서 켜려면 ID·시크릿을 함께 넣고 https 콜백 주소를
    # 명시한다). 콜백 주소는 *_REDIRECT_URI 하나만 받는다 (Google의 허용 목록 같은 추가 주소는 없다). 변수가 없으면
    # development만 FE 개발 서버 주소로 대신한다.
    github_client_id: str
    github_client_secret: str = field(repr=False)
    github_redirect_uri: str
    naver_client_id: str
    naver_client_secret: str = field(repr=False)
    naver_redirect_uri: str
    # 세 OAuth 제공자 호출의 제한 시간(초). OAUTH_TIMEOUT_SECONDS (비우면 예전 GOOGLE_OAUTH_TIMEOUT_SECONDS, 둘 다 없으면 10).
    oauth_timeout_seconds: float
    # AI 서비스(ai/, `uvicorn src.server.app:app`). 직무 비교·커리큘럼 계산을 맡는다.
    ai_service_url: str
    # AI의 AI_SERVICE_TOKEN과 같은 32바이트 이상 공유 비밀. 비면 AI 기능 요청은 503.
    ai_service_token: str = field(repr=False)
    # 루프백이 아닌 http:// AI 주소는 이 값이 true일 때만 허용한다 (Docker 내부망 등).
    ai_service_allow_http: bool
    ai_timeout_seconds: float
    ai_generation_timeout_seconds: float
    # 프로세스 하나가 AI 서비스에 동시에 보내는 공고·학습자료 묶음 요청 수의 상한 (1 이상). 넘는 요청은 기다리다 503.
    ai_max_concurrency: int
    # 프로세스 하나가 동시에 여는 앱 DB 연결 수의 상한 (2 이상). 넘는 요청은 자리를 기다리다 503 (database.app_db_connection).
    db_max_connections: int
    # 공고·학습자료를 반입할 수 있는 연구자 사용자 UUID 목록.
    researcher_user_ids: frozenset[str]
    # 커리큘럼 LLM 모드를 쓸 수 있는 사용자 UUID 목록 (LLM 설정 자체는 AI 서비스 .env). "*"가 있으면 로그인한 모든 계정.
    curriculum_llm_user_ids: frozenset[str]
    # 허용된 사용자 한 명이 24시간에 만들 수 있는 LLM 모드 학습 계획 수 (1 이상, 기본 20). 외부 LLM(Claude) 비용·한도 보호.
    curriculum_llm_daily_limit: int
    # 모든 계정을 합쳐 24시간에 만들 수 있는 LLM 모드 학습 계획 수 (1 이상, 기본 200).
    curriculum_llm_global_daily_limit: int = DEFAULT_CURRICULUM_LLM_GLOBAL_DAILY_LIMIT

    def llm_allowed(self, user_id: str) -> bool:
        """이 계정이 커리큘럼 LLM 모드를 쓸 수 있는지 (CURRICULUM_LLM_USER_IDS에 UUID가 있거나 "*")."""
        return ALL_USERS in self.curriculum_llm_user_ids or user_id in self.curriculum_llm_user_ids


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

        try:
            holder_key = parse_master_key(raw_master_key)
        except ValueError as exc:
            # 형식 오류(base64가 아님, 32바이트가 아님. `NAME=` 접두사를 붙인 값 등)도 다른 설정 오류처럼 RuntimeError 한 줄로
            # 알린다: 서버는 traceback 없이 이 줄과 종료 코드 1로 끝난다 (main.py). 메시지는 parse_master_key의 고정 문구
            # (변수 이름과 규칙)뿐이고 넣은 값은 싣지 않는다. 원래 예외(base64 디코더 오류)는 잇지 않는다.
            raise RuntimeError(str(exc)) from None
        # 디코딩한 바이트로 비교하므로 base64 표기(표준·URL-safe·패딩 없음)가 달라도 같은 키로 본다.
        if production and _weak_secret(holder_key):
            missing.append(f"HOLDER_KEY_ENCRYPTION_KEY ({_RANDOM_NOT_PUBLISHED})")
    elif production:
        missing.append("HOLDER_KEY_ENCRYPTION_KEY")
    else:
        logger.warning("HOLDER_KEY_ENCRYPTION_KEY가 설정되지 않아 새 가입자에게 홀더 키 없이 임시 DID를 발급합니다.")

    google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    # FE 개발 서버 콜백 주소는 development에서만 기본값으로 쓴다. production에서 localhost 주소가 조용히 허용 목록에
    # 들어가면 인가 코드를 사용자 PC의 localhost로 보내는 로그인 주소를 만들 수 있다.
    google_redirect_uri = os.getenv(
        "GOOGLE_REDIRECT_URI", "" if production else "http://localhost:5173/auth/google/callback"
    ).strip()
    raw_allowed_redirects = _env_list("GOOGLE_ALLOWED_REDIRECT_URIS", "")
    allowed_redirects = list(
        dict.fromkeys(([google_redirect_uri] if google_redirect_uri else []) + raw_allowed_redirects)
    )
    # GitHub·네이버도 같은 이유로 FE 개발 서버 콜백 주소는 development에서만 기본값으로 쓴다.
    github_client_id = os.getenv("GITHUB_CLIENT_ID", "").strip()
    github_client_secret = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
    github_redirect_uri = os.getenv(
        "GITHUB_REDIRECT_URI", "" if production else "http://localhost:5173/auth/github/callback"
    ).strip()
    naver_client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    naver_client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    naver_redirect_uri = os.getenv(
        "NAVER_REDIRECT_URI", "" if production else "http://localhost:5173/auth/naver/callback"
    ).strip()

    service_api_key = os.getenv("SERVICE_API_KEY", "").strip()
    if production and service_api_key and (
        len(service_api_key.encode("utf-8")) < MIN_JWT_SECRET_BYTES or _weak_secret(service_api_key.encode("utf-8"))
    ):
        # 모든 DID를 읽는 키이므로 JWT_SECRET과 같은 기준을 둔다 (비우면 서비스 키 인증을 끈다).
        missing.append(f"SERVICE_API_KEY (empty, or >= {MIN_JWT_SECRET_BYTES} bytes {_RANDOM_NOT_PUBLISHED})")

    if production:
        # 비밀값은 용도마다 따로 만든다: DXWORKS_API_KEY(협력사로 보낸다)나 AI_SERVICE_TOKEN(AI 서비스도 가진다)을 아는 쪽이
        # 같은 값으로 사용자 토큰을 만들거나(JWT_SECRET) 홀더 키를 복호화하거나(HOLDER_KEY_ENCRYPTION_KEY) 모든 DID를 읽지
        # (SERVICE_API_KEY) 못하게 여덟 값의 모든 쌍을 비교한다. 비운 값은 비교하지 않는다. 바꾸기 어려운 값부터 두고
        # (협력사가 발급한 키, 바꾸면 기존 홀더 키를 복호화할 수 없는 마스터 키, 바꾸면 모두 로그아웃되는 JWT_SECRET 순),
        # 같은 쌍은 뒤쪽(바꿀 값)의 이름을 앞에 써서 알린다. GOOGLE_CLIENT_SECRET도 교환할 때마다 Google로 보내고
        # Google Cloud 프로젝트 관리자가 볼 수 있는 값이라 함께 비교한다 (Google이 발급하므로 앞쪽에 둔다).
        # GITHUB_CLIENT_SECRET·NAVER_CLIENT_SECRET도 같은 이유로 비교한다 (각 제공자가 발급한다).
        values = {"DXWORKS_API_KEY": os.getenv("DXWORKS_API_KEY", "").strip(),
                  "GOOGLE_CLIENT_SECRET": google_client_secret,
                  "GITHUB_CLIENT_SECRET": github_client_secret, "NAVER_CLIENT_SECRET": naver_client_secret,
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
        # DB TLS(PGSSLMODE·PGSSLROOTCERT)도 시작할 때 알린다. 접속할 때도 db_tls_options()가 다시 막는다.
        missing += _db_tls_problems()

    if production and (google_client_id or google_client_secret):
        # Google 로그인을 켠 운영 설정. 한쪽만 넣으면 요청마다 503이 되고, 콜백 주소를 빼면 FE의 운영 콜백이 400이 되며,
        # http:// 콜백은 인가 코드를 평문으로 실어 보낸다. 요청 때가 아니라 시작할 때 알린다 (이름만, 값은 넣지 않는다).
        if not (google_client_id and google_client_secret):
            missing.append("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (set both, or leave both empty)")
        if not google_redirect_uri:
            missing.append("GOOGLE_REDIRECT_URI (set explicitly when Google OAuth is configured)")
        if any(not _is_https_url(uri) for uri in allowed_redirects):
            missing.append("GOOGLE_REDIRECT_URI and GOOGLE_ALLOWED_REDIRECT_URIS (https:// addresses only)")

    # GitHub·네이버 로그인을 켠 운영 설정도 Google과 같은 규칙이다 (이름만, 값은 넣지 않는다).
    for prefix, label, client_id, client_secret, redirect_uri in (
            ("GITHUB", "GitHub", github_client_id, github_client_secret, github_redirect_uri),
            ("NAVER", "Naver", naver_client_id, naver_client_secret, naver_redirect_uri)):
        if not (production and (client_id or client_secret)):
            continue
        if not (client_id and client_secret):
            missing.append(f"{prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET (set both, or leave both empty)")
        if not redirect_uri:
            missing.append(f"{prefix}_REDIRECT_URI (set explicitly when {label} OAuth is configured)")
        elif not _is_https_url(redirect_uri):
            missing.append(f"{prefix}_REDIRECT_URI (https:// address only)")

    # development는 변수가 없으면 FE 개발 서버 주소를 쓴다. production은 없거나 비어 있으면 다른 출처를 허용하지 않고
    # (FE와 API가 같은 출처), 넣었으면 모든 항목이 scheme://host[:port] 꼴이어야 한다.
    cors_origins = _env_list("CORS_ORIGINS", "" if production else "http://localhost:5173,http://127.0.0.1:5173")
    if production and not all(_is_plain_origin(origin) for origin in cors_origins):
        missing.append("CORS_ORIGINS (scheme://host[:port] entries only: no *, null, path or trailing slash)")

    if missing:
        raise RuntimeError(
            "APP_ENV=production requires " + ", ".join(missing)
            + "; set the same values on every task/worker (e.g. AWS Secrets Manager or SSM)"
        )

    jwt_expires_minutes = _env_int("JWT_EXPIRES_MINUTES", MAX_JWT_EXPIRES_MINUTES,
                                   minimum=1, maximum=MAX_JWT_EXPIRES_MINUTES)
    mock_issuer_daily_limit = _env_int("MOCK_ISSUER_DAILY_LIMIT", DEFAULT_MOCK_ISSUER_DAILY_LIMIT, minimum=1)
    mock_issuer_global_daily_limit = _env_int("MOCK_ISSUER_GLOBAL_DAILY_LIMIT",
                                              DEFAULT_MOCK_ISSUER_GLOBAL_DAILY_LIMIT, minimum=1)
    badge_template_id = os.getenv("DXWORKS_BADGE_TEMPLATE_ID", "").strip()
    # DXWorks 접속값은 넣었을 때만 형식을 본다 (비워 두면 지금처럼 그 기능만 503). 잘못된 값은 요청마다 503이 되는 대신
    # 시작할 때 바로 알린다. 오류 메시지에는 변수 이름만 넣고 값은 넣지 않는다.
    dxworks_api_host = os.getenv("DXWORKS_API_HOST", "").strip().rstrip("/")
    dxworks_api_key = os.getenv("DXWORKS_API_KEY", "").strip()
    if dxworks_api_host:
        from app.integrations.dxworks import is_valid_host_setting

        if not is_valid_host_setting(dxworks_api_host):
            raise RuntimeError("DXWORKS_API_HOST must be an https:// URL (http:// only for loopback) without"
                               " credentials, query, fragment or whitespace")
    if not dxworks_api_key.isascii():
        raise RuntimeError("DXWORKS_API_KEY must contain only ASCII characters")
    settings = Settings(
        app_env=app_env,
        dxworks_api_host=dxworks_api_host,
        dxworks_api_key=dxworks_api_key,
        # 0·음수·inf·nan이면 시간 제한이 없어지거나 모든 호출이 실패하므로 범위를 둔다 (비우면 15초).
        dxworks_timeout_seconds=_env_float("DXWORKS_TIMEOUT_SECONDS", 15.0, minimum=1, maximum=120),
        dxworks_badge_template_id=badge_template_id,
        identity_verification_enabled=_env_bool("IDENTITY_VERIFICATION_ENABLED", False),
        jwt_secret=jwt_secret,
        jwt_expires_minutes=jwt_expires_minutes,
        service_api_key=service_api_key,
        cors_origins=cors_origins,
        mock_issuer_enabled=_env_bool("MOCK_ISSUER_ENABLED", not production),
        mock_issuer_template_ids=frozenset([badge_template_id, *_env_list("MOCK_ISSUER_TEMPLATE_IDS", "")]) - {""},
        mock_issuer_daily_limit=mock_issuer_daily_limit,
        mock_issuer_global_daily_limit=mock_issuer_global_daily_limit,
        holder_key_encryption_key=holder_key,
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
        google_redirect_uri=google_redirect_uri,
        google_allowed_redirect_uris=allowed_redirects,
        github_client_id=github_client_id,
        github_client_secret=github_client_secret,
        github_redirect_uri=github_redirect_uri,
        naver_client_id=naver_client_id,
        naver_client_secret=naver_client_secret,
        naver_redirect_uri=naver_redirect_uri,
        # 아래 세 시간 제한도 DXWORKS_TIMEOUT_SECONDS처럼 읽는다: 비우면 기본값, 숫자가 아니거나 0·음수·inf·nan이면
        # (시간 제한이 없어지거나 모든 호출이 실패한다) 변수 이름을 알리고 시작을 거부한다.
        oauth_timeout_seconds=_oauth_timeout_seconds(),
        ai_service_url=os.getenv("AI_SERVICE_URL", "http://127.0.0.1:8100").strip().rstrip("/"),
        ai_service_token=os.getenv("AI_SERVICE_TOKEN", ""),
        ai_service_allow_http=_env_bool("AI_SERVICE_ALLOW_HTTP", False),
        ai_timeout_seconds=_env_float("AI_TIMEOUT_SECONDS", 30.0, minimum=1, maximum=600),
        ai_generation_timeout_seconds=_env_float("AI_GENERATION_TIMEOUT_SECONDS", 90.0, minimum=1, maximum=600),
        ai_max_concurrency=_env_int("AI_MAX_CONCURRENCY", DEFAULT_AI_MAX_CONCURRENCY, minimum=1),
        db_max_connections=db_max_connections(),
        researcher_user_ids=frozenset(_env_list("RESEARCHER_USER_IDS", "")),
        curriculum_llm_user_ids=frozenset(_env_list("CURRICULUM_LLM_USER_IDS", "")),
        curriculum_llm_daily_limit=_env_int("CURRICULUM_LLM_DAILY_LIMIT", DEFAULT_CURRICULUM_LLM_DAILY_LIMIT,
                                            minimum=1),
        curriculum_llm_global_daily_limit=_env_int("CURRICULUM_LLM_GLOBAL_DAILY_LIMIT",
                                                   DEFAULT_CURRICULUM_LLM_GLOBAL_DAILY_LIMIT, minimum=1),
    )
    if production and settings.mock_issuer_enabled:
        logger.warning(
            "MOCK_ISSUER_ENABLED=true (production): 로그인 사용자가 허용된 템플릿으로 본인 DID에 배지를 발급할 수 있습니다"
            " (사용자별 24시간 %d건, 전체 24시간 %d건).",
            settings.mock_issuer_daily_limit, settings.mock_issuer_global_daily_limit,
        )
    if not _env_bool("AUTH_REQUIRED", True):
        # 예전 전환 기간 설정이 남은 .env·작업 정의. 인증을 끄는 설정은 없어졌다 (README "인증").
        logger.warning("AUTH_REQUIRED=false 설정은 더 이상 쓰지 않습니다: 토큰 없는 요청은 이 값과 관계없이 항상 401로 거절합니다.")
    if os.getenv("KAKAO_CLIENT_ID", "").strip() or os.getenv("KAKAO_CLIENT_SECRET", "").strip():
        # 카카오 로그인을 없앤 뒤에도 값이 남은 .env·작업 정의 (AUTH_REQUIRED와 같은 방식). 값은 쓰지도 로그에 남기지도 않는다.
        logger.warning("KAKAO_* 설정은 더 이상 쓰지 않습니다 (카카오 로그인 제거). .env에서 지웁니다.")
    return settings

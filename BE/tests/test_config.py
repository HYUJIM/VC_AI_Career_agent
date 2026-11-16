"""APP_ENV별 설정 검사와 DB 접속 설정 (DB 없이 설정 값만 본다).

- development(기본, 비어 있어도 같음): 비밀값이 없으면 지금처럼 경고하고 임시 값으로 대신한다. 테스트 값도 그대로 받는다.
- production: JWT_SECRET(32바이트 이상)·HOLDER_KEY_ENCRYPTION_KEY·DB_HOST·DB_NAME·DB_USER·DB_PASSWORD가 없거나,
  이 저장소에 공개된 값·자리표시 값(DB_PASSWORD는 로컬 기본값)이면 서버가 시작하지 않는다.
  SERVICE_API_KEY는 비우거나 32바이트 이상의 임의 값이어야 한다.
  JWT_SECRET·HOLDER_KEY_ENCRYPTION_KEY·SERVICE_API_KEY·AI_SERVICE_TOKEN·DXWORKS_API_KEY·GOOGLE_CLIENT_SECRET·
  GITHUB_CLIENT_SECRET·NAVER_CLIENT_SECRET은 (설정한 경우) 모두 서로 다른 값이어야 한다.
  Google 로그인을 켜려면 GOOGLE_CLIENT_ID·GOOGLE_CLIENT_SECRET을 함께 넣고 GOOGLE_REDIRECT_URI를 명시해야 하며 허용하는
  콜백 주소는 모두 https여야 한다 (localhost 기본값은 development에서만 쓴다). GitHub·네이버도 같은 규칙이다
  (GITHUB_*·NAVER_*, 콜백 주소는 *_REDIRECT_URI 하나). 카카오 로그인은 없앴다: 남은 카카오 설정 값은 쓰지 않고 경고 한 줄만
  남긴다.
  CORS_ORIGINS는 비우면 빈 목록이고, 넣었으면 모든 항목이 scheme://host[:port] 꼴이어야 한다 (`*`·`null`·경로·끝의 `/` 거부).
  DB TLS: PGSSLMODE는 verify-full·verify-ca(앞뒤 공백만 떼고 글자 그대로)여야 하고 PGSSLROOTCERT는 읽을 수 있는 파일이어야
  한다 (서버 시작과 get_db_connection 모두. 마이그레이션 실행기 쪽은 tests/test_migrate.py).
  설정 오류는 uvicorn·`import main`에서 traceback 없이 메시지 원문 한 줄 + 종료 코드 1이다.
- 공통: JWT_EXPIRES_MINUTES는 1~1440, 모의 발급 기본값(development 켬·production 끔)·템플릿 허용 목록·사용자별 한도·
  전체 한도, 예전 AUTH_REQUIRED 설정은 경고만 남기고 인증을 끄지 못한다, 설정 객체의 repr에 비밀값이 나오지 않는다.
  DXWorks 접속값(DXWORKS_API_HOST·DXWORKS_API_KEY·DXWORKS_TIMEOUT_SECONDS)은 넣었으면 형식·범위가 맞아야 시작한다
  (비워 두는 것은 지금처럼 허용). 신원 VP 검증은 IDENTITY_VERIFICATION_ENABLED로 켤 때만 받는다.
  OAuth·AI 시간 제한도 비우면 기본값이고 범위를 벗어나면 변수 이름을 알리며 시작하지 않는다 (OAUTH_TIMEOUT_SECONDS가
  없으면 예전 이름 GOOGLE_OAUTH_TIMEOUT_SECONDS를 읽는다).
  비워 둔 LOG_LEVEL은 INFO, 비워 둔 DB_PORT는 5432다 (traceback이나 요청마다 500이 아니다). 없는 수준 이름의 LOG_LEVEL과
  형식이 틀린 HOLDER_KEY_ENCRYPTION_KEY도 다른 설정 오류처럼 RuntimeError 한 줄(넣은 값 없음)이다.
  비워 둔 PGSSLMODE·PGSSLROOTCERT는 없는 것과 같다 (PGSSLMODE는 libpq 기본값 prefer, 앞뒤 공백은 뗀다).
production에서 받아들이는 값은 실행할 때마다 새로 만든다 (저장소에 고정해 두면 그 자체가 공개된 값이 된다).
오류 메시지·로그에 비밀값이 들어가지 않는지도 확인한다.
"""
import base64
import dataclasses
import logging
import os
import pprint
import re
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from app.core.config import Settings, get_settings
from tests.conftest import PRODUCTION_DB_TLS_ENV, RDS_CA_BUNDLE

BE_DIR = Path(__file__).resolve().parents[1]
TEST_JWT_SECRET = secrets.token_urlsafe(48)
TEST_MASTER_KEY = base64.b64encode(os.urandom(32)).decode()
TEST_DB_PASSWORD = secrets.token_urlsafe(24)  # production 검사용 값 (이 파일의 테스트는 DB에 접속하지 않는다)
# 이 저장소에 공개된 테스트·예시 값과 자리표시 값. production에서는 모두 거부한다.
PUBLISHED = [
    ("JWT_SECRET", "test-secret-for-pytest-only-0123456789"),  # tests/conftest.py
    ("JWT_SECRET", "pytest-only-jwt-secret-0123456789abcdef"),  # 이 파일의 예전 값
    ("JWT_SECRET", "x" * 32),
    ("JWT_SECRET", "ab" * 40),  # 길어도 서로 다른 바이트가 적은 자리표시 값
    ("HOLDER_KEY_ENCRYPTION_KEY", "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="),  # tests/conftest.py (0x01 반복)
    ("HOLDER_KEY_ENCRYPTION_KEY", base64.urlsafe_b64encode(b"\x01" * 32).decode().rstrip("=")),  # 같은 키, 다른 표기
    ("HOLDER_KEY_ENCRYPTION_KEY", base64.b64encode(b"\x02" * 32).decode()),  # 이 파일의 예전 값
    ("HOLDER_KEY_ENCRYPTION_KEY", base64.b64encode(bytes(range(32))).decode()),  # tests/test_key_custody.py
    ("HOLDER_KEY_ENCRYPTION_KEY", base64.b64encode(bytes(32)).decode()),
    ("SERVICE_API_KEY", "test-service-key"),  # tests/conftest.py·test_api.py·test_features.py
    ("SERVICE_API_KEY", "s" * 40),
    ("SERVICE_API_KEY", "short-random-looking-Zq8LmX2v"),  # 32바이트 미만
    ("DB_PASSWORD", "password"),  # docker-compose.yml·.env.example·tests/conftest.py·CI 기본값
    ("DB_PASSWORD", " password "),
    ("DB_PASSWORD", " "),
]
PUBLISHED_IDS = [f"{name}-{index}" for index, (name, _) in enumerate(PUBLISHED)]  # 테스트 이름에 값을 넣지 않는다


@pytest.fixture(autouse=True)
def fresh_settings():
    # 여기서 만든 설정(특히 production)이 캐시에 남아 다른 테스트로 새지 않게 앞뒤로 비운다.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def production(monkeypatch, *, jwt_secret: str = TEST_JWT_SECRET, master_key: str = TEST_MASTER_KEY) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("JWT_SECRET", jwt_secret)
    monkeypatch.setenv("HOLDER_KEY_ENCRYPTION_KEY", master_key)
    # conftest의 로컬 기본 DB 비밀번호·공개된 서비스 키는 production에서 거부되므로 바꿔 둔다.
    # DB_HOST·DB_NAME·DB_USER는 conftest가 넣은 값을 그대로 쓴다.
    monkeypatch.setenv("DB_PASSWORD", TEST_DB_PASSWORD)
    monkeypatch.setenv("SERVICE_API_KEY", "")
    # DB TLS: 운영 작업 정의와 같은 값 (verify-full + 이미지의 RDS CA 묶음). 이 파일의 테스트는 DB에 접속하지 않는다.
    for name, value in PRODUCTION_DB_TLS_ENV.items():
        monkeypatch.setenv(name, value)


def test_development_is_default_and_keeps_fallbacks(monkeypatch, caplog):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("JWT_SECRET", "")
    monkeypatch.setenv("HOLDER_KEY_ENCRYPTION_KEY", "")
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        settings = get_settings()
    assert settings.app_env == "development"
    assert len(settings.jwt_secret.encode("utf-8")) >= 32  # 프로세스마다 새로 만든 임시 키
    assert settings.holder_key_encryption_key is None  # 가입자는 임시 DID
    assert "JWT_SECRET" in caplog.text and "HOLDER_KEY_ENCRYPTION_KEY" in caplog.text  # 예전과 같은 경고


@pytest.mark.parametrize("value", ["", "  ", "development"])
def test_blank_or_development_app_env_is_development(monkeypatch, value):
    monkeypatch.setenv("APP_ENV", value)
    assert get_settings().app_env == "development"


@pytest.mark.parametrize("value", ["prod", "staging", "dev", "test"])
def test_unknown_app_env_refuses_to_start(monkeypatch, value):
    monkeypatch.setenv("APP_ENV", value)
    with pytest.raises(RuntimeError, match="APP_ENV must be one of: development, production"):
        get_settings()


def test_production_with_secrets_starts(monkeypatch):
    production(monkeypatch)
    settings = get_settings()
    assert settings.app_env == "production"
    assert settings.jwt_secret == TEST_JWT_SECRET
    assert settings.holder_key_encryption_key == base64.b64decode(TEST_MASTER_KEY)
    assert settings.service_api_key == ""  # 비우면 서비스 키 인증을 끈다 (README 권장값)


def test_production_requires_jwt_secret(monkeypatch):
    production(monkeypatch, jwt_secret="")
    with pytest.raises(RuntimeError, match="JWT_SECRET") as error:
        get_settings()
    assert "HOLDER_KEY_ENCRYPTION_KEY" not in str(error.value)  # 빠진 것만 알려 준다


def test_production_rejects_short_jwt_secret_without_echoing_it(monkeypatch):
    production(monkeypatch, jwt_secret="short-secret-value")
    with pytest.raises(RuntimeError, match=r"JWT_SECRET \(>= 32 bytes\)") as error:
        get_settings()
    assert "short-secret-value" not in str(error.value)

    boundary = secrets.token_urlsafe(24)  # 정확히 32바이트
    production(monkeypatch, jwt_secret=boundary[:31])
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        get_settings()

    production(monkeypatch, jwt_secret=boundary)
    get_settings.cache_clear()
    assert get_settings().jwt_secret == boundary


def test_production_requires_holder_key_encryption_key(monkeypatch):
    production(monkeypatch, master_key="")
    with pytest.raises(RuntimeError, match="HOLDER_KEY_ENCRYPTION_KEY") as error:
        get_settings()
    assert "JWT_SECRET" not in str(error.value)


# ── production: 공개된 값·자리표시 값·로컬 기본값 거부 ─────────────────────

@pytest.mark.parametrize(("name", "value"), PUBLISHED, ids=PUBLISHED_IDS)
def test_production_rejects_values_published_in_this_repository(monkeypatch, name, value):
    # 테스트·로컬 환경 값을 그대로 운영 작업 정의에 옮겨도 시작하지 않는다. 빠진 값과 같은 형식으로 이름만 알린다.
    production(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=f"^APP_ENV=production requires {name} ") as error:
        get_settings()
    assert value.strip() == "" or value.strip() not in str(error.value)
    others = {"JWT_SECRET", "HOLDER_KEY_ENCRYPTION_KEY", "SERVICE_API_KEY", "DB_PASSWORD"} - {name}
    assert [other for other in others if other in str(error.value)] == []  # 문제가 있는 변수만 알린다


def test_secrets_set_by_conftest_are_rejected_in_production(monkeypatch):
    # tests/conftest.py 값이 바뀌어도 운영에서 받아들여지지 않는지 본다 (바꾸면 app.core.config.PUBLISHED_SECRETS에도 더한다).
    published = {name: os.environ[name] for name in ("JWT_SECRET", "HOLDER_KEY_ENCRYPTION_KEY", "SERVICE_API_KEY")}
    for name, value in published.items():
        production(monkeypatch)
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match=f"requires {name} ") as error:
            get_settings()
        assert value not in str(error.value)


def test_production_accepts_random_service_api_key_or_none(monkeypatch):
    production(monkeypatch)
    assert get_settings().service_api_key == ""
    key = secrets.token_urlsafe(32)
    monkeypatch.setenv("SERVICE_API_KEY", key)
    get_settings.cache_clear()
    assert get_settings().service_api_key == key


def test_production_requires_explicit_db_settings(monkeypatch):
    # 운영에서 DB_*가 비면 개발용 기본값(localhost, postgres/password)으로 접속하지 않고 시작을 거부한다.
    production(monkeypatch)
    monkeypatch.setenv("DB_HOST", " ")
    monkeypatch.delenv("DB_USER", raising=False)
    with pytest.raises(RuntimeError, match=r"^APP_ENV=production requires DB_HOST, DB_USER; ") as error:
        get_settings()
    assert "DB_NAME" not in str(error.value) and "JWT_SECRET" not in str(error.value)

    monkeypatch.delenv("DB_PASSWORD")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match=r"requires DB_HOST, DB_USER, DB_PASSWORD \("):
        get_settings()


def test_production_env_fixture_passes_production_checks(production_env, monkeypatch):
    # conftest의 production_env(운영 모드 API 테스트용)는 실행할 때마다 새 비밀값을 넣고, 테스트 DB에는 실제 비밀번호로 접속한다.
    from app.services import database
    from tests.conftest import TEST_DB_PASSWORD as REAL_TEST_DB_PASSWORD

    settings = get_settings()
    assert settings.app_env == "production"
    assert settings.jwt_secret == production_env["JWT_SECRET"]
    assert settings.service_api_key == production_env["SERVICE_API_KEY"]
    seen: dict = {}
    monkeypatch.setattr(database.psycopg, "connect", lambda **kwargs: seen.update(kwargs))
    database.get_db_connection()
    assert seen["password"] == REAL_TEST_DB_PASSWORD != production_env["DB_PASSWORD"]


def test_development_keeps_accepting_test_values(monkeypatch):
    # 로컬·테스트(conftest 값, docker-compose 기본 DB 비밀번호, DB_* 기본값)는 지금과 같다.
    monkeypatch.delenv("APP_ENV", raising=False)
    for name in ("DB_HOST", "DB_NAME", "DB_USER"):
        monkeypatch.delenv(name, raising=False)
    for name, value in PUBLISHED:
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        assert get_settings().app_env == "development"


def _one_error_line(result: subprocess.CompletedProcess) -> str:
    """설정 오류로 끝난 프로세스: 종료 코드 1, stdout 없음, stderr는 traceback 없이 오류 한 줄. 그 줄을 돌려준다."""
    assert result.returncode == 1, result.stderr[-2000:]
    assert result.stdout == "" and "Traceback" not in result.stderr, result.stderr[-2000:]
    lines = result.stderr.splitlines()
    assert len(lines) == 1, result.stderr[-2000:]
    return lines[0]


def test_server_refuses_to_start_in_production_without_secrets():
    # main.py는 import 시점(CORS 설정)에 설정을 읽으므로 uvicorn이 앱을 불러오는 순간 실패한다.
    # 빈 값을 명시해 .env가 있어도 채워 넣지 못하게 한다.
    # 회귀: 예전에는 41줄 traceback(RuntimeError: ...)이었다. 이제 메시지 원문 한 줄 + 종료 코드 1이다.
    env = {**os.environ, "APP_ENV": "production", "JWT_SECRET": "short-secret-value",
           "HOLDER_KEY_ENCRYPTION_KEY": "", "PYTHONDONTWRITEBYTECODE": "1", **PRODUCTION_DB_TLS_ENV}
    result = subprocess.run([sys.executable, "-c", "import main"], cwd=BE_DIR, env=env,
                            capture_output=True, text=True, timeout=120)
    line = _one_error_line(result)
    assert line.startswith("APP_ENV=production requires JWT_SECRET (>= 32 bytes), HOLDER_KEY_ENCRYPTION_KEY, ")
    assert line.endswith("; set the same values on every task/worker (e.g. AWS Secrets Manager or SSM)")
    assert "short-secret-value" not in result.stderr


def test_server_refuses_to_start_in_production_with_the_test_environment():
    # 테스트·로컬 환경 값(conftest 비밀값, 로컬 기본 DB 비밀번호)을 그대로 쓴 운영 작업 정의는 시작하지 않는다.
    # DB TLS 변수(PGSSLMODE·PGSSLROOTCERT)도 없으므로 함께 알린다.
    env = {name: value for name, value in os.environ.items() if not name.startswith("PGSSL")}
    env.update({"APP_ENV": "production", "DB_PASSWORD": "password", "PYTHONDONTWRITEBYTECODE": "1"})
    published = [env[name] for name in ("JWT_SECRET", "HOLDER_KEY_ENCRYPTION_KEY", "SERVICE_API_KEY")]
    result = subprocess.run([sys.executable, "-c", "import main"], cwd=BE_DIR, env=env,
                            capture_output=True, text=True, timeout=120)
    line = _one_error_line(result)
    assert line.startswith("APP_ENV=production requires ")
    for name in ("JWT_SECRET", "HOLDER_KEY_ENCRYPTION_KEY", "SERVICE_API_KEY", "DB_PASSWORD",
                 "PGSSLMODE (verify-full or verify-ca in production)", "PGSSLROOTCERT (readable CA bundle file)"):
        assert name in line
    for value in published:
        assert value not in result.stderr


@pytest.mark.parametrize("case,expected", [
    ("missing-secrets", "APP_ENV=production requires JWT_SECRET (>= 32 bytes), HOLDER_KEY_ENCRYPTION_KEY, "
                        "PGSSLMODE (verify-full or verify-ca in production), PGSSLROOTCERT (readable CA bundle file); "
                        "set the same values on every task/worker (e.g. AWS Secrets Manager or SSM)"),
    ("unknown-app-env", "APP_ENV must be one of: development, production (empty means development)"),
    ("range-error", "JWT_EXPIRES_MINUTES must be an integer between 1 and 1440"),
    # 회귀(E1): 아래 두 경우는 import 중의 ValueError traceback(수십 줄)으로 끝났다.
    ("unknown-log-level", "LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL (empty means INFO)"),
    ("malformed-master-key", "HOLDER_KEY_ENCRYPTION_KEY must be base64"),
    ("prefixed-master-key", "HOLDER_KEY_ENCRYPTION_KEY must decode to 32 bytes"),
])
def test_uvicorn_ends_a_config_error_with_one_stderr_line_and_exit_code_1(case, expected):
    # 운영 이미지의 CMD와 같은 uvicorn CLI. 설정 오류(RuntimeError)는 import 중에 끝나므로 서버를 띄우지 않고(포트를 열지
    # 않음) 메시지 원문 한 줄만 남긴다: CloudWatch 이벤트 하나, 메트릭 필터는 `APP_ENV=production requires`·`must be`.
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("PGSSL", "GOOGLE_", "GITHUB_", "NAVER_", "OAUTH_", "CORS_"))}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "DB_PASSWORD": secrets.token_urlsafe(24), "SERVICE_API_KEY": ""})
    secret_values = [env["DB_PASSWORD"]]
    if case == "missing-secrets":
        env.update({"APP_ENV": "production", "JWT_SECRET": "", "HOLDER_KEY_ENCRYPTION_KEY": ""})
    elif case == "unknown-app-env":
        env.update({"APP_ENV": "prod"})
    elif case == "unknown-log-level":
        env.update({"APP_ENV": "development", "LOG_LEVEL": "verbose"})
        secret_values.append("verbose")
    elif case in ("malformed-master-key", "prefixed-master-key"):
        # 운영 이미지 그대로(production). base64 길이가 맞지 않는 키, 또는 Secrets Manager에 `NAME=값` 꼴로 넣은 키
        # (README ④). 오류 줄에는 넣은 값이 나오지 않는다.
        key = base64.b64encode(os.urandom(32)).decode().rstrip("=")
        value = key + "AB" if case == "malformed-master-key" else f"HOLDER_KEY_ENCRYPTION_KEY={key}"
        env.update({"APP_ENV": "production", "JWT_SECRET": secrets.token_urlsafe(48), "HOLDER_KEY_ENCRYPTION_KEY": value,
                    **PRODUCTION_DB_TLS_ENV})
        secret_values += [key, env["JWT_SECRET"]]
    else:
        env.update({"APP_ENV": "development", "JWT_EXPIRES_MINUTES": "0"})
    result = subprocess.run([sys.executable, "-B", "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "0"],
                            cwd=BE_DIR, env=env, capture_output=True, text=True, timeout=60)
    assert _one_error_line(result) == expected
    for value in secret_values:
        assert value not in result.stderr


# ── production: 한 비밀값을 여러 용도에 쓰지 않기 ─────────────────────

REUSED = [("SERVICE_API_KEY", "JWT_SECRET"), ("AI_SERVICE_TOKEN", "JWT_SECRET"),
          ("AI_SERVICE_TOKEN", "SERVICE_API_KEY")]


def distinct_service_secrets(monkeypatch) -> dict[str, str]:
    """production 검사를 통과하는 서로 다른 JWT_SECRET·SERVICE_API_KEY·AI_SERVICE_TOKEN (실행할 때마다 새로 만든다)."""
    values = {"JWT_SECRET": secrets.token_urlsafe(48), "SERVICE_API_KEY": secrets.token_urlsafe(32),
              "AI_SERVICE_TOKEN": secrets.token_urlsafe(40)}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.mark.parametrize(("name", "other"), REUSED, ids=[f"{name}-{other}" for name, other in REUSED])
def test_production_refuses_one_secret_for_two_purposes(monkeypatch, name, other):
    # AI_SERVICE_TOKEN(AI 서비스도 가진다)이나 SERVICE_API_KEY를 아는 쪽이 같은 값으로 사용자 토큰을 만들거나(JWT_SECRET)
    # 모든 DID를 읽지(SERVICE_API_KEY) 못하게 한다. 빠진 값과 같은 오류로 변수 이름만 알리고 값은 넣지 않는다.
    production(monkeypatch)
    values = distinct_service_secrets(monkeypatch)
    expected = rf"^APP_ENV=production requires {name} \(not the same value as {other}\); "
    for reused in (values[other], f" {values[other]}\n"):  # 앞뒤 공백만 다른 값도 같은 비밀이다
        monkeypatch.setenv(name, reused)
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match=expected) as error:
            get_settings()
        assert values[other] not in str(error.value)
        assert [n for n in ("HOLDER_KEY_ENCRYPTION_KEY", "DB_PASSWORD") if n in str(error.value)] == []


def test_production_names_every_reused_pair_and_development_is_unchanged(monkeypatch):
    production(monkeypatch)
    shared = secrets.token_urlsafe(48)
    for name in ("JWT_SECRET", "SERVICE_API_KEY", "AI_SERVICE_TOKEN"):
        monkeypatch.setenv(name, shared)
    with pytest.raises(RuntimeError) as error:
        get_settings()
    assert str(error.value).startswith(
        "APP_ENV=production requires SERVICE_API_KEY (not the same value as JWT_SECRET), "
        "AI_SERVICE_TOKEN (not the same value as JWT_SECRET), "
        "AI_SERVICE_TOKEN (not the same value as SERVICE_API_KEY); ")
    assert shared not in str(error.value)

    monkeypatch.setenv("APP_ENV", "development")  # development는 지금과 같다 (같은 값이어도 시작한다)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.jwt_secret == settings.service_api_key == settings.ai_service_token == shared


def test_production_accepts_distinct_or_empty_service_secrets(monkeypatch):
    production(monkeypatch)
    values = distinct_service_secrets(monkeypatch)
    settings = get_settings()
    assert (settings.jwt_secret, settings.service_api_key, settings.ai_service_token) == tuple(values.values())
    # 비운 값은 비교하지 않는다 (SERVICE_API_KEY를 비우면 서비스 키 인증을 끄고, AI_SERVICE_TOKEN이 비면 AI 기능 요청은 503).
    monkeypatch.setenv("SERVICE_API_KEY", "")
    monkeypatch.setenv("AI_SERVICE_TOKEN", " ")
    get_settings.cache_clear()
    assert get_settings().app_env == "production"


# 비교하는 다섯 비밀값 (바꾸기 어려운 값부터). 아래 쌍은 오류에 나오는 순서 그대로 (바꿀 값, 같으면 안 되는 값)이다.
FIVE_SECRETS = ("DXWORKS_API_KEY", "HOLDER_KEY_ENCRYPTION_KEY", "JWT_SECRET", "SERVICE_API_KEY", "AI_SERVICE_TOKEN")
REUSED_WITH_DXWORKS_OR_MASTER_KEY = [
    ("HOLDER_KEY_ENCRYPTION_KEY", "DXWORKS_API_KEY"), ("JWT_SECRET", "DXWORKS_API_KEY"),
    ("SERVICE_API_KEY", "DXWORKS_API_KEY"), ("AI_SERVICE_TOKEN", "DXWORKS_API_KEY"),
    ("JWT_SECRET", "HOLDER_KEY_ENCRYPTION_KEY"), ("SERVICE_API_KEY", "HOLDER_KEY_ENCRYPTION_KEY"),
    ("AI_SERVICE_TOKEN", "HOLDER_KEY_ENCRYPTION_KEY"),
]


def distinct_five_secrets(monkeypatch) -> dict[str, str]:
    """다섯 비밀값에 서로 다른 값을 넣는다. 모두 32바이트 임의 값의 base64라 어느 변수에 옮겨 넣어도 production 검사
    (길이·자리표시 값·마스터 키 형식)를 통과한다 (실행할 때마다 새로 만든다)."""
    values = {name: base64.b64encode(os.urandom(32)).decode() for name in FIVE_SECRETS}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


@pytest.mark.parametrize(("name", "other"), REUSED_WITH_DXWORKS_OR_MASTER_KEY,
                         ids=[f"{name}-{other}" for name, other in REUSED_WITH_DXWORKS_OR_MASTER_KEY])
def test_production_refuses_a_secret_shared_with_the_dxworks_key_or_the_master_key(monkeypatch, name, other):
    # DXWORKS_API_KEY(협력사로 보낸다)나 AI_SERVICE_TOKEN(AI 서비스도 가진다)을 아는 쪽이 같은 값으로 사용자 토큰을 만들거나
    # 홀더 키를 복호화하거나 모든 DID를 읽지 못하게 한다. 어느 쪽 값을 다른 쪽에 넣어도(앞뒤 공백만 달라도) 같은 한 줄로
    # 두 이름만 알리고 값은 넣지 않는다.
    production(monkeypatch)
    values = distinct_five_secrets(monkeypatch)
    expected = rf"^APP_ENV=production requires {name} \(not the same value as {other}\); "
    others = [n for n in (*FIVE_SECRETS, "DB_PASSWORD") if n not in (name, other)]
    for target, source in ((name, other), (other, name)):
        for reused in (values[source], f" {values[source]}\n"):  # 앞뒤 공백만 다른 값도 같은 비밀이다
            monkeypatch.setenv(target, reused)
            get_settings.cache_clear()
            with pytest.raises(RuntimeError, match=expected) as error:
                get_settings()
            assert values[source] not in str(error.value)
            assert [n for n in others if n in str(error.value)] == []  # 같은 값을 쓴 쌍만 알린다
        monkeypatch.setenv(target, values[target])


def test_production_names_all_ten_reused_pairs_and_development_still_starts(monkeypatch, caplog):
    production(monkeypatch)
    shared = base64.b64encode(os.urandom(32)).decode()  # 다섯 변수 어디에나 넣을 수 있는 값 (마스터 키 형식)
    for name in FIVE_SECRETS:
        monkeypatch.setenv(name, shared)
    with pytest.raises(RuntimeError) as error:
        get_settings()
    assert str(error.value).startswith(
        "APP_ENV=production requires HOLDER_KEY_ENCRYPTION_KEY (not the same value as DXWORKS_API_KEY), "
        "JWT_SECRET (not the same value as DXWORKS_API_KEY), "
        "SERVICE_API_KEY (not the same value as DXWORKS_API_KEY), "
        "AI_SERVICE_TOKEN (not the same value as DXWORKS_API_KEY), "
        "JWT_SECRET (not the same value as HOLDER_KEY_ENCRYPTION_KEY), "
        "SERVICE_API_KEY (not the same value as HOLDER_KEY_ENCRYPTION_KEY), "
        "AI_SERVICE_TOKEN (not the same value as HOLDER_KEY_ENCRYPTION_KEY), "
        "SERVICE_API_KEY (not the same value as JWT_SECRET), AI_SERVICE_TOKEN (not the same value as JWT_SECRET), "
        "AI_SERVICE_TOKEN (not the same value as SERVICE_API_KEY); ")
    assert shared not in str(error.value)

    monkeypatch.setenv("APP_ENV", "development")  # development는 지금과 같다 (다섯 값이 모두 같아도 경고 없이 시작한다)
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        settings = get_settings()
    assert [r.getMessage() for r in caplog.records if r.name == "app.core.config"] == []
    assert settings.dxworks_api_key == settings.jwt_secret == shared
    assert settings.service_api_key == settings.ai_service_token == shared
    assert settings.holder_key_encryption_key == base64.b64decode(shared)


def test_production_accepts_five_distinct_secrets_and_skips_empty_ones(monkeypatch):
    production(monkeypatch)
    values = distinct_five_secrets(monkeypatch)
    settings = get_settings()
    assert settings.holder_key_encryption_key == base64.b64decode(values["HOLDER_KEY_ENCRYPTION_KEY"])
    assert (settings.dxworks_api_key, settings.jwt_secret, settings.service_api_key, settings.ai_service_token) == (
        values["DXWORKS_API_KEY"], values["JWT_SECRET"], values["SERVICE_API_KEY"], values["AI_SERVICE_TOKEN"])
    # 비운 값은 비교하지 않는다: DXWORKS_API_KEY·SERVICE_API_KEY·AI_SERVICE_TOKEN이 모두 비어도(공백뿐이어도) 시작한다.
    for name, value in (("DXWORKS_API_KEY", ""), ("SERVICE_API_KEY", " "), ("AI_SERVICE_TOKEN", "\n")):
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    assert get_settings().app_env == "production"


# ── production: Google OAuth 설정 ───────────────────────────────────

HTTPS_CALLBACK = "https://app.example.com/auth/google/callback"
DEV_CALLBACK = "http://localhost:5173/auth/google/callback"


def google_configured(monkeypatch, **overrides) -> dict:
    """production 검사를 통과하는 Google 설정 (시크릿은 실행할 때마다 새로 만든다). 값이 None인 변수는 지운다."""
    values = {"GOOGLE_CLIENT_ID": "1234-test.apps.googleusercontent.com",
              "GOOGLE_CLIENT_SECRET": base64.b64encode(os.urandom(32)).decode(),
              "GOOGLE_REDIRECT_URI": HTTPS_CALLBACK, "GOOGLE_ALLOWED_REDIRECT_URIS": None, **overrides}
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    return values


def google_unset(monkeypatch) -> None:
    google_configured(monkeypatch, GOOGLE_CLIENT_ID=None, GOOGLE_CLIENT_SECRET=None, GOOGLE_REDIRECT_URI=None)


def test_google_redirect_default_is_for_development_only(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    google_unset(monkeypatch)
    settings = get_settings()  # 로컬 개발은 지금처럼 FE 개발 서버 콜백이 기본값이다
    assert settings.google_redirect_uri == DEV_CALLBACK and settings.google_allowed_redirect_uris == [DEV_CALLBACK]

    # 회귀: production에서도 localhost 콜백이 기본값으로 허용 목록에 들어가 있었다.
    production(monkeypatch)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.google_redirect_uri == "" and settings.google_allowed_redirect_uris == []
    assert settings.google_client_id == "" and settings.google_client_secret == ""
    # Google 로그인을 켜지 않았으면 콜백 변수만 남아 있어도 시작한다 (기능은 503).
    monkeypatch.setenv("GOOGLE_ALLOWED_REDIRECT_URIS", f"{DEV_CALLBACK}, {HTTPS_CALLBACK}")
    get_settings.cache_clear()
    assert get_settings().google_allowed_redirect_uris == [DEV_CALLBACK, HTTPS_CALLBACK]


def test_production_accepts_a_complete_google_configuration(monkeypatch):
    production(monkeypatch)
    other = "https://admin.example.com/auth/google/callback"
    values = google_configured(monkeypatch, GOOGLE_ALLOWED_REDIRECT_URIS=f" {other} ,{HTTPS_CALLBACK}")
    settings = get_settings()
    assert settings.google_client_id == values["GOOGLE_CLIENT_ID"]
    assert settings.google_client_secret == values["GOOGLE_CLIENT_SECRET"]
    assert settings.google_redirect_uri == HTTPS_CALLBACK
    assert settings.google_allowed_redirect_uris == [HTTPS_CALLBACK, other]


@pytest.mark.parametrize("missing_name", ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"])
def test_production_requires_google_client_id_and_secret_together(monkeypatch, missing_name):
    # 한쪽만 넣으면 요청마다 503이 된다. 시작할 때 두 이름을 알린다 (값은 넣지 않는다).
    production(monkeypatch)
    for blank in (None, "", "  "):
        values = google_configured(monkeypatch, **{missing_name: blank})
        with pytest.raises(RuntimeError, match=r"^APP_ENV=production requires GOOGLE_CLIENT_ID and "
                                               r"GOOGLE_CLIENT_SECRET \(set both, or leave both empty\); ") as error:
            get_settings()
        for value in (values["GOOGLE_CLIENT_ID"], values["GOOGLE_CLIENT_SECRET"]):
            assert not value or not value.strip() or value not in str(error.value)

    monkeypatch.setenv("APP_ENV", "development")  # development는 지금처럼 시작한다 (그 기능만 503)
    get_settings.cache_clear()
    assert get_settings().app_env == "development"


def test_production_google_requires_an_explicit_redirect_uri(monkeypatch):
    # 회귀: 키만 넣은 운영 설정은 localhost 콜백으로 시작했고, FE의 운영 콜백은 400 'Invalid redirect_uri'였다.
    production(monkeypatch)
    expected = (r"^APP_ENV=production requires GOOGLE_REDIRECT_URI "
                r"\(set explicitly when Google OAuth is configured\); ")
    for blank in (None, "", "  "):
        google_configured(monkeypatch, GOOGLE_REDIRECT_URI=blank)
        with pytest.raises(RuntimeError, match=expected):
            get_settings()
    # 허용 목록에 https 주소가 있어도 기본 콜백은 명시해야 한다.
    google_configured(monkeypatch, GOOGLE_REDIRECT_URI=None, GOOGLE_ALLOWED_REDIRECT_URIS=HTTPS_CALLBACK)
    with pytest.raises(RuntimeError, match=expected):
        get_settings()


NOT_HTTPS = [("http://app.example.com/auth/google/callback", None), (DEV_CALLBACK, None),
             ("HTTP://app.example.com/auth/google/callback", None), ("app.example.com/auth/google/callback", None),
             (HTTPS_CALLBACK, "http://app.example.com/auth/google/callback"), (HTTPS_CALLBACK, DEV_CALLBACK),
             (HTTPS_CALLBACK, f"https://admin.example.com/cb,{DEV_CALLBACK}"),
             # 회귀: 'https://'로 시작하는지만 봐서 호스트가 없는 값도 통과했다. 이제 주소로 읽어 호스트가 있어야 한다.
             ("https://", None), ("https:///auth/google/callback", None), ("https://:443/auth/google/callback", None),
             ("https:app.example.com/auth/google/callback", None), (HTTPS_CALLBACK, "https://?next=example.com"),
             ("https://[app.example.com/auth/google/callback", None)]


@pytest.mark.parametrize(("redirect_uri", "allowed"), NOT_HTTPS, ids=[f"uri-{index}" for index in range(len(NOT_HTTPS))])
def test_production_google_refuses_redirect_uris_that_are_not_https(monkeypatch, redirect_uri, allowed):
    # http:// 콜백은 인가 코드를 평문으로 실어 보내고, localhost 콜백은 사용자 PC의 localhost로 코드를 보낸다.
    production(monkeypatch)
    google_configured(monkeypatch, GOOGLE_REDIRECT_URI=redirect_uri, GOOGLE_ALLOWED_REDIRECT_URIS=allowed)
    with pytest.raises(RuntimeError, match=r"^APP_ENV=production requires GOOGLE_REDIRECT_URI and "
                                           r"GOOGLE_ALLOWED_REDIRECT_URIS \(https:// addresses only\); ") as error:
        get_settings()
    for private in ("example.com", "localhost"):  # 주소는 싣지 않는다
        assert private not in str(error.value)

    monkeypatch.setenv("APP_ENV", "development")  # development는 지금처럼 그대로 받는다
    get_settings.cache_clear()
    assert redirect_uri in get_settings().google_allowed_redirect_uris


@pytest.mark.parametrize("redirect_uri", ["HTTPS://app.example.com/auth/google/callback",
                                          "Https://app.example.com:8443/auth/google/callback",
                                          "https://[2001:db8::1]/auth/google/callback"])
def test_production_google_reads_the_redirect_uri_as_a_url(monkeypatch, redirect_uri):
    # 회귀: 스킴을 글자 그대로 비교해 'HTTPS://…'(스킴은 대소문자를 가리지 않는다)로 적은 https 콜백을 거부했다.
    production(monkeypatch)
    google_configured(monkeypatch, GOOGLE_REDIRECT_URI=redirect_uri, GOOGLE_ALLOWED_REDIRECT_URIS=HTTPS_CALLBACK)
    settings = get_settings()
    assert settings.google_redirect_uri == redirect_uri  # 값은 적은 그대로 쓴다 (콜백 주소는 글자 그대로 비교한다)
    assert settings.google_allowed_redirect_uris == [redirect_uri, HTTPS_CALLBACK]


def test_production_names_every_google_problem_at_once(monkeypatch):
    production(monkeypatch)
    google_configured(monkeypatch, GOOGLE_CLIENT_SECRET=None, GOOGLE_REDIRECT_URI=None,
                      GOOGLE_ALLOWED_REDIRECT_URIS=DEV_CALLBACK)
    with pytest.raises(RuntimeError) as error:
        get_settings()
    assert str(error.value).startswith(
        "APP_ENV=production requires GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (set both, or leave both empty), "
        "GOOGLE_REDIRECT_URI (set explicitly when Google OAuth is configured), "
        "GOOGLE_REDIRECT_URI and GOOGLE_ALLOWED_REDIRECT_URIS (https:// addresses only); ")


# GOOGLE_CLIENT_SECRET도 교환할 때마다 Google로 보내는 값이라 다른 비밀값과 달라야 한다. (바꿀 값, 같으면 안 되는 값)
REUSED_WITH_GOOGLE = [("GOOGLE_CLIENT_SECRET", "DXWORKS_API_KEY"), ("HOLDER_KEY_ENCRYPTION_KEY", "GOOGLE_CLIENT_SECRET"),
                      ("JWT_SECRET", "GOOGLE_CLIENT_SECRET"), ("SERVICE_API_KEY", "GOOGLE_CLIENT_SECRET"),
                      ("AI_SERVICE_TOKEN", "GOOGLE_CLIENT_SECRET")]


@pytest.mark.parametrize(("name", "other"), REUSED_WITH_GOOGLE,
                         ids=[f"{name}-{other}" for name, other in REUSED_WITH_GOOGLE])
def test_production_refuses_a_secret_shared_with_the_google_client_secret(monkeypatch, name, other):
    # 회귀: 다섯 비밀값만 비교해, GOOGLE_CLIENT_SECRET이 JWT_SECRET과 같아도(Google Cloud 프로젝트를 보는 사람이 사용자
    # 토큰을 만들 수 있다) 시작했다. 어느 쪽 값을 다른 쪽에 넣어도 같은 한 줄로 두 이름만 알린다.
    production(monkeypatch)
    values = distinct_five_secrets(monkeypatch)
    values["GOOGLE_CLIENT_SECRET"] = google_configured(monkeypatch)["GOOGLE_CLIENT_SECRET"]
    expected = rf"^APP_ENV=production requires {name} \(not the same value as {other}\); "
    others = [n for n in (*FIVE_SECRETS, "GOOGLE_CLIENT_SECRET", "DB_PASSWORD") if n not in (name, other)]
    for target, source in ((name, other), (other, name)):
        for reused in (values[source], f" {values[source]}\n"):  # 앞뒤 공백만 다른 값도 같은 비밀이다
            monkeypatch.setenv(target, reused)
            get_settings.cache_clear()
            with pytest.raises(RuntimeError, match=expected) as error:
                get_settings()
            assert values[source] not in str(error.value)
            assert [n for n in others if n in str(error.value)] == []  # 같은 값을 쓴 쌍만 알린다
        monkeypatch.setenv(target, values[target])


def test_production_names_all_fifteen_reused_pairs_with_the_google_secret(monkeypatch, caplog):
    production(monkeypatch)
    shared = base64.b64encode(os.urandom(32)).decode()  # 여섯 변수 어디에나 넣을 수 있는 값 (마스터 키 형식)
    google_configured(monkeypatch, GOOGLE_CLIENT_SECRET=shared)
    for name in FIVE_SECRETS:
        monkeypatch.setenv(name, shared)
    with pytest.raises(RuntimeError) as error:
        get_settings()
    assert str(error.value).startswith(
        "APP_ENV=production requires GOOGLE_CLIENT_SECRET (not the same value as DXWORKS_API_KEY), "
        "HOLDER_KEY_ENCRYPTION_KEY (not the same value as DXWORKS_API_KEY), "
        "JWT_SECRET (not the same value as DXWORKS_API_KEY), "
        "SERVICE_API_KEY (not the same value as DXWORKS_API_KEY), "
        "AI_SERVICE_TOKEN (not the same value as DXWORKS_API_KEY), "
        "HOLDER_KEY_ENCRYPTION_KEY (not the same value as GOOGLE_CLIENT_SECRET), "
        "JWT_SECRET (not the same value as GOOGLE_CLIENT_SECRET), "
        "SERVICE_API_KEY (not the same value as GOOGLE_CLIENT_SECRET), "
        "AI_SERVICE_TOKEN (not the same value as GOOGLE_CLIENT_SECRET), "
        "JWT_SECRET (not the same value as HOLDER_KEY_ENCRYPTION_KEY), "
        "SERVICE_API_KEY (not the same value as HOLDER_KEY_ENCRYPTION_KEY), "
        "AI_SERVICE_TOKEN (not the same value as HOLDER_KEY_ENCRYPTION_KEY), "
        "SERVICE_API_KEY (not the same value as JWT_SECRET), AI_SERVICE_TOKEN (not the same value as JWT_SECRET), "
        "AI_SERVICE_TOKEN (not the same value as SERVICE_API_KEY); ")
    assert shared not in str(error.value)

    monkeypatch.setenv("APP_ENV", "development")  # development는 지금과 같다 (같은 값이어도 경고 없이 시작한다)
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        settings = get_settings()
    assert [r.getMessage() for r in caplog.records if r.name == "app.core.config"] == []
    assert settings.google_client_secret == settings.jwt_secret == shared


# ── GitHub·네이버 OAuth 설정 ─────────────────────────────────────────

GITHUB_NAVER = [("GITHUB", "GitHub", "github"), ("NAVER", "Naver", "naver")]


def provider_configured(monkeypatch, prefix: str, **overrides) -> dict:
    """production 검사를 통과하는 GitHub·네이버 설정 (시크릿은 실행할 때마다 새로 만든다). 값이 None인 변수는 지운다."""
    values = {f"{prefix}_CLIENT_ID": f"{prefix.lower()}-client-id-0123",
              f"{prefix}_CLIENT_SECRET": base64.b64encode(os.urandom(32)).decode(),
              f"{prefix}_REDIRECT_URI": f"https://app.example.com/auth/{prefix.lower()}/callback", **overrides}
    for name, value in values.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    return values


@pytest.mark.parametrize("prefix,label,name", GITHUB_NAVER)
def test_github_and_naver_redirect_default_is_for_development_only(monkeypatch, prefix, label, name):
    monkeypatch.delenv("APP_ENV", raising=False)
    provider_configured(monkeypatch, prefix, **{f"{prefix}_CLIENT_ID": None, f"{prefix}_CLIENT_SECRET": None,
                                                f"{prefix}_REDIRECT_URI": None})
    settings = get_settings()
    assert getattr(settings, f"{name}_redirect_uri") == f"http://localhost:5173/auth/{name}/callback"
    assert getattr(settings, f"{name}_client_id") == "" and getattr(settings, f"{name}_client_secret") == ""
    # 비워 둔 변수는 비운 그대로다 (기본값으로 대신하지 않는다 — Google과 같다).
    monkeypatch.setenv(f"{prefix}_REDIRECT_URI", " ")
    get_settings.cache_clear()
    assert getattr(get_settings(), f"{name}_redirect_uri") == ""

    production(monkeypatch)
    monkeypatch.delenv(f"{prefix}_REDIRECT_URI")
    get_settings.cache_clear()
    assert getattr(get_settings(), f"{name}_redirect_uri") == ""  # production은 localhost 기본값을 쓰지 않는다


@pytest.mark.parametrize("prefix,label,name", GITHUB_NAVER)
def test_production_accepts_a_complete_github_or_naver_configuration(monkeypatch, prefix, label, name):
    production(monkeypatch)
    values = provider_configured(monkeypatch, prefix, **{f"{prefix}_REDIRECT_URI": "HTTPS://app.example.com/cb"})
    settings = get_settings()
    assert getattr(settings, f"{name}_client_id") == values[f"{prefix}_CLIENT_ID"]
    assert getattr(settings, f"{name}_client_secret") == values[f"{prefix}_CLIENT_SECRET"]
    assert getattr(settings, f"{name}_redirect_uri") == "HTTPS://app.example.com/cb"  # 주소로 읽고, 값은 적은 그대로


@pytest.mark.parametrize("prefix,label,name", GITHUB_NAVER)
def test_production_github_or_naver_needs_id_secret_together_and_an_explicit_https_redirect(monkeypatch, prefix, label,
                                                                                             name):
    production(monkeypatch)
    head = "^APP_ENV=production requires "
    both = rf"{head}{prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET \(set both, or leave both empty\); "
    explicit = rf"{head}{prefix}_REDIRECT_URI \(set explicitly when {label} OAuth is configured\); "
    https = rf"{head}{prefix}_REDIRECT_URI \(https:// address only\); "
    for missing in (f"{prefix}_CLIENT_ID", f"{prefix}_CLIENT_SECRET"):
        for blank in (None, "", "  "):
            values = provider_configured(monkeypatch, prefix, **{missing: blank})
            with pytest.raises(RuntimeError, match=both) as error:
                get_settings()
            for value in values.values():
                assert not value or not value.strip() or value not in str(error.value)  # 이름만 알리고 값은 넣지 않는다
    for blank in (None, "", "  "):
        provider_configured(monkeypatch, prefix, **{f"{prefix}_REDIRECT_URI": blank})
        with pytest.raises(RuntimeError, match=explicit):
            get_settings()
    for uri in (f"http://app.example.com/auth/{name}/callback", f"http://localhost:5173/auth/{name}/callback",
                "https://", f"https:///auth/{name}/callback", "app.example.com/cb"):
        provider_configured(monkeypatch, prefix, **{f"{prefix}_REDIRECT_URI": uri})
        with pytest.raises(RuntimeError, match=https) as error:
            get_settings()
        assert "example.com" not in str(error.value) and "localhost" not in str(error.value)
    # 모두 빠지면 한 번에 알린다. 다른 제공자 설정·Google 설정은 섞이지 않는다.
    provider_configured(monkeypatch, prefix, **{f"{prefix}_CLIENT_ID": None, f"{prefix}_REDIRECT_URI": None})
    with pytest.raises(RuntimeError) as error:
        get_settings()
    assert str(error.value).startswith(
        f"APP_ENV=production requires {prefix}_CLIENT_ID and {prefix}_CLIENT_SECRET (set both, or leave both empty), "
        f"{prefix}_REDIRECT_URI (set explicitly when {label} OAuth is configured); ")
    assert "GOOGLE_" not in str(error.value)
    # 켜지 않았으면(ID·시크릿 모두 비움) 콜백 주소가 http여도 시작한다. development는 지금처럼 그대로 받는다.
    provider_configured(monkeypatch, prefix, **{f"{prefix}_CLIENT_ID": "", f"{prefix}_CLIENT_SECRET": "",
                                                f"{prefix}_REDIRECT_URI": "http://app.example.com/cb"})
    assert get_settings().app_env == "production"
    provider_configured(monkeypatch, prefix,
                        **{f"{prefix}_CLIENT_SECRET": None, f"{prefix}_REDIRECT_URI": "http://x/cb"})
    monkeypatch.setenv("APP_ENV", "development")
    get_settings.cache_clear()
    assert get_settings().app_env == "development"


# GitHub·네이버 시크릿도 각 제공자로 보내고 그 콘솔(OAuth App 설정) 관리자가 볼 수 있는 값이라 다른 비밀값과 달라야 한다.
REUSED_WITH_GITHUB_OR_NAVER = [
    ("GITHUB_CLIENT_SECRET", "DXWORKS_API_KEY"), ("GITHUB_CLIENT_SECRET", "GOOGLE_CLIENT_SECRET"),
    ("NAVER_CLIENT_SECRET", "GITHUB_CLIENT_SECRET"), ("HOLDER_KEY_ENCRYPTION_KEY", "NAVER_CLIENT_SECRET"),
    ("JWT_SECRET", "GITHUB_CLIENT_SECRET"), ("SERVICE_API_KEY", "NAVER_CLIENT_SECRET"),
    ("AI_SERVICE_TOKEN", "GITHUB_CLIENT_SECRET"), ("NAVER_CLIENT_SECRET", "DXWORKS_API_KEY"),
]


@pytest.mark.parametrize(("name", "other"), REUSED_WITH_GITHUB_OR_NAVER,
                         ids=[f"{name}-{other}" for name, other in REUSED_WITH_GITHUB_OR_NAVER])
def test_production_refuses_a_secret_shared_with_a_github_or_naver_secret(monkeypatch, name, other):
    production(monkeypatch)
    values = distinct_five_secrets(monkeypatch)
    values["GOOGLE_CLIENT_SECRET"] = google_configured(monkeypatch)["GOOGLE_CLIENT_SECRET"]
    for prefix in ("GITHUB", "NAVER"):
        values[f"{prefix}_CLIENT_SECRET"] = provider_configured(monkeypatch, prefix)[f"{prefix}_CLIENT_SECRET"]
    assert get_settings().app_env == "production"  # 대조: 모두 다르면 시작한다
    expected = rf"^APP_ENV=production requires {name} \(not the same value as {other}\); "
    names = (*FIVE_SECRETS, "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_SECRET", "NAVER_CLIENT_SECRET", "DB_PASSWORD")
    others = [n for n in names if n not in (name, other)]
    for target, source in ((name, other), (other, name)):
        for reused in (values[source], f" {values[source]}\n"):  # 앞뒤 공백만 다른 값도 같은 비밀이다
            monkeypatch.setenv(target, reused)
            get_settings.cache_clear()
            with pytest.raises(RuntimeError, match=expected) as error:
                get_settings()
            assert values[source] not in str(error.value)
            assert [n for n in others if n in str(error.value)] == []  # 같은 값을 쓴 쌍만 알린다
        monkeypatch.setenv(target, values[target])


def test_production_names_all_twenty_eight_reused_pairs(monkeypatch):
    production(monkeypatch)
    shared = base64.b64encode(os.urandom(32)).decode()  # 여덟 변수 어디에나 넣을 수 있는 값 (마스터 키 형식)
    google_configured(monkeypatch, GOOGLE_CLIENT_SECRET=shared)
    for prefix in ("GITHUB", "NAVER"):
        provider_configured(monkeypatch, prefix, **{f"{prefix}_CLIENT_SECRET": shared})
    for name in FIVE_SECRETS:
        monkeypatch.setenv(name, shared)
    with pytest.raises(RuntimeError) as error:
        get_settings()
    order = ["DXWORKS_API_KEY", "GOOGLE_CLIENT_SECRET", "GITHUB_CLIENT_SECRET", "NAVER_CLIENT_SECRET",
             "HOLDER_KEY_ENCRYPTION_KEY", "JWT_SECRET", "SERVICE_API_KEY", "AI_SERVICE_TOKEN"]
    pairs = ", ".join(f"{order[j]} (not the same value as {order[i]})"
                      for i in range(len(order)) for j in range(i + 1, len(order)))
    assert str(error.value).startswith(f"APP_ENV=production requires {pairs}; ")
    assert str(error.value).count("not the same value as") == 28
    assert shared not in str(error.value)


def test_oauth_timeout_prefers_the_new_name_and_falls_back_to_the_google_name(monkeypatch):
    # 세 제공자가 같이 쓰는 OAUTH_TIMEOUT_SECONDS. 없거나 비었으면 예전 .env·작업 정의의 GOOGLE_OAUTH_TIMEOUT_SECONDS를 읽는다.
    monkeypatch.delenv("OAUTH_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("GOOGLE_OAUTH_TIMEOUT_SECONDS", "25")
    assert get_settings().oauth_timeout_seconds == 25.0
    for blank in ("", "  "):
        monkeypatch.setenv("OAUTH_TIMEOUT_SECONDS", blank)
        get_settings.cache_clear()
        assert get_settings().oauth_timeout_seconds == 25.0
    monkeypatch.setenv("OAUTH_TIMEOUT_SECONDS", "12")
    get_settings.cache_clear()
    assert get_settings().oauth_timeout_seconds == 12.0  # 새 이름이 있으면 그 값 (예전 이름은 보지 않는다)
    monkeypatch.setenv("GOOGLE_OAUTH_TIMEOUT_SECONDS", "not-a-number")
    get_settings.cache_clear()
    assert get_settings().oauth_timeout_seconds == 12.0
    monkeypatch.setenv("OAUTH_TIMEOUT_SECONDS", "0")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match=r"^OAUTH_TIMEOUT_SECONDS must be a number between 1 and 120$"):
        get_settings()
    monkeypatch.delenv("OAUTH_TIMEOUT_SECONDS")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match=r"^GOOGLE_OAUTH_TIMEOUT_SECONDS must be a number between 1 and 120$"):
        get_settings()
    monkeypatch.delenv("GOOGLE_OAUTH_TIMEOUT_SECONDS")
    get_settings.cache_clear()
    assert get_settings().oauth_timeout_seconds == 10.0
    assert not hasattr(get_settings(), "google_oauth_timeout_seconds")  # 세 어댑터가 같은 값을 쓴다


# ── production: CORS_ORIGINS ────────────────────────────────────────

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def test_cors_origins_default_is_for_development_only(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert get_settings().cors_origins == DEV_ORIGINS  # 로컬 개발은 지금처럼 FE 개발 서버 주소

    # 회귀: production에서도 비우면 localhost 개발 서버 주소를 허용했다. 이제 다른 출처를 허용하지 않는다.
    production(monkeypatch)
    for value in (None, "", "  ", " , "):
        if value is None:
            monkeypatch.delenv("CORS_ORIGINS", raising=False)
        else:
            monkeypatch.setenv("CORS_ORIGINS", value)
        get_settings.cache_clear()
        assert get_settings().cors_origins == []


@pytest.mark.parametrize(("value", "origins"), [
    ("https://app.example.com", ["https://app.example.com"]),
    (" https://a.example ,https://b.example:8443,", ["https://a.example", "https://b.example:8443"]),
    # http:// 출처는 막지 않는다: 운영 이미지(APP_ENV=production)를 로컬·스테이징 FE와 함께 띄울 수 있어야 한다.
    ("http://localhost:5173,http://staging.example.com", ["http://localhost:5173", "http://staging.example.com"]),
    ("http://[::1]:5173", ["http://[::1]:5173"]),
])
def test_production_accepts_plain_cors_origins(monkeypatch, value, origins):
    production(monkeypatch)
    monkeypatch.setenv("CORS_ORIGINS", value)
    assert get_settings().cors_origins == origins


BAD_ORIGINS = ["*", "null", "https://app.example.com/", "https://app.example.com/app", "https://app.example.com?x=1",
               "https://app.example.com#top", "https://app.example.com?", "https://user:hunter2@app.example.com",
               "https://*.example.com", "app.example.com", "ftp://app.example.com", "https://",
               "https://app.example.com:port", "https://app .example.com",
               "https://ok.example.com,*", "https://ok.example.com, https://app.example.com/"]


@pytest.mark.parametrize("value", BAD_ORIGINS, ids=[f"origin-{index}" for index in range(len(BAD_ORIGINS))])
def test_production_refuses_cors_origins_that_match_everything_or_nothing(monkeypatch, value):
    # 회귀: 운영에서 `*`(모든 출처)·`null`과, 끝에 /가 붙어 어느 출처와도 맞지 않는 주소(다른 출처의 FE가 조용히 막힌다)를
    # 그대로 받았다. 빠진 값과 같은 오류로 변수 이름만 알린다.
    production(monkeypatch)
    monkeypatch.setenv("CORS_ORIGINS", value)
    with pytest.raises(RuntimeError, match=r"^APP_ENV=production requires CORS_ORIGINS \(scheme://host\[:port\] "
                                           r"entries only: no \*, null, path or trailing slash\); ") as error:
        get_settings()
    for private in ("example", "hunter2"):
        assert private not in str(error.value)

    monkeypatch.setenv("APP_ENV", "development")  # development는 지금처럼 그대로 받는다
    get_settings.cache_clear()
    assert get_settings().cors_origins == [item.strip() for item in value.split(",") if item.strip()]


# ── 액세스 토큰 수명 ───────────────────────────────────────────────

@pytest.mark.parametrize("value", ["0", "-5", "1441", "525600", "abc", "1.5"])
def test_jwt_lifetime_must_be_between_one_minute_and_one_day(monkeypatch, value):
    monkeypatch.setenv("JWT_EXPIRES_MINUTES", value)
    with pytest.raises(RuntimeError, match=r"^JWT_EXPIRES_MINUTES must be an integer between 1 and 1440$"):
        get_settings()


@pytest.mark.parametrize(("value", "minutes"), [(None, 1440), ("", 1440), (" 480 ", 480), ("1", 1), ("1440", 1440)])
def test_jwt_lifetime_default_and_allowed_values(monkeypatch, value, minutes):
    if value is None:
        monkeypatch.delenv("JWT_EXPIRES_MINUTES", raising=False)
    else:
        monkeypatch.setenv("JWT_EXPIRES_MINUTES", value)
    assert get_settings().jwt_expires_minutes == minutes


# ── 예전 AUTH_REQUIRED 설정 ───────────────────────────────────────

@pytest.mark.parametrize("app_env", ["development", "production"])
def test_legacy_auth_required_false_cannot_disable_auth(monkeypatch, caplog, app_env):
    # 예전 전환 기간 설정(AUTH_REQUIRED=false)이 남은 .env·작업 정의: 인증을 끄지 못하고 경고만 한 번 남긴다.
    production(monkeypatch)
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        settings = get_settings()
        get_settings()  # 캐시된 설정: 경고를 다시 남기지 않는다
    assert not hasattr(settings, "auth_required")  # 인증을 끄는 설정 자체가 없다
    assert "auth_required" not in {f.name for f in dataclasses.fields(Settings)}
    assert caplog.text.count("AUTH_REQUIRED=false") == 1

    for value in (None, "", "true"):  # 없거나 비었거나 true면 조용하다
        if value is None:
            monkeypatch.delenv("AUTH_REQUIRED")
        else:
            monkeypatch.setenv("AUTH_REQUIRED", value)
        get_settings.cache_clear()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="app.core.config"):
            get_settings()
        assert "AUTH_REQUIRED" not in caplog.text


# ── 없앤 카카오 로그인 설정이 남은 경우 ────────────────────────────────

LEFTOVER_KAKAO_WARNING = "KAKAO_* 설정은 더 이상 쓰지 않습니다 (카카오 로그인 제거). .env에서 지웁니다."


@pytest.mark.parametrize("app_env", ["development", "production"])
@pytest.mark.parametrize("name", ["KAKAO_CLIENT_ID", "KAKAO_CLIENT_SECRET"])
def test_leftover_kakao_settings_are_ignored_with_one_warning(monkeypatch, caplog, app_env, name):
    # 카카오 로그인을 없앤 뒤에도 값이 남은 .env·작업 정의: 서버는 그대로 시작하고, 값은 쓰지도 로그에 남기지도 않으며
    # 지우라는 경고만 한 번 남긴다 (AUTH_REQUIRED 남은 줄과 같은 방식).
    production(monkeypatch)
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.delenv("MOCK_ISSUER_ENABLED", raising=False)  # production의 모의 발급 경고가 섞이지 않게 기본값(끔)으로 둔다
    value = secrets.token_urlsafe(24)  # 실행할 때마다 새로 만든 값 (로그에 나오면 안 된다)
    monkeypatch.setenv(name, f" {value} ")
    monkeypatch.setenv("KAKAO_REDIRECT_URI", "http://app.example.com/auth/kakao/callback")  # production 검사도 하지 않는다
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        settings = get_settings()
        get_settings()  # 캐시된 설정: 경고를 다시 남기지 않는다
    assert settings.app_env == app_env
    assert [r.getMessage() for r in caplog.records if r.name == "app.core.config"] == [LEFTOVER_KAKAO_WARNING]
    assert value not in caplog.text and value not in repr(settings)
    assert not [f.name for f in dataclasses.fields(Settings) if "kakao" in f.name]  # 설정 객체에 카카오 필드가 없다

    for blank in (None, "", "   "):  # 없거나 비었으면 조용하다 (콜백 주소만 남은 줄도 조용하다)
        if blank is None:
            monkeypatch.delenv(name)
        else:
            monkeypatch.setenv(name, blank)
        get_settings.cache_clear()
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="app.core.config"):
            get_settings()
        assert "KAKAO" not in caplog.text


# ── 모의 발급 ─────────────────────────────────────────────────────

def test_mock_issuer_defaults_on_in_development_and_off_in_production(monkeypatch, caplog):
    monkeypatch.delenv("MOCK_ISSUER_ENABLED", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    assert get_settings().mock_issuer_enabled is True  # 로컬 개발은 지금처럼 켜져 있다
    monkeypatch.setenv("MOCK_ISSUER_ENABLED", "false")
    get_settings.cache_clear()
    assert get_settings().mock_issuer_enabled is False  # 명시한 값이 이긴다

    production(monkeypatch)
    monkeypatch.setenv("MOCK_ISSUER_ENABLED", " ")  # .env.example처럼 비워 두면 APP_ENV 기본값
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        assert get_settings().mock_issuer_enabled is False  # 운영 이미지(APP_ENV=production)는 명시해야 켜진다
    assert "MOCK_ISSUER_ENABLED" not in caplog.text

    monkeypatch.setenv("MOCK_ISSUER_ENABLED", "true")
    get_settings.cache_clear()
    with caplog.at_level(logging.WARNING, logger="app.core.config"):
        assert get_settings().mock_issuer_enabled is True
    assert "MOCK_ISSUER_ENABLED=true (production)" in caplog.text  # 운영에서 켜면 시작할 때 경고


def test_mock_issuer_template_allowlist_and_limit(monkeypatch):
    monkeypatch.setenv("DXWORKS_BADGE_TEMPLATE_ID", " tpl-a ")
    monkeypatch.setenv("MOCK_ISSUER_TEMPLATE_IDS", "tpl-b, ,tpl-c,tpl-a")
    monkeypatch.delenv("MOCK_ISSUER_DAILY_LIMIT", raising=False)
    settings = get_settings()
    assert settings.mock_issuer_template_ids == frozenset({"tpl-a", "tpl-b", "tpl-c"})
    assert settings.dxworks_badge_template_id == "tpl-a"
    assert settings.mock_issuer_daily_limit == 10
    for value, limit in ((" ", 10), ("", 10), ("1", 1), (" 25 ", 25)):  # 비우면(.env.example) 기본값
        monkeypatch.setenv("MOCK_ISSUER_DAILY_LIMIT", value)
        get_settings.cache_clear()
        assert get_settings().mock_issuer_daily_limit == limit

    monkeypatch.setenv("DXWORKS_BADGE_TEMPLATE_ID", "")
    monkeypatch.delenv("MOCK_ISSUER_TEMPLATE_IDS")
    get_settings.cache_clear()
    assert get_settings().mock_issuer_template_ids == frozenset()  # 템플릿 설정이 없으면 아무것도 허용하지 않는다

    monkeypatch.setenv("MOCK_ISSUER_TEMPLATE_IDS", "tpl-only-extra")
    get_settings.cache_clear()
    assert get_settings().mock_issuer_template_ids == frozenset({"tpl-only-extra"})


@pytest.mark.parametrize("value", ["0", "-1", "abc", "2.5"])
def test_mock_issuer_daily_limit_must_be_positive(monkeypatch, value):
    monkeypatch.setenv("MOCK_ISSUER_DAILY_LIMIT", value)
    with pytest.raises(RuntimeError, match=r"^MOCK_ISSUER_DAILY_LIMIT must be an integer >= 1$"):
        get_settings()


def test_mock_issuer_global_daily_limit_default_and_range(monkeypatch):
    monkeypatch.delenv("MOCK_ISSUER_GLOBAL_DAILY_LIMIT", raising=False)
    assert get_settings().mock_issuer_global_daily_limit == 200
    for value, limit in ((" ", 200), ("", 200), ("1", 1), (" 5000 ", 5000)):  # 비우면(.env.example) 기본값
        monkeypatch.setenv("MOCK_ISSUER_GLOBAL_DAILY_LIMIT", value)
        get_settings.cache_clear()
        assert get_settings().mock_issuer_global_daily_limit == limit
    for value in ("0", "-1", "abc", "2.5"):
        monkeypatch.setenv("MOCK_ISSUER_GLOBAL_DAILY_LIMIT", value)
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match=r"^MOCK_ISSUER_GLOBAL_DAILY_LIMIT must be an integer >= 1$"):
            get_settings()


def test_db_max_connections_default_and_range(monkeypatch):
    # 프로세스가 동시에 여는 앱 DB 연결 수의 상한. 서버는 시작할 때(get_settings), DB 접근자는 처음 쓸 때 같은 값을 읽는다.
    from app.core.config import db_max_connections

    monkeypatch.delenv("DB_MAX_CONNECTIONS", raising=False)
    assert get_settings().db_max_connections == db_max_connections() == 20
    for value, limit in ((" ", 20), ("", 20), ("2", 2), (" 50 ", 50)):  # 비우면(.env.example) 기본값
        monkeypatch.setenv("DB_MAX_CONNECTIONS", value)
        get_settings.cache_clear()
        assert get_settings().db_max_connections == db_max_connections() == limit
    for value in ("1", "0", "-1", "abc", "2.5"):
        monkeypatch.setenv("DB_MAX_CONNECTIONS", value)
        get_settings.cache_clear()
        for read in (get_settings, db_max_connections):
            with pytest.raises(RuntimeError, match=r"^DB_MAX_CONNECTIONS must be an integer >= 2$"):
                read()


def test_ai_max_concurrency_default_and_range(monkeypatch):
    monkeypatch.delenv("AI_MAX_CONCURRENCY", raising=False)
    assert get_settings().ai_max_concurrency == 4
    for value, limit in ((" ", 4), ("", 4), ("1", 1), (" 16 ", 16)):
        monkeypatch.setenv("AI_MAX_CONCURRENCY", value)
        get_settings.cache_clear()
        assert get_settings().ai_max_concurrency == limit
    for value in ("0", "-1", "abc", "2.5"):
        monkeypatch.setenv("AI_MAX_CONCURRENCY", value)
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match=r"^AI_MAX_CONCURRENCY must be an integer >= 1$"):
            get_settings()


def test_curriculum_llm_daily_limit_default_and_range(monkeypatch):
    # 허용된 사용자 한 명의 24시간 LLM 모드 학습 계획 수 (외부 LLM 비용·한도 보호). 비우면 20.
    monkeypatch.delenv("CURRICULUM_LLM_DAILY_LIMIT", raising=False)
    assert get_settings().curriculum_llm_daily_limit == 20
    for value, limit in ((" ", 20), ("", 20), ("1", 1), (" 100 ", 100)):
        monkeypatch.setenv("CURRICULUM_LLM_DAILY_LIMIT", value)
        get_settings.cache_clear()
        assert get_settings().curriculum_llm_daily_limit == limit
    for value in ("0", "-1", "abc", "2.5"):
        monkeypatch.setenv("CURRICULUM_LLM_DAILY_LIMIT", value)
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match=r"^CURRICULUM_LLM_DAILY_LIMIT must be an integer >= 1$"):
            get_settings()


# ── DXWorks 접속 설정: 넣었으면 형식·범위가 맞아야 한다 ────────────────────

def test_identity_verification_is_off_unless_enabled(monkeypatch):
    monkeypatch.delenv("IDENTITY_VERIFICATION_ENABLED", raising=False)
    for app_env in ("development", "production"):
        production(monkeypatch)
        monkeypatch.setenv("APP_ENV", app_env)
        get_settings.cache_clear()
        assert get_settings().identity_verification_enabled is False  # 어느 환경이든 명시해야 켜진다
    for value, enabled in ((" ", False), ("false", False), ("0", False), ("true", True), ("1", True)):
        monkeypatch.setenv("IDENTITY_VERIFICATION_ENABLED", value)
        get_settings.cache_clear()
        assert get_settings().identity_verification_enabled is enabled


@pytest.mark.parametrize("value,seconds", [(None, 15.0), ("", 15.0), ("  ", 15.0), ("15", 15.0), (" 2.5 ", 2.5),
                                           ("1", 1.0), ("120", 120.0), ("1e1", 10.0)])
def test_dxworks_timeout_default_and_allowed_values(monkeypatch, value, seconds):
    if value is None:
        monkeypatch.delenv("DXWORKS_TIMEOUT_SECONDS", raising=False)
    else:
        monkeypatch.setenv("DXWORKS_TIMEOUT_SECONDS", value)  # 비우면(.env.example에서 지운 줄) 기본값
    assert get_settings().dxworks_timeout_seconds == seconds


@pytest.mark.parametrize("value", ["0", "0.5", "-5", "121", "1e9", "inf", "-inf", "nan", "NaN", "abc", "15s", "1,5"])
def test_dxworks_timeout_must_be_a_finite_number_in_range(monkeypatch, value):
    # 회귀: float()로만 읽어 0·음수(모든 호출 실패), inf(시간 제한 없음), nan(호출마다 500)이 그대로 통과했고,
    # 숫자가 아니면 변수 이름 없는 ValueError였다.
    monkeypatch.setenv("DXWORKS_TIMEOUT_SECONDS", value)
    with pytest.raises(RuntimeError, match=r"^DXWORKS_TIMEOUT_SECONDS must be a number between 1 and 120$"):
        get_settings()


def test_env_float_names_the_variable_and_keeps_the_default(monkeypatch):
    from app.core.config import _env_float

    monkeypatch.delenv("SOME_TIMEOUT_SECONDS", raising=False)
    assert _env_float("SOME_TIMEOUT_SECONDS", 7.5, minimum=0.5, maximum=10) == 7.5
    monkeypatch.setenv("SOME_TIMEOUT_SECONDS", "0.5")
    assert _env_float("SOME_TIMEOUT_SECONDS", 7.5, minimum=0.5, maximum=10) == 0.5
    for value in ("0.49", "10.01", "nan", "inf", "x"):
        monkeypatch.setenv("SOME_TIMEOUT_SECONDS", value)
        with pytest.raises(RuntimeError, match=r"^SOME_TIMEOUT_SECONDS must be a number between 0\.5 and 10$"):
            _env_float("SOME_TIMEOUT_SECONDS", 7.5, minimum=0.5, maximum=10)


@pytest.mark.parametrize("name,field,default,maximum", [
    ("OAUTH_TIMEOUT_SECONDS", "oauth_timeout_seconds", 10.0, 120),
    ("GOOGLE_OAUTH_TIMEOUT_SECONDS", "oauth_timeout_seconds", 10.0, 120),  # 예전 이름 (OAUTH_TIMEOUT_SECONDS가 없을 때)
    ("AI_TIMEOUT_SECONDS", "ai_timeout_seconds", 30.0, 600),
    ("AI_GENERATION_TIMEOUT_SECONDS", "ai_generation_timeout_seconds", 90.0, 600),
])
def test_oauth_and_ai_timeouts_default_when_blank_and_must_be_in_range(monkeypatch, name, field, default, maximum):
    # 회귀: float()로만 읽어 `AI_TIMEOUT_SECONDS=`처럼 비워 둔 줄이 변수 이름 없는 ValueError traceback이었고,
    # 0·음수(모든 호출 실패)·inf(시간 제한 없음)·nan은 그대로 통과했다.
    for oauth_name in ("OAUTH_TIMEOUT_SECONDS", "GOOGLE_OAUTH_TIMEOUT_SECONDS"):
        monkeypatch.delenv(oauth_name, raising=False)
    monkeypatch.delenv(name, raising=False)
    assert getattr(get_settings(), field) == default
    for value, seconds in (("", default), ("  ", default), (" 45 ", 45.0), ("1", 1.0), (str(maximum), float(maximum))):
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        assert getattr(get_settings(), field) == seconds
    for value in ("0", "0.5", "-5", str(maximum + 1), "inf", "nan", "abc", "30s"):
        monkeypatch.setenv(name, value)
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match=rf"^{name} must be a number between 1 and {maximum}$"):
            get_settings()


def test_log_level_defaults_to_info_when_missing_or_blank(monkeypatch):
    # 회귀: `LOG_LEVEL=`로 비워 둔 줄이 있으면 서버·마이그레이션 실행기가 "Unknown level: ''" traceback으로 뜨지 않았다.
    from app.core.config import log_level

    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert log_level() == "INFO"
    for value, level in (("", "INFO"), ("   ", "INFO"), ("debug", "DEBUG"), (" Warning ", "WARNING"), ("INFO", "INFO"),
                         ("error", "ERROR"), ("Critical", "CRITICAL")):
        monkeypatch.setenv("LOG_LEVEL", value)
        assert log_level() == level


@pytest.mark.parametrize("value", ["verbose", "10", "NOTSET", "warn", "FATAL", "ＩＮＦＯ", "INFO,DEBUG", "trace\n"])
def test_log_level_refuses_unknown_names_without_echoing_them(monkeypatch, value):
    # 회귀(E1): 없는 수준 이름은 logging.basicConfig의 'Unknown level' ValueError traceback으로 서버·마이그레이션 실행기를
    # 막았다. 이제 받는 다섯 이름 밖의 값은 RuntimeError(변수 이름과 받는 값만)다. logging만 아는 별칭(NOTSET: 루트가 모든
    # 수준을 남김, WARN·FATAL)도 받지 않는다.
    from app.core.config import LOG_LEVELS, log_level

    monkeypatch.setenv("LOG_LEVEL", value)
    with pytest.raises(RuntimeError) as error:
        log_level()
    assert str(error.value) == "LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL (empty means INFO)"
    assert LOG_LEVELS == ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    if value != "INFO,DEBUG":  # 받는 이름 두 개를 이은 값이라 두 이름은 메시지에도 있다
        assert value.strip() not in str(error.value)


def test_server_ends_a_non_utf8_secret_with_one_line_without_the_byte():
    # E1 감사: 비밀값 환경변수에 UTF-8이 아닌 바이트가 들어 있으면 get_settings()의 .encode("utf-8")가 UnicodeEncodeError를 내고
    # traceback(그 바이트와 위치가 든 문구)으로 끝났다. 이제 고정 문구 한 줄과 종료 코드 1이다 (POSIX 환경변수의 \xff 바이트는
    # 파이썬에서 '\udcff'로 읽힌다).
    secret = secrets.token_urlsafe(40)
    env = {**os.environ, "APP_ENV": "production", "JWT_SECRET": secret[:5] + "\udcff" + secret[5:],
           "DB_PASSWORD": secrets.token_urlsafe(24), "SERVICE_API_KEY": "", "PYTHONDONTWRITEBYTECODE": "1",
           **PRODUCTION_DB_TLS_ENV}
    result = subprocess.run([sys.executable, "-B", "-c", "import main"], cwd=BE_DIR, env=env,
                            capture_output=True, text=True, errors="backslashreplace", timeout=120)
    assert _one_error_line(result) == ("A setting value is not valid UTF-8 text (check the secret and environment values"
                                       " for stray bytes)")
    assert secret[:5] not in result.stderr and "udcff" not in result.stderr.lower() and "\\xff" not in result.stderr


@pytest.mark.parametrize("app_env", ["development", "production"])
def test_malformed_master_key_is_a_one_line_config_error_without_the_value(monkeypatch, app_env):
    # 회귀(E1): 형식이 틀린 HOLDER_KEY_ENCRYPTION_KEY는 get_settings()에서 ValueError로 나가 main.py가 잡지 못하고(설정 오류는
    # RuntimeError만 잡음) traceback으로 끝났다. 이제 다른 설정 오류처럼 RuntimeError 한 줄이고, base64 디코더 오류는 잇지 않는다.
    key = base64.b64encode(os.urandom(32)).decode()
    if app_env == "production":
        production(monkeypatch)
    for value, message in ((key.rstrip("=") + "AB", "HOLDER_KEY_ENCRYPTION_KEY must be base64"),
                           ("키" + key, "HOLDER_KEY_ENCRYPTION_KEY must be base64"),
                           (f"HOLDER_KEY_ENCRYPTION_KEY={key}", "HOLDER_KEY_ENCRYPTION_KEY must decode to 32 bytes"),
                           (base64.b64encode(os.urandom(16)).decode(), "HOLDER_KEY_ENCRYPTION_KEY must decode to 32 bytes")):
        monkeypatch.setenv("HOLDER_KEY_ENCRYPTION_KEY", value)
        get_settings.cache_clear()
        with pytest.raises(RuntimeError) as error:
            get_settings()
        assert type(error.value) is RuntimeError and str(error.value) == message
        assert error.value.__cause__ is None and error.value.__suppress_context__
        assert key.rstrip("=") not in str(error.value) and value not in str(error.value)


@pytest.mark.parametrize("host,stored", [
    ("", ""), ("  ", ""),  # 비워 두면 지금처럼 시작한다 (DXWorks 기능만 503)
    ("https://dx.partner.invalid", "https://dx.partner.invalid"),
    (" https://dx.partner.invalid/ ", "https://dx.partner.invalid"),
    ("https://dx.partner.invalid/gateway/", "https://dx.partner.invalid/gateway"),
    ("http://127.0.0.1:8999", "http://127.0.0.1:8999"), ("http://localhost:8999", "http://localhost:8999"),
])
@pytest.mark.parametrize("app_env", ["development", "production"])
def test_dxworks_host_may_be_empty_or_a_plain_https_url(monkeypatch, app_env, host, stored):
    production(monkeypatch)
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("DXWORKS_API_HOST", host)
    monkeypatch.setenv("DXWORKS_API_KEY", "")  # 키도 비워 둘 수 있다
    settings = get_settings()
    assert settings.dxworks_api_host == stored and settings.dxworks_api_key == ""


BAD_HOSTS = ["http://dx.partner.invalid", "dx.partner.invalid", "ftp://dx.partner.invalid", "https://",
             "https://user:hunter2@dx.partner.invalid", "https://user@dx.partner.invalid",
             "https://dx.partner.invalid?tenant=secret-tenant", "https://dx.partner.invalid/#secret-frag",
             "https://dx.partner .invalid", "https://dx.partner.invalid/a\tb"]


@pytest.mark.parametrize("host", BAD_HOSTS, ids=[f"host-{index}" for index in range(len(BAD_HOSTS))])
def test_malformed_dxworks_host_refuses_to_start_without_echoing_it(monkeypatch, host):
    # 회귀: 호출할 때만 확인해 잘못된 주소로도 서버가 떴고(요청마다 503), user:pw@ 주소는 Basic 인증 헤더와 함께 나갔으며
    # ?쿼리가 붙은 주소는 다른 경로를 불렀다. 오류에는 변수 이름만 넣는다 (주소에 든 비밀번호·토큰을 로그에 남기지 않는다).
    monkeypatch.setenv("DXWORKS_API_HOST", host)
    with pytest.raises(RuntimeError, match=r"^DXWORKS_API_HOST must be an https:// URL") as error:
        get_settings()
    for secret in ("hunter2", "secret-tenant", "secret-frag", "dx.partner"):
        assert secret not in str(error.value)


def test_dxworks_api_key_must_be_ascii(monkeypatch):
    # 회귀: ASCII가 아닌 키는 헤더에 넣을 수 없어 발급·신원 검증이 500(UnicodeEncodeError)이었다.
    monkeypatch.setenv("DXWORKS_API_KEY", "키-값-secret")
    with pytest.raises(RuntimeError, match=r"^DXWORKS_API_KEY must contain only ASCII characters$") as error:
        get_settings()
    assert "secret" not in str(error.value)
    monkeypatch.setenv("DXWORKS_API_KEY", " plain-ascii-key ")
    get_settings.cache_clear()
    assert get_settings().dxworks_api_key == "plain-ascii-key"


# ── 설정 객체 repr에 비밀값 숨김 ─────────────────────────────────────

def test_secret_like_settings_fields_are_hidden_from_repr():
    # 새 비밀값 필드를 추가하면서 field(repr=False)를 빠뜨리지 않게 이름(…_secret/_key/_token/_password)으로도 확인한다.
    hidden = {f.name for f in dataclasses.fields(Settings) if not f.repr}
    assert hidden == {"dxworks_api_key", "jwt_secret", "service_api_key", "holder_key_encryption_key",
                      "google_client_secret", "github_client_secret", "naver_client_secret", "ai_service_token"}
    assert {f.name for f in dataclasses.fields(Settings)
            if re.search(r"(secret|key|token|password)$", f.name)} <= hidden


@pytest.mark.parametrize("app_env", ["development", "production"])
def test_settings_repr_never_contains_secret_values(monkeypatch, app_env):
    # 설정 객체를 로그(%s·%r)·f-문자열·pprint로 찍어도 비밀값이 나오지 않는다. 값은 실행할 때마다 새로 만든다.
    production(monkeypatch)
    monkeypatch.setenv("APP_ENV", app_env)
    fake = {
        "JWT_SECRET": secrets.token_urlsafe(48),
        "DXWORKS_API_KEY": secrets.token_urlsafe(24),
        "SERVICE_API_KEY": secrets.token_urlsafe(32),
        "AI_SERVICE_TOKEN": secrets.token_urlsafe(40),
        "GOOGLE_CLIENT_SECRET": secrets.token_urlsafe(24),
        "GITHUB_CLIENT_SECRET": secrets.token_urlsafe(24),
        "NAVER_CLIENT_SECRET": secrets.token_urlsafe(24),
    }
    master = os.urandom(32)
    for name, value in fake.items():
        monkeypatch.setenv(name, value)
    # production은 시크릿만 넣은 OAuth 설정을 받지 않는다: ID와 https 콜백 주소도 함께 넣는다.
    for provider in ("google", "github", "naver"):
        monkeypatch.setenv(f"{provider.upper()}_CLIENT_ID", f"1234-test-{provider}-client-id")
        monkeypatch.setenv(f"{provider.upper()}_REDIRECT_URI", f"https://app.example.com/auth/{provider}/callback")
    monkeypatch.setenv("HOLDER_KEY_ENCRYPTION_KEY", base64.b64encode(master).decode())

    settings = get_settings()
    rendered = "\n".join([repr(settings), str(settings), f"{settings}", "%s %r" % (settings, settings),
                          pprint.pformat(settings, width=20)])
    for value in [*fake.values(), repr(master), master.hex(), base64.b64encode(master).decode()]:
        assert value not in rendered
    # 코드가 쓰는 속성 값과 비밀이 아닌 설정의 repr은 그대로다.
    assert settings.jwt_secret == fake["JWT_SECRET"] and settings.ai_service_token == fake["AI_SERVICE_TOKEN"]
    assert settings.dxworks_api_key == fake["DXWORKS_API_KEY"]
    assert settings.service_api_key == fake["SERVICE_API_KEY"]
    assert settings.holder_key_encryption_key == master
    assert f"app_env='{app_env}'" in repr(settings) and "mock_issuer_daily_limit=" in repr(settings)


# ── DB 접속 ────────────────────────────────────────────────────────

def test_db_connections_fail_fast_with_connect_timeout(monkeypatch):
    from app.services import database

    seen: dict = {}
    monkeypatch.setattr(database.psycopg, "connect", lambda **kwargs: seen.update(kwargs))
    monkeypatch.delenv("DB_CONNECT_TIMEOUT", raising=False)
    monkeypatch.delenv("PGCONNECT_TIMEOUT", raising=False)
    database.get_db_connection()
    assert seen["connect_timeout"] == 10  # psycopg 기본값 130초 대신

    monkeypatch.setenv("PGCONNECT_TIMEOUT", "7")  # libpq 표준 변수도 지금처럼 따른다
    database.get_db_connection()
    assert seen["connect_timeout"] == 7

    monkeypatch.setenv("DB_CONNECT_TIMEOUT", "3")
    database.get_db_connection()
    assert seen["connect_timeout"] == 3
    assert seen["autocommit"] is True  # 나머지 접속 방식은 그대로


def test_blank_db_port_means_the_default_port(monkeypatch):
    # 회귀: `DB_PORT=`로 비워 둔 줄이 있으면 서버는 뜨고 /health도 200인데 DB를 쓰는 요청마다 500이었고(int('')),
    # 마이그레이션 실행기는 traceback으로 끝났다.
    from app.services import database

    seen: dict = {}
    monkeypatch.setattr(database.psycopg, "connect", lambda **kwargs: seen.update(kwargs))
    for value, port in (("", 5432), ("  ", 5432), ("6543", 6543), (" 6543 ", 6543)):
        monkeypatch.setenv("DB_PORT", value)
        database.get_db_connection()
        assert seen["port"] == port
    monkeypatch.delenv("DB_PORT")
    database.get_db_connection()
    assert seen["port"] == 5432


def test_production_db_connection_refuses_missing_or_default_password(monkeypatch):
    # 앱 요청과 마이그레이션 실행기가 쓰는 get_db_connection 자체도 막는다 (get_settings()를 거치지 않는 경로).
    from app.services import database

    seen: dict = {}
    monkeypatch.setattr(database.psycopg, "connect", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setenv("APP_ENV", "production")
    for value in (None, "", "  ", "password", " password "):
        if value is None:
            monkeypatch.delenv("DB_PASSWORD", raising=False)
        else:
            monkeypatch.setenv("DB_PASSWORD", value)
        with pytest.raises(RuntimeError, match=r"^APP_ENV=production requires DB_PASSWORD ") as error:
            database.get_db_connection()
        assert "password" not in str(error.value)
    assert not seen  # 공개된 기본 비밀번호로는 접속을 시도하지도 않는다

    monkeypatch.setenv("DB_PASSWORD", TEST_DB_PASSWORD)
    for name, value in PRODUCTION_DB_TLS_ENV.items():  # production은 DB TLS도 검사한다 (아래 DB TLS 테스트)
        monkeypatch.setenv(name, value)
    database.get_db_connection()
    assert seen["password"] == TEST_DB_PASSWORD

    seen.clear()
    monkeypatch.setenv("APP_ENV", "staging")  # 잘못된 APP_ENV도 앱과 같은 오류로 접속하지 않는다
    with pytest.raises(RuntimeError, match="APP_ENV must be one of"):
        database.get_db_connection()
    assert not seen

    monkeypatch.delenv("APP_ENV")  # development: 지금처럼 없으면 로컬 기본값, 있으면 그 값 그대로
    monkeypatch.delenv("DB_PASSWORD")
    database.get_db_connection()
    assert seen["password"] == "password"
    monkeypatch.setenv("DB_PASSWORD", " spaced ")
    database.get_db_connection()
    assert seen["password"] == " spaced "


# ── DB TLS (PGSSLMODE·PGSSLROOTCERT) ─────────────────────────────────

PGSSLMODE_RULE = "PGSSLMODE (verify-full or verify-ca in production)"
PGSSLROOTCERT_RULE = "PGSSLROOTCERT (readable CA bundle file)"
SETTINGS_TAIL = "; set the same values on every task/worker (e.g. AWS Secrets Manager or SSM)"


def _set_or_delete(monkeypatch, name: str, value) -> None:
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("value", [None, "", "  ", "prefer", "require", "disable", "allow", "VERIFY-FULL", "Verify-CA",
                                   "verify_full", "verify-full verify-ca", "system"],
                         ids=lambda value: repr(value))
def test_production_refuses_a_pgsslmode_that_does_not_verify_the_server(monkeypatch, value):
    # 없거나 비었거나 서버 인증서를 확인하지 않는 값(libpq 기본값 prefer·require 등)이면 시작하지 않는다. 값은 libpq가 받는
    # 글자 그대로 비교한다 (대문자는 libpq도 받지 않는다). 메시지에는 이름만 있고 값은 없다 (메시지 전체를 비교한다).
    production(monkeypatch)
    _set_or_delete(monkeypatch, "PGSSLMODE", value)
    with pytest.raises(RuntimeError) as error:
        get_settings()
    assert str(error.value) == f"APP_ENV=production requires {PGSSLMODE_RULE}{SETTINGS_TAIL}"


@pytest.mark.parametrize("value,expected", [("verify-full", "verify-full"), ("verify-ca", "verify-ca"),
                                            (" verify-full\n", "verify-full"), ("\tverify-ca ", "verify-ca")])
def test_production_accepts_verifying_sslmodes_and_passes_them_trimmed_to_libpq(monkeypatch, value, expected):
    from app.services import database

    production(monkeypatch)
    monkeypatch.setenv("PGSSLMODE", value)
    assert get_settings().app_env == "production"
    seen: dict = {}
    monkeypatch.setattr(database.psycopg, "connect", lambda **kwargs: seen.update(kwargs))
    database.get_db_connection()
    assert seen["sslmode"] == expected
    # CA 묶음 경로는 접속 인자로 넘기지 않고 libpq가 PGSSLROOTCERT를 그대로 읽는다 (한글 경로도 접속 문자열에 들어가지 않는다).
    assert "sslrootcert" not in seen and os.environ["PGSSLROOTCERT"] == str(RDS_CA_BUNDLE)


def test_production_refuses_a_pgsslrootcert_that_is_not_a_readable_file(monkeypatch, tmp_path):
    # 이미지 기본값(/app/certs/rds-global-bundle.pem)을 작업 정의에서 비우거나 틀린 경로·디렉터리·읽을 수 없는 파일로 바꾸면
    # verify-full 접속이 모두 실패하므로(서버는 뜨고 /health 200, DB 요청마다 500) 시작할 때 거부한다. 경로는 메시지에 없다.
    # libpq가 읽는 값 그대로 본다: 끝 줄바꿈·앞뒤 공백이 붙은 경로도 libpq는 열지 못하므로 거부한다.
    production(monkeypatch)
    unreadable = tmp_path / "unreadable-ca.pem"
    unreadable.write_bytes(RDS_CA_BUNDLE.read_bytes())
    unreadable.chmod(0)
    values = [None, "", "   ", "system", str(tmp_path / "missing-ca.pem"), str(tmp_path), f"{RDS_CA_BUNDLE}\n",
              f" {RDS_CA_BUNDLE}"]
    if os.geteuid() != 0:  # root는 권한과 관계없이 읽는다
        values.append(str(unreadable))
    for value in values:
        _set_or_delete(monkeypatch, "PGSSLROOTCERT", value)
        get_settings.cache_clear()
        with pytest.raises(RuntimeError) as error:
            get_settings()
        assert str(error.value) == f"APP_ENV=production requires {PGSSLROOTCERT_RULE}{SETTINGS_TAIL}", value
    unreadable.chmod(0o600)


def test_production_names_both_db_tls_problems_after_the_db_password_and_development_ignores_them(monkeypatch):
    production(monkeypatch)
    monkeypatch.setenv("DB_PASSWORD", "password")
    monkeypatch.setenv("PGSSLMODE", "require")
    monkeypatch.setenv("PGSSLROOTCERT", "/nonexistent/rds-ca.pem")
    with pytest.raises(RuntimeError) as error:
        get_settings()
    assert str(error.value) == ("APP_ENV=production requires DB_PASSWORD (not empty and not the local development "
                                f"default), {PGSSLMODE_RULE}, {PGSSLROOTCERT_RULE}{SETTINGS_TAIL}")
    assert "/nonexistent" not in str(error.value)
    # development는 DB TLS를 검사하지 않는다 (로컬 Compose·테스트 DB는 TLS가 없다).
    for value in (None, "", "disable", "VERIFY-FULL"):
        monkeypatch.setenv("APP_ENV", "development")
        _set_or_delete(monkeypatch, "PGSSLMODE", value)
        monkeypatch.delenv("PGSSLROOTCERT", raising=False)
        get_settings.cache_clear()
        assert get_settings().app_env == "development"


def test_production_db_connection_refuses_db_tls_settings_before_connecting(monkeypatch):
    # get_settings()를 거치지 않는 경로(마이그레이션 실행기·시장 통계 배치·시작 점검)도 접속 전에 막는다. 이름만 알린다.
    from app.services import database

    seen: dict = {}
    monkeypatch.setattr(database.psycopg, "connect", lambda **kwargs: seen.update(kwargs))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DB_PASSWORD", TEST_DB_PASSWORD)
    for mode, root, expected in ((None, str(RDS_CA_BUNDLE), PGSSLMODE_RULE),
                                 ("prefer", str(RDS_CA_BUNDLE), PGSSLMODE_RULE),
                                 ("verify-full", "/nonexistent/rds-ca.pem", PGSSLROOTCERT_RULE),
                                 ("", "", f"{PGSSLMODE_RULE}, {PGSSLROOTCERT_RULE}")):
        _set_or_delete(monkeypatch, "PGSSLMODE", mode)
        monkeypatch.setenv("PGSSLROOTCERT", root)
        with pytest.raises(RuntimeError) as error:
            database.get_db_connection()
        assert str(error.value) == f"APP_ENV=production requires {expected}"
    assert not seen  # 인증서를 확인하지 않는 설정으로는 접속을 시도하지도 않는다


@pytest.mark.parametrize("mode,root,expected", [
    (None, None, {"sslmode": "prefer"}),  # 로컬 Compose·테스트: libpq 기본값 그대로
    # 회귀: 빈 값이 libpq에 그대로 가서 모든 접속이 'invalid sslmode value: ""'였다. 빈 경로는 libpq도 없는 것으로 본다.
    ("", "", {"sslmode": "prefer", "sslrootcert": ""}),
    ("  ", "  ", {"sslmode": "prefer", "sslrootcert": ""}),  # 공백 경로를 libpq가 환경변수에서 대신 읽지 않게 덮는다
    (" require ", None, {"sslmode": "require"}),  # 회귀: 앞뒤 공백 때문에 'invalid sslmode value'였다
    ("disable", "/certs/ca.pem", {"sslmode": "disable"}),  # 경로는 libpq가 환경변수에서 읽는다
    (None, str(RDS_CA_BUNDLE), {"sslmode": "prefer"}),  # 이미지 기본값 + PGSSLMODE 없음 (로컬 Compose)
], ids=["unset", "blank", "spaces", "require-trimmed", "disable-with-root", "image-default-root"])
def test_development_db_connection_ignores_blank_tls_variables(monkeypatch, mode, root, expected):
    from app.services import database

    seen: dict = {}
    monkeypatch.setattr(database.psycopg, "connect", lambda **kwargs: seen.update(kwargs))
    monkeypatch.delenv("APP_ENV", raising=False)
    _set_or_delete(monkeypatch, "PGSSLMODE", mode)
    _set_or_delete(monkeypatch, "PGSSLROOTCERT", root)
    database.get_db_connection()
    assert {name: seen[name] for name in ("sslmode", "sslrootcert") if name in seen} == expected


def test_tests_never_read_a_developer_dotenv(tmp_path, monkeypatch):
    # 개발자 PC의 BE/.env에는 실제 DXWorks 키·Google 비밀값이 들어 있다. 테스트(main.py의 load_dotenv() 포함)는
    # 그 파일을 읽지 않아야 한다 (conftest의 PYTHON_DOTENV_DISABLED).
    from dotenv import load_dotenv

    env_file = tmp_path / ".env"
    env_file.write_text("VC_TEST_ONLY_FROM_DOTENV=leaked\n", encoding="utf-8")
    monkeypatch.delenv("VC_TEST_ONLY_FROM_DOTENV", raising=False)
    load_dotenv(env_file)
    assert "VC_TEST_ONLY_FROM_DOTENV" not in os.environ
    assert os.environ["DXWORKS_API_HOST"] == "https://dx.invalid"  # 실제 협력사 주소가 아니라 테스트 값이다

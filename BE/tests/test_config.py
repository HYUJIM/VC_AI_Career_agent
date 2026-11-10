"""APP_ENV별 설정 검사와 DB 접속 설정 (DB 없이 설정 값만 본다).

- development(기본, 비어 있어도 같음): 비밀값이 없으면 지금처럼 경고하고 임시 값으로 대신한다. 테스트 값도 그대로 받는다.
- production: JWT_SECRET(32바이트 이상)·HOLDER_KEY_ENCRYPTION_KEY·DB_HOST·DB_NAME·DB_USER·DB_PASSWORD가 없거나,
  이 저장소에 공개된 값·자리표시 값(DB_PASSWORD는 로컬 기본값)이면 서버가 시작하지 않는다.
  SERVICE_API_KEY는 비우거나 32바이트 이상의 임의 값이어야 한다.
  JWT_SECRET·HOLDER_KEY_ENCRYPTION_KEY·SERVICE_API_KEY·AI_SERVICE_TOKEN·DXWORKS_API_KEY는 (설정한 경우) 모두 서로 다른
  값이어야 한다.
- 공통: JWT_EXPIRES_MINUTES는 1~1440, 모의 발급 기본값(development 켬·production 끔)·템플릿 허용 목록·사용자별 한도,
  예전 AUTH_REQUIRED 설정은 경고만 남기고 인증을 끄지 못한다, 설정 객체의 repr에 비밀값이 나오지 않는다.
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


def test_server_refuses_to_start_in_production_without_secrets():
    # main.py는 import 시점(CORS 설정)에 설정을 읽으므로 uvicorn이 앱을 불러오는 순간 실패한다.
    # 빈 값을 명시해 .env가 있어도 채워 넣지 못하게 한다.
    env = {**os.environ, "APP_ENV": "production", "JWT_SECRET": "short-secret-value",
           "HOLDER_KEY_ENCRYPTION_KEY": "", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-c", "import main"], cwd=BE_DIR, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert "RuntimeError: APP_ENV=production requires" in result.stderr
    assert "JWT_SECRET" in result.stderr and "HOLDER_KEY_ENCRYPTION_KEY" in result.stderr
    assert "short-secret-value" not in result.stderr


def test_server_refuses_to_start_in_production_with_the_test_environment():
    # 테스트·로컬 환경 값(conftest 비밀값, 로컬 기본 DB 비밀번호)을 그대로 쓴 운영 작업 정의는 시작하지 않는다.
    env = {**os.environ, "APP_ENV": "production", "DB_PASSWORD": "password", "PYTHONDONTWRITEBYTECODE": "1"}
    published = [env[name] for name in ("JWT_SECRET", "HOLDER_KEY_ENCRYPTION_KEY", "SERVICE_API_KEY")]
    result = subprocess.run([sys.executable, "-c", "import main"], cwd=BE_DIR, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode != 0
    assert "RuntimeError: APP_ENV=production requires" in result.stderr
    for name in ("JWT_SECRET", "HOLDER_KEY_ENCRYPTION_KEY", "SERVICE_API_KEY", "DB_PASSWORD"):
        assert name in result.stderr
    for value in published:
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


# ── 설정 객체 repr에 비밀값 숨김 ─────────────────────────────────────

def test_secret_like_settings_fields_are_hidden_from_repr():
    # 새 비밀값 필드를 추가하면서 field(repr=False)를 빠뜨리지 않게 이름(…_secret/_key/_token/_password)으로도 확인한다.
    hidden = {f.name for f in dataclasses.fields(Settings) if not f.repr}
    assert hidden == {"dxworks_api_key", "jwt_secret", "service_api_key", "holder_key_encryption_key",
                      "ai_service_token"}
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
    }
    master = os.urandom(32)
    for name, value in fake.items():
        monkeypatch.setenv(name, value)
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

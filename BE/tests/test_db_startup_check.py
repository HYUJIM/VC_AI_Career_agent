"""운영 시작 DB 자체 점검(app.services.db_startup_check)과 DB TLS 변수의 실제 접속.

- APP_ENV=production 앱은 lifespan startup에서 요청과 같은 접근자(app_db_connection)로 `SELECT 1`과 users 표 읽기를 한 번
  한다. 통과하면 지금처럼 시작한다.
- 실패하면 RETRY_SECONDS 동안 다시 시도하고(시도마다 경고 한 줄), 그래도 실패하면 lifespan.startup.failed에 한 줄
  (`DB startup check failed: <오류 종류>: <안전한 요약>`)만 싣는다. uvicorn은 그 줄을 남기고 종료 코드 3으로 끝난다.
  요약에는 DB 호스트·포트·사용자·DB 이름·비밀번호·인증서 경로를 넣지 않는다. 설정 오류는 다시 시도하지 않는다.
- development와 production_env 픽스처(ENABLED=False)에서는 하지 않는다. 새 경로(/ready 등)는 없고 /health는 DB를 보지 않는다.
- PGSSLMODE·PGSSLROOTCERT를 비워 둔 줄은 없는 것과 같고(실제 접속), 이미지의 RDS CA 묶음을 PGSSLROOTCERT로 둔 채
  PGSSLMODE 없이(로컬 Compose) TLS 없는 DB에 그대로 접속한다.
DB가 필요한 테스트는 conftest의 테스트 DB(TLS 없음)를 쓰고, 빈 DB가 필요하면 임시 DB를 만들고 지운다.
"""
import asyncio
import base64
import logging
import os
import secrets
import subprocess
import sys
import time
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

import main
from app.core.config import db_tls_options as real_db_tls_options
from app.core.config import get_settings
from app.services import database, db_startup_check
from tests.conftest import BE_DIR, PRODUCTION_DB_TLS_ENV, RDS_CA_BUNDLE, TEST_DB_NAME, _conn_kwargs

FAILED = "DB startup check failed: "


# ── DB TLS 변수와 실제 접속 (development) ─────────────────────────────

def _ssl_in_use() -> bool:
    with database.get_db_connection() as conn:
        return conn.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()["ssl"]


@pytest.mark.parametrize("mode,root", [("", ""), ("  ", "  "), ("", None), (None, ""), (" prefer\n", None)],
                         ids=["both-empty", "both-spaces", "mode-empty", "root-empty", "mode-padded"])
def test_blank_db_tls_variables_are_ignored_by_real_connections_in_development(monkeypatch, mode, root):
    # 회귀: `PGSSLMODE=`로 비워 둔 줄이 있으면 libpq가 'invalid sslmode value: ""'로 모든 접속을 거절했다 (서버는 뜨고
    # /health 200, DB를 쓰는 요청마다 500). 이제 없는 것과 같다: 앱 요청 접근자와 실행기 접근자가 모두 접속한다.
    # 앞뒤 공백·끝 줄바꿈이 붙은 값도 떼고 넘긴다 (예전에는 'invalid sslmode value: " prefer\n"').
    monkeypatch.delenv("APP_ENV", raising=False)
    for name, value in (("PGSSLMODE", mode), ("PGSSLROOTCERT", root)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    assert _ssl_in_use() is False  # 테스트 DB는 TLS가 없다 (libpq 기본값 prefer)
    with database.app_db_connection() as conn:
        assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1


def test_image_ca_bundle_default_does_not_change_local_compose_connections(monkeypatch, tmp_path):
    # 운영 이미지는 PGSSLROOTCERT=/app/certs/rds-global-bundle.pem을 기본값으로 둔다. 로컬 Compose(development, TLS 없는
    # Postgres, PGSSLMODE 없음)에서도 그 값이 그대로 들어가지만 libpq 기본값 prefer라 지금처럼 접속한다.
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("PGSSLMODE", raising=False)
    monkeypatch.setenv("PGSSLROOTCERT", str(RDS_CA_BUNDLE))
    assert _ssl_in_use() is False
    # 한글 경로(이 저장소를 둔 폴더처럼): 경로를 접속 문자열로 넘기면 macOS UTF-8 로캘의 libpq가 "젝"(EC A0 9D)의 0xA0을
    # 공백으로 보고 값을 잘라 'missing "=" after ...'로 실패했다. 경로는 libpq가 환경변수에서 그대로 읽는다.
    korean = tmp_path / "종합설계프로젝트" / "rds-global-bundle.pem"
    korean.parent.mkdir()
    korean.write_bytes(RDS_CA_BUNDLE.read_bytes())
    monkeypatch.setenv("PGSSLROOTCERT", str(korean))
    assert _ssl_in_use() is False


# ── 운영 시작 DB 점검 ────────────────────────────────────────────────

@pytest.fixture
def startup_check(production_env, monkeypatch):
    """production 앱에서 점검을 켠다 (production_env는 끈다). 다시 시도 간격·시간은 짧게 줄인다. 시도 수를 센다."""
    monkeypatch.setattr(db_startup_check, "ENABLED", True)
    monkeypatch.setattr(db_startup_check, "RETRY_SECONDS", 0.6)
    monkeypatch.setattr(db_startup_check, "RETRY_INTERVAL_SECONDS", 0.1)
    attempts: list[float] = []
    real = db_startup_check.check_database

    def counted() -> None:
        attempts.append(time.monotonic())
        real()

    monkeypatch.setattr(db_startup_check, "check_database", counted)
    return attempts


async def _lifespan_startup(app) -> list[dict]:
    """ASGI lifespan startup을 보내고 앱이 보낸 메시지를 돌려준다. 시작했으면 shutdown까지 마친다."""
    inbox: asyncio.Queue = asyncio.Queue()
    sent: list[dict] = []
    started = asyncio.Event()

    async def send(message: dict) -> None:
        sent.append(message)
        if message["type"].startswith("lifespan.startup."):
            started.set()

    await inbox.put({"type": "lifespan.startup"})
    task = asyncio.create_task(app({"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, inbox.get, send))
    await asyncio.wait_for(started.wait(), timeout=30)
    if sent[0]["type"] == "lifespan.startup.complete":
        await inbox.put({"type": "lifespan.shutdown"})
    await asyncio.wait_for(task, timeout=30)
    return sent


def _leaks(text: str, *values: str) -> list[str]:
    return [value for value in values if value and value in text]


def _vc_be_log(caplog) -> str:
    return "\n".join(record.getMessage() for record in caplog.records if record.name == "vc-be")


def test_production_startup_checks_the_database_once_and_then_serves(startup_check):
    # 통과하면 지금처럼 시작한다: 요청과 같은 접근자(계정·TLS 인자·세션 제한)로 한 번만 점검한다.
    with TestClient(main.app) as client:
        assert len(startup_check) == 1
        assert client.get("/health").status_code == 200
        res = client.post("/api/v1/users/login", json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com",
                                                      "password": "password1234"})
        assert res.status_code == 401  # DB를 쓰는 요청도 그대로 (없는 계정)
    assert len(startup_check) == 1


async def test_production_startup_check_sends_startup_complete_to_the_server(startup_check):
    sent = await _lifespan_startup(main.app)
    assert [m["type"] for m in sent] == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    assert len(startup_check) == 1


async def test_unreachable_database_is_retried_then_refused_with_one_safe_line(startup_check, monkeypatch, caplog):
    # RDS가 닿지 않는 태스크(보안 그룹·DB_HOST·DB_PORT 오류): 다시 시도한 뒤 한 줄로 시작을 거부한다. 앱의 lifespan은
    # 시작하지 않는다 (startup.complete 없음). 메시지·경고에는 호스트·포트·사용자·DB 이름이 없다.
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "1")  # 닫힌 포트: 바로 Connection refused
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger="vc-be"):
        sent = await _lifespan_startup(main.app)
    elapsed = time.monotonic() - started
    line = FAILED + "OperationalError: connection refused (check DB_HOST and DB_PORT)"
    assert sent == [{"type": "lifespan.startup.failed", "message": line}]
    assert len(startup_check) >= 3 and elapsed < db_startup_check.RETRY_SECONDS + 5  # 다시 시도하되 제한 안에서 끝난다
    retries = [r.getMessage() for r in caplog.records if r.name == "vc-be" and "DB startup check" in r.getMessage()]
    assert len(retries) == len(startup_check) - 1
    assert all(m.endswith("(OperationalError: connection refused (check DB_HOST and DB_PORT)); retrying in 0.1s")
               for m in retries), retries
    assert _leaks(line + "\n" + _vc_be_log(caplog), "127.0.0.1", "port 1", os.environ["DB_USER"], TEST_DB_NAME) == []


async def test_wrong_password_is_reported_without_the_password(startup_check, monkeypatch, caplog):
    wrong = secrets.token_urlsafe(18)
    monkeypatch.setattr(database, "db_password", lambda: wrong)
    with caplog.at_level(logging.INFO, logger="vc-be"):
        sent = await _lifespan_startup(main.app)
    assert sent == [{"type": "lifespan.startup.failed",
                     "message": FAILED + "OperationalError: password authentication failed (check DB_USER and DB_PASSWORD)"}]
    assert len(startup_check) >= 2
    assert _leaks(sent[0]["message"] + _vc_be_log(caplog), wrong, os.environ["DB_USER"], os.environ["DB_HOST"]) == []
    assert wrong not in caplog.text


async def test_database_without_migrations_is_refused(startup_check, monkeypatch):
    # 마이그레이션 태스크를 돌리지 않은(또는 다른) DB: 접속은 되지만 users 표가 없다.
    empty = f"{TEST_DB_NAME}_startup_{uuid.uuid4().hex[:8]}"

    def admin() -> psycopg.Connection:  # production_env의 PGSSLMODE=verify-full 대신 TLS 없는 테스트 DB에 맞춘다
        return psycopg.connect(**_conn_kwargs("postgres"), sslmode="prefer", autocommit=True)

    with admin() as conn:
        conn.execute(f'CREATE DATABASE "{empty}"')
    try:
        monkeypatch.setenv("DB_NAME", empty)
        sent = await _lifespan_startup(main.app)
    finally:
        with admin() as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{empty}" WITH (FORCE)')
    assert sent == [{"type": "lifespan.startup.failed",
                     "message": FAILED + "UndefinedTable: table users not found (run the migration task first)"}]
    assert empty not in sent[0]["message"]


async def test_verify_full_against_a_server_without_tls_is_refused(startup_check, monkeypatch):
    # 실제 운영 TLS 인자(verify-full + 이미지 CA 묶음)로 TLS가 없는 서버(RDS가 아닌 DB_HOST)에 접속하면 실패한다.
    monkeypatch.setattr(database, "db_tls_options", real_db_tls_options)
    sent = await _lifespan_startup(main.app)
    assert sent == [{"type": "lifespan.startup.failed",
                     "message": FAILED + "OperationalError: server does not support SSL (check DB_HOST and PGSSLMODE)"}]
    assert str(RDS_CA_BUNDLE) not in sent[0]["message"]


async def test_config_errors_at_connect_time_are_not_retried(startup_check, monkeypatch):
    # 설정 오류는 다시 해도 같으므로 한 번만 시도한다. get_settings()가 보지 않는 DB_PORT(숫자 아님)는 값 없이 알린다.
    port = os.environ["DB_PORT"]
    monkeypatch.setenv("DB_PORT", "5432/tcp")
    sent = await _lifespan_startup(main.app)
    assert sent == [{"type": "lifespan.startup.failed",
                     "message": FAILED + "ValueError: DB_PORT and DB_CONNECT_TIMEOUT must be integers"}]
    assert len(startup_check) == 1

    # 접속할 때의 운영 DB TLS 검사(db_tls_options)도 같다. 시작할 때 get_settings()가 먼저 거부하므로 보통은 여기까지 오지
    # 않는다 (설정은 import 때 읽어 두었다).
    startup_check.clear()
    monkeypatch.setenv("DB_PORT", port)
    get_settings()
    monkeypatch.setattr(database, "db_tls_options", real_db_tls_options)
    monkeypatch.delenv("PGSSLMODE")
    sent = await _lifespan_startup(main.app)
    assert sent == [{"type": "lifespan.startup.failed", "message": FAILED + "RuntimeError: APP_ENV=production requires "
                                                                            "PGSSLMODE (verify-full or verify-ca in production)"}]
    assert len(startup_check) == 1


def test_development_and_the_production_env_fixture_do_not_run_the_check(monkeypatch, request):
    # development(테스트 기본값)는 점검하지 않는다. production_env 픽스처는 ENABLED를 끈다 (운영 모드 API 테스트용).
    calls: list[int] = []
    monkeypatch.setattr(db_startup_check, "check_database", lambda: calls.append(1))
    monkeypatch.delenv("APP_ENV", raising=False)
    get_settings.cache_clear()
    assert db_startup_check.ENABLED is True  # 운영 기본값은 켜져 있다 (끄는 환경변수는 없다)
    with TestClient(main.app) as development_client:
        assert development_client.get("/health").status_code == 200
    assert calls == []
    request.getfixturevalue("production_env")
    assert db_startup_check.ENABLED is False
    with TestClient(main.app) as production_client:
        assert production_client.get("/health").status_code == 200
    assert calls == []


def test_no_readiness_route_and_health_does_not_touch_the_database(production_env, monkeypatch):
    # 새 공개 경로는 만들지 않았다: API 밖의 경로는 /health와 (development 전용) 문서 경로뿐이다.
    from tests.test_auth_security import DOCS_PATHS, route_inventory

    assert {path for _, path in route_inventory() if not path.startswith("/api/")} <= {"/health"} | DOCS_PATHS
    # /health는 그대로 DB를 보지 않는다: DB가 닿지 않아도 200이다 (lifespan을 돌리지 않는 클라이언트).
    monkeypatch.setenv("DB_PORT", "1")
    monkeypatch.setattr(database.psycopg, "connect", lambda **kwargs: pytest.fail("/health must not connect"))
    assert TestClient(main.app).get("/health").status_code == 200


# 운영 이미지의 CMD와 같은 uvicorn CLI를 짧은 다시 시도 시간으로 띄운다 (점검 모듈의 상수만 바꾼다).
_UVICORN_PROBE = r"""
import sys
from app.services import db_startup_check
db_startup_check.RETRY_SECONDS = 1.0
db_startup_check.RETRY_INTERVAL_SECONDS = 0.2
from uvicorn.main import main
main(["main:app", "--host", "127.0.0.1", "--port", "0"])
"""


def test_uvicorn_exits_with_one_failure_line_when_the_database_check_fails():
    # ECS 태스크와 같은 경로: 실패 줄 하나 + uvicorn의 "Application startup failed. Exiting." + 종료 코드 3 (traceback 없음).
    # 포트를 열기 전에 끝나므로 상태 확인도 받지 않는다. 비밀값·DB 접속값은 어디에도 나오지 않는다.
    secret_values = {"JWT_SECRET": secrets.token_urlsafe(48), "DB_PASSWORD": secrets.token_urlsafe(24),
                     "HOLDER_KEY_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode()}
    env = {name: value for name, value in os.environ.items()
           if not name.startswith(("PGSSL", "GOOGLE_", "GITHUB_", "NAVER_", "OAUTH_", "CORS_"))}
    env.update({"APP_ENV": "production", "PYTHONDONTWRITEBYTECODE": "1", "SERVICE_API_KEY": "",
                "DB_HOST": "127.0.0.1", "DB_PORT": "1", "DB_USER": "vc_probe_user", **secret_values,
                **PRODUCTION_DB_TLS_ENV})
    result = subprocess.run([sys.executable, "-B", "-c", _UVICORN_PROBE], cwd=BE_DIR, env=env, capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 3, result.stderr[-2000:]
    assert "Traceback" not in result.stderr
    lines = result.stderr.splitlines()
    failed = [line for line in lines if FAILED in line]
    assert failed == ["ERROR:    " + FAILED + "OperationalError: connection refused (check DB_HOST and DB_PORT)"], lines
    assert lines[-1] == "ERROR:    Application startup failed. Exiting."
    retries = [line for line in lines if "DB startup check: attempt" in line]
    assert len(retries) >= 2 and all(line.startswith("WARNING:vc-be:DB startup check: attempt ") for line in retries)
    assert _leaks(result.stderr + result.stdout, *secret_values.values(), "vc_probe_user", "port 1",
                  str(RDS_CA_BUNDLE)) == []


# ── 오류 요약 (libpq 원문 → 안전한 요약) ─────────────────────────────

_HOST = '"rds-prod.c9xyz.ap-northeast-2.rds.amazonaws.com" (10.0.12.34), port 5432'


@pytest.mark.parametrize("message,summary", [
    (f'connection failed: connection to server at {_HOST} failed: root certificate file '
     '"/app/certs/rds-global-bundle.pem" does not exist\nEither provide the file, use the system\'s trusted roots with '
     'sslrootcert=system, or change sslmode to disable server certificate verification.',
     "CA bundle file is missing or unreadable (check PGSSLROOTCERT)"),
    (f'connection failed: connection to server at {_HOST} failed: server certificate for "vc-dep-image-pg" '
     '(and 2 other names) does not match host name "10.0.12.34"',
     "server certificate does not match the host name (set DB_HOST to the RDS endpoint name, not an IP or CNAME)"),
    (f"connection failed: connection to server at {_HOST} failed: SSL error: certificate verify failed",
     "server certificate verification failed (check PGSSLROOTCERT: RDS CA bundle)"),
    (f"connection failed: connection to server at {_HOST} failed: server does not support SSL, but SSL was required",
     "server does not support SSL (check DB_HOST and PGSSLMODE)"),
    (f'connection failed: connection to server at {_HOST} failed: FATAL:  pg_hba.conf rejects connection for host '
     '"10.0.1.9", user "vc_app", database "vc_career_agent", no encryption',
     "connection rejected by pg_hba.conf (check PGSSLMODE: RDS requires SSL)"),
    (f'connection failed: connection to server at {_HOST} failed: FATAL:  password authentication failed for user '
     '"vc_app"', "password authentication failed (check DB_USER and DB_PASSWORD)"),
    (f'connection failed: connection to server at {_HOST} failed: FATAL:  role "vc_app" does not exist',
     "role does not exist (check DB_USER)"),
    (f'connection failed: connection to server at {_HOST} failed: FATAL:  database "vc_career_agent" does not exist',
     "database does not exist (check DB_NAME)"),
    (f"connection failed: connection to server at {_HOST} failed: FATAL:  sorry, too many clients already",
     "too many connections (check RDS max_connections and DB_MAX_CONNECTIONS)"),
    (f"connection failed: connection to server at {_HOST} failed: FATAL:  remaining connection slots are reserved for "
     "roles with the SUPERUSER attribute", "too many connections (check RDS max_connections and DB_MAX_CONNECTIONS)"),
    (f"connection failed: connection to server at {_HOST} failed: FATAL:  the database system is starting up",
     "database server is starting up, shutting down or in recovery"),
    (f"connection failed: connection to server at {_HOST} failed: timeout expired",
     "connection timed out (check DB_HOST, DB_PORT and security groups)"),
    (f"connection failed: connection to server at {_HOST} failed: Connection refused\n\tIs the server running on that "
     "host and accepting TCP/IP connections?", "connection refused (check DB_HOST and DB_PORT)"),
    ('connection failed: could not translate host name "rds-prod.c9xyz.ap-northeast-2.rds.amazonaws.com" to address: '
     "Name or service not known", "host name not resolved (check DB_HOST)"),
    (f"connection failed: connection to server at {_HOST} failed: SSL SYSCALL error: EOF detected",
     "SSL connection failed (check PGSSLMODE and PGSSLROOTCERT)"),
], ids=["root-cert-missing", "host-mismatch", "verify-failed", "no-ssl-server", "pg-hba", "password", "role", "database",
        "too-many", "slots-reserved", "starting-up", "timeout", "refused", "dns", "ssl-other"])
def test_libpq_connection_errors_are_summarized_without_hosts_users_or_paths(message, summary):
    line = db_startup_check.describe_error(psycopg.OperationalError(message))
    assert line == f"OperationalError: {summary}"
    assert "\n" not in line
    assert _leaks(line, "rds-prod", "10.0.", "vc_app", "vc_career_agent", "/app/certs", "vc-dep-image-pg", "5432") == []


@pytest.mark.parametrize("exc,expected", [
    (psycopg.errors.UndefinedTable('relation "users" does not exist'),
     "UndefinedTable: table users not found (run the migration task first)"),
    (psycopg.errors.InsufficientPrivilege("permission denied for table users"),
     "InsufficientPrivilege: permission denied on table users (run the migration task with DB_APP_ROLE set to this "
     "DB_USER)"),
    (psycopg.errors.QueryCanceled("canceling statement due to statement timeout"), "QueryCanceled: query timed out"),
    (psycopg.OperationalError('connection to server at "10.0.12.34" failed: something new'),
     "OperationalError: unclassified database error"),
    (psycopg.errors.UndefinedColumn('column "x" of relation "users" does not exist'), "UndefinedColumn: SQLSTATE 42703"),
    (database.DatabaseBusy("no free DB connection slot within 5s"), "DatabaseBusy: no free DB connection slot"),
    (RuntimeError("APP_ENV=production requires DB_PASSWORD (not empty and not the local development default)"),
     "RuntimeError: APP_ENV=production requires DB_PASSWORD (not empty and not the local development default)"),
    (ValueError("invalid literal for int() with base 10: 'rds-prod:5432'"),
     "ValueError: DB_PORT and DB_CONNECT_TIMEOUT must be integers"),
    (OSError("[Errno 2] No such file or directory: '/secret/path'"), "OSError"),
], ids=["undefined-table", "permission", "query-canceled", "unclassified", "other-sqlstate", "busy", "config", "value",
        "other"])
def test_other_errors_are_summarized_by_kind(exc, expected):
    assert db_startup_check.describe_error(exc) == expected

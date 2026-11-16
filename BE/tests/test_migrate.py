"""DB 마이그레이션 실행기(app.migrate).

빈 DB(빈 RDS) 부트스트랩·원장 기록·다시 실행, 원장 없는 기존 DB(docker-compose initdb로 만든 볼륨) 넘겨받기,
체크섬 검사(CLI 종료 코드 1), 실패한 파일의 롤백, docker-compose initdb 마운트 순서와 실행기 순서의 일치를 본다.
운영(APP_ENV=production) 검사(--seed 거부, 공개된 시드 비밀번호 계정, DB_PASSWORD)와 서비스 계정(DB_APP_ROLE) 권한,
09·10 마이그레이션이 만든 열·표를 쓰는 DB 함수(토큰 버전, 모의 발급 한도)도 본다.
13 마이그레이션(이메일 유일성, 쓰지 않는 표·인덱스 정리)은 예전 버전으로 만든 DB의 업그레이드와 대소문자만 다른 이메일에서 멈추는 것을 본다.
DB가 필요한 테스트는 임시 DB(TEST_DB_NAME + 임의 접미사)를 만들고 끝나면 지운다. 임시 역할도 테스트가 끝나면 지운다.
"""
import hashlib
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import bcrypt
import psycopg
import pytest
from psycopg import sql

from tests.conftest import BE_DIR, TEST_DB_NAME, _conn_kwargs, _connect

# 지금 있는 마이그레이션. 새 파일은 이 뒤에 다음 번호로 붙는다.
KNOWN = ["04_users_password_hash.sql", "05_credentials_raw_token.sql", "06_holder_keys.sql", "07_shares.sql",
         "08_career_curriculum.sql", "09_users_token_version.sql", "10_mock_issuances.sql", "11_oauth_accounts.sql",
         "12_market_statistics.sql", "13_email_unique_drop_unused_tables.sql"]


@pytest.fixture
def scratch_db():
    names: list[str] = []

    def _make(label: str = "mig") -> str:  # label에 대문자를 넣으면 SQL에서 따옴표가 필요한 DB 이름이 된다
        name = f"{TEST_DB_NAME}_{label}_{uuid.uuid4().hex[:8]}"
        with _connect("postgres") as admin:
            admin.execute(f'CREATE DATABASE "{name}"')
        names.append(name)
        return name

    yield _make
    with _connect("postgres") as admin:
        for name in names:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _migrate(dbname: str, **kwargs) -> list[str]:
    from app.migrate import migrate

    return migrate(**kwargs, **_conn_kwargs(dbname))


def _names() -> list[str]:
    from app.migrate import migration_files

    return [path.name for path in migration_files()]


def _scalar(dbname: str, sql: str):
    with _connect(dbname) as conn:
        return conn.execute(sql).fetchone()[0]


def _ledger(dbname: str) -> set[str]:
    with _connect(dbname) as conn:
        return {row[0] for row in conn.execute("SELECT filename FROM public.schema_migrations")}


def _apply_like_initdb(dbname: str, path) -> None:
    """docker-compose initdb(예전 conftest)처럼 원장 없이 파일을 그대로 실행한다."""
    sql = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("\\"))
    with _connect(dbname) as conn:
        conn.execute(sql)


def _catalog(dbname: str) -> set:
    """스키마 비교용 스냅샷: 열·인덱스·제약·ENUM 값."""
    with _connect(dbname) as conn:
        columns = conn.execute(
            "SELECT table_name, column_name, data_type, is_nullable, column_default"
            " FROM information_schema.columns WHERE table_schema = 'public'").fetchall()
        indexes = conn.execute("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'").fetchall()
        constraints = conn.execute(
            "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE connamespace = 'public'::regnamespace").fetchall()
        enums = conn.execute("SELECT t.typname, e.enumlabel, e.enumsortorder FROM pg_enum e"
                             " JOIN pg_type t ON t.oid = e.enumtypid").fetchall()
    return set(columns) | set(indexes) | set(constraints) | set(enums)


def test_empty_database_gets_schema_and_every_migration_once(scratch_db):
    names = _names()
    assert names[:len(KNOWN)] == KNOWN
    db = scratch_db()

    assert _migrate(db) == names  # 빈 DB(빈 RDS): 기본 스키마 + 마이그레이션 전부를 번호 순으로
    assert _ledger(db) == set(names)
    assert _migrate(db) == []  # 다시 실행하면 아무것도 적용하지 않는다
    assert _scalar(db, "SELECT count(*) FROM users") == 0  # --seed가 없으면 테스트 계정을 만들지 않는다 (운영 경로)
    assert _scalar(db, "SELECT to_regclass('public.curricula') IS NOT NULL")
    # 새 DB에는 확장을 만들지 않고(마이그레이션 계정에 확장 권한이 필요 없다) 쓰지 않는 표·중복 인덱스도 없다.
    assert _scalar(db, "SELECT array_agg(extname::text ORDER BY extname) FROM pg_extension") == ["plpgsql"]
    assert _scalar(db, "SELECT to_regclass('public.user_skills') IS NULL"
                       " AND to_regclass('public.student_credential_embeddings') IS NULL"
                       " AND to_regclass('public.idx_users_did') IS NULL")
    assert _scalar(db, "SELECT indexdef FROM pg_indexes WHERE indexname = 'users_email_lower_key'") == (
        "CREATE UNIQUE INDEX users_email_lower_key ON public.users USING btree (lower((email)::text))")


def test_initdb_volume_without_ledger_is_adopted_and_upgraded(scratch_db):
    from app.migrate import bootstrap_files, migration_files

    fresh, legacy = scratch_db(), scratch_db()
    _migrate(fresh, seed=True)  # 빈 DB + 시드: conftest와 같은 경로
    # 마지막 마이그레이션이 생기기 전에 docker-compose initdb로 만든 예전 볼륨 (원장 없음)
    for path in (bootstrap_files(seed=True) + migration_files())[:-1]:
        _apply_like_initdb(legacy, path)
    user001 = "SELECT password_hash FROM users WHERE email = 'user001@example.com'"
    before = _scalar(legacy, user001)

    # 원장이 없으면 마이그레이션을 모두 다시 적용(여러 번 실행해도 안전)하고 기록한다. 이미 데이터가 있으니 시드는 넣지 않는다.
    assert _migrate(legacy, seed=True) == _names()
    assert _ledger(legacy) == set(_names())
    assert _scalar(legacy, user001) == before
    assert _catalog(legacy) == _catalog(fresh)
    with_password = "SELECT count(*) FROM users WHERE password_hash IS NOT NULL"
    assert _scalar(legacy, with_password) == _scalar(fresh, with_password) > 0


def test_changed_sql_of_applied_migration_fails_with_exit_code_1(scratch_db, monkeypatch):
    import app.migrate as runner

    db = scratch_db()
    _migrate(db)
    with _connect(db) as conn:  # 적용한 뒤 08의 SQL을 고친 것과 같다
        conn.execute("UPDATE public.schema_migrations SET checksum = %s WHERE filename = %s",
                     ("0" * 64, "08_career_curriculum.sql"))
    with pytest.raises(runner.MigrationError, match="08_career_curriculum.sql"):
        _migrate(db)

    # CLI는 앱과 같은 DB_* 환경변수로 접속하고, 실패하면 종료 코드 1을 돌려준다.
    monkeypatch.setenv("DB_NAME", db)
    monkeypatch.setattr(runner, "load_dotenv", lambda *args, **kwargs: False)
    assert runner.main([]) == 1


# ── 운영(APP_ENV=production) 검사 ───────────────────────────────────

def test_production_refuses_seed_before_touching_the_database(monkeypatch):
    import app.migrate as runner

    monkeypatch.setattr(runner.psycopg, "connect", lambda *args, **kwargs: pytest.fail("must not connect"))
    with pytest.raises(runner.MigrationError, match="--seed"):
        runner.migrate(seed=True, production=True, **_conn_kwargs("unused"))


def test_production_fails_while_accounts_use_the_published_seed_password(scratch_db):
    import app.migrate as runner

    # 빈 DB(빈 RDS)의 운영 경로는 그대로다. 시드와 같은 이메일·비밀번호로 가입해도(해시가 다름) 배포를 막지 못한다.
    clean = scratch_db()
    assert _migrate(clean, production=True) == _names()
    with _connect(clean) as conn:
        conn.execute("INSERT INTO users (email, name, subject_did, password_hash) VALUES (%s, %s, %s, %s)",
                     ("user001@example.com", "가입자", "did:key:z6MkSelfRegistered",
                      bcrypt.hashpw(b"test1234", bcrypt.gensalt(rounds=4)).decode()))
    assert _migrate(clean, production=True) == []

    # 원장 없이 시드가 든 DB (개발 DB 덤프를 운영 DB에 복원한 경우): 마이그레이션은 적용·기록하되 실패로 끝난다.
    seeded = scratch_db()
    for path in runner.bootstrap_files(seed=True) + runner.migration_files():
        _apply_like_initdb(seeded, path)
    with pytest.raises(runner.MigrationError, match="시드 테스트 비밀번호") as error:
        _migrate(seeded, production=True)
    assert runner.SEED_PASSWORD_HASH not in str(error.value)  # 해시 값은 메시지(CLI 로그)에 넣지 않는다
    assert _ledger(seeded) == set(_names())
    # 로컬(development)은 그대로: docs/GETTING_STARTED.md 6번(initdb 시드 DB에 --seed 없이)과 Compose migrate 서비스(--seed)
    assert _migrate(seeded) == []
    assert _migrate(seeded, seed=True) == []

    # 시드 계정을 지우면(자격증명 등은 ON DELETE CASCADE) 운영 마이그레이션이 통과한다.
    with _connect(seeded) as conn:
        conn.execute("DELETE FROM users WHERE password_hash = %s", (runner.SEED_PASSWORD_HASH,))
    assert _migrate(seeded, production=True) == []


def test_cli_decides_production_checks_from_app_env_like_the_app(monkeypatch):
    import app.migrate as runner

    calls: list[dict] = []
    monkeypatch.setattr(runner, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(runner, "migrate", lambda **kwargs: calls.append(kwargs) or [])
    monkeypatch.delenv("DB_APP_ROLE", raising=False)
    # BE 이미지 기본값(production)이면 운영 검사, 로컬 .env(development)·빈 값이면 지금과 같다. 잘못된 값도 운영 검사를 한다.
    for value, production in (("production", True), (" Production ", True), ("prod", True),
                              ("development", False), ("", False)):
        monkeypatch.setenv("APP_ENV", value)
        assert runner.main([]) == 0
        assert calls.pop()["production"] is production
    monkeypatch.delenv("APP_ENV")
    monkeypatch.setenv("DB_APP_ROLE", " vc_app ")
    assert runner.main(["--seed"]) == 0
    assert calls.pop() == {"seed": True, "production": False, "wait_seconds": 60, "app_role": "vc_app"}
    monkeypatch.setenv("DB_APP_ROLE", " ")
    assert runner.main([]) == 0
    assert calls.pop()["app_role"] is None


@pytest.mark.parametrize("log_level", ["", "  ", "warning"])
def test_cli_treats_a_blank_log_level_as_info(log_level):
    # 회귀: `LOG_LEVEL=`로 비워 둔 줄이 있으면 실행기가 try 밖에서 "Unknown level: ''" traceback으로 끝났다.
    # basicConfig는 처리기가 없는 프로세스에서만 수준을 읽으므로 새 프로세스에서 본다 (DB에는 접속하지 않는다).
    probe = ("import logging, app.migrate as runner\n"
             "runner.migrate = lambda **kwargs: []\n"
             "code = runner.main([])\n"
             "print(code, logging.getLogger().level)")
    env = {**os.environ, "LOG_LEVEL": log_level, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-B", "-c", probe], cwd=BE_DIR, env=env, capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0 and "Traceback" not in result.stderr, result.stderr[-2000:]
    expected = logging.WARNING if log_level == "warning" else logging.INFO
    assert result.stdout.split() == ["0", str(expected)]
    assert ("완료: 새로 적용한 마이그레이션 없음" in result.stderr) == (expected == logging.INFO)


def test_seed_password_hash_is_the_one_migration_04_sets():
    from app.migrate import DB_DIR, SEED_PASSWORD_HASH

    text = (DB_DIR / "migrations" / "04_users_password_hash.sql").read_text(encoding="utf-8")
    assert f"SET password_hash = '{SEED_PASSWORD_HASH}'" in text


def test_migration_cli_refuses_default_db_password_in_production(monkeypatch, caplog):
    import app.migrate as runner

    monkeypatch.setattr(runner, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(runner.psycopg, "connect", lambda *args, **kwargs: pytest.fail("must not connect"))
    monkeypatch.setenv("APP_ENV", "production")  # 운영 이미지 기본값
    for value in ("password", " "):
        monkeypatch.setenv("DB_PASSWORD", value)
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger="app.migrate"):
            assert runner.main([]) == 1  # 접속하지 않고 오류 한 줄 + 종료 코드 1 (traceback 없음)
        assert "DB_PASSWORD" in caplog.text
    monkeypatch.delenv("DB_PASSWORD")
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="app.migrate"):
        assert runner.main([]) == 1
    assert "DB_PASSWORD" in caplog.text

    monkeypatch.setenv("APP_ENV", "prod")  # 잘못된 APP_ENV도 앱과 같은 오류로 1
    monkeypatch.setenv("DB_PASSWORD", secrets.token_urlsafe(24))
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="app.migrate"):
        assert runner.main([]) == 1
    assert "APP_ENV must be one of" in caplog.text


PGSSLMODE_RULE = "PGSSLMODE (verify-full or verify-ca in production)"
PGSSLROOTCERT_RULE = "PGSSLROOTCERT (readable CA bundle file)"


def test_migration_cli_refuses_db_tls_settings_that_do_not_verify_in_production(monkeypatch, caplog, tmp_path):
    # 마이그레이션 태스크도 서비스와 같은 DB TLS 규칙이다 (app.core.config.db_tls_options): 인증서를 확인하지 않는 설정이면
    # 접속하지 않고(60초 접속 대기도 없이) 이름만 담은 오류 한 줄 + 종료 코드 1이다.
    import app.migrate as runner

    monkeypatch.setattr(runner, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr(runner.psycopg, "connect", lambda *args, **kwargs: pytest.fail("must not connect"))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DB_PASSWORD", secrets.token_urlsafe(24))
    bundle = str(BE_DIR / "certs" / "rds-global-bundle.pem")
    missing = str(tmp_path / "missing-rds-ca.pem")
    for mode, root, expected in ((None, bundle, PGSSLMODE_RULE), ("", bundle, PGSSLMODE_RULE),
                                 ("require", bundle, PGSSLMODE_RULE), ("verify-full", None, PGSSLROOTCERT_RULE),
                                 ("verify-ca", missing, PGSSLROOTCERT_RULE), (" ", " ", f"{PGSSLMODE_RULE}, {PGSSLROOTCERT_RULE}")):
        for name, value in (("PGSSLMODE", mode), ("PGSSLROOTCERT", root)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        caplog.clear()
        started = time.monotonic()
        with caplog.at_level(logging.ERROR, logger="app.migrate"):
            assert runner.main([]) == 1
        assert time.monotonic() - started < 5  # 접속 대기(60초) 없이 바로
        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == [
            f"마이그레이션 실패: APP_ENV=production requires {expected}"], (mode, root)
        assert missing not in caplog.text

    # 규칙에 맞으면 그 값(PGSSLMODE는 앞뒤 공백을 뗀 값)으로 접속한다. CA 묶음 경로는 libpq가 환경변수에서 읽는다.
    seen: dict = {}

    def stop(**kwargs):
        seen.update(kwargs)
        raise psycopg.ProgrammingError("stop before touching the database")  # 다시 시도하지 않는 오류

    monkeypatch.setattr(runner.psycopg, "connect", stop)
    monkeypatch.setenv("PGSSLMODE", " verify-full ")
    monkeypatch.setenv("PGSSLROOTCERT", bundle)
    with caplog.at_level(logging.ERROR, logger="app.migrate"):
        assert runner.main([]) == 1
    assert seen["sslmode"] == "verify-full" and "sslrootcert" not in seen


def _cli_env(**overrides) -> dict:
    env = {name: value for name, value in os.environ.items() if not name.startswith("PGSSL")}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", **overrides})
    return env


def test_migration_cli_ends_a_production_db_tls_error_with_one_stderr_line_and_exit_code_1(tmp_path):
    # 운영 이미지의 마이그레이션 태스크 명령 그대로(`python -m app.migrate`). traceback 없이 한 줄, 경로·비밀번호 없이.
    password = secrets.token_urlsafe(24)
    missing = str(tmp_path / "missing-rds-ca.pem")
    env = _cli_env(APP_ENV="production", DB_PASSWORD=password, PGSSLMODE="require", PGSSLROOTCERT=missing)
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-B", "-m", "app.migrate"], cwd=BE_DIR, env=env, capture_output=True,
                            text=True, timeout=120)
    assert time.monotonic() - started < 30  # 60초 접속 대기 없이
    assert result.returncode == 1 and result.stdout == "", result.stderr[-2000:]
    assert result.stderr.splitlines() == [
        f"ERROR:app.migrate:마이그레이션 실패: APP_ENV=production requires {PGSSLMODE_RULE}, {PGSSLROOTCERT_RULE}"]
    assert password not in result.stderr and missing not in result.stderr


@pytest.mark.parametrize("blank", ["", "  "], ids=["empty", "spaces"])
def test_migration_cli_treats_blank_db_tls_variables_as_unset_in_development(blank):
    # 회귀: `PGSSLMODE=`처럼 비워 둔 줄(.env·작업 정의)이 libpq에 그대로 가서 'invalid sslmode value: ""'로 60초 동안
    # 접속을 다시 시도한 뒤 종료 코드 1이었다. 이제 없는 것과 같다 (TLS 없는 테스트 DB에 libpq 기본값 prefer로 접속).
    env = _cli_env(APP_ENV="development", PGSSLMODE=blank, PGSSLROOTCERT=blank)
    result = subprocess.run([sys.executable, "-B", "-m", "app.migrate"], cwd=BE_DIR, env=env, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr[-2000:]
    assert "INFO:app.migrate:완료: 새로 적용한 마이그레이션 없음" in result.stderr.splitlines()
    assert "sslmode" not in result.stderr


@pytest.mark.parametrize("log_level", ["verbose", "10", "NOTSET", "INFO,DEBUG"])
def test_cli_ends_an_unknown_log_level_with_one_line_and_exit_code_1(log_level):
    # 회귀: 없는 수준 이름이면 실행기가 try 밖의 logging.basicConfig에서 "Unknown level" traceback으로 끝났다.
    # 이제 다른 실패와 같은 `마이그레이션 실패:` 한 줄(변수 이름과 받는 값만, 넣은 값은 싣지 않음)과 종료 코드 1이다.
    # DB에 접속하지 않는다 (접속 정보를 일부러 닿지 않는 주소로 둔다).
    env = _cli_env(LOG_LEVEL=log_level, DB_HOST="127.0.0.1", DB_PORT="1")
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-B", "-m", "app.migrate"], cwd=BE_DIR, env=env, capture_output=True,
                            text=True, timeout=120)
    assert time.monotonic() - started < 30  # 60초 접속 대기 없이
    assert result.returncode == 1 and result.stdout == "", result.stderr[-2000:]
    assert result.stderr.splitlines() == [
        "ERROR:app.migrate:마이그레이션 실패: LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL"
        " (empty means INFO)"]
    if log_level != "INFO,DEBUG":  # "INFO"는 받는 값 목록에도 있다
        assert log_level not in result.stderr


# ── 접속 실패: 다시 해도 같은 오류는 기다리지 않고, 남기는 줄에는 안전한 요약만 ──────────────────

# libpq 접속 오류 원문 꼴. 원문에는 DB 주소(IP)·사용자 이름·DB 이름·인증서 경로가 들어 있어 로그에 싣지 않는다.
_RAW_DETAILS = ("10.78.0.2", "vc-db.abc123.ap-northeast-2.rds.amazonaws.com", "vc_master", "vc_career_agent",
                "/run/vc-ca/ca.pem")
_AT = 'connection failed: connection to server at "10.78.0.2", port 5432 failed: '
_PERMANENT_CONNECT_ERRORS = [
    (_AT + "SSL error: certificate verify failed",
     "server certificate verification failed (check PGSSLROOTCERT: RDS CA bundle)"),
    (_AT + 'server certificate for "vc-db.abc123.ap-northeast-2.rds.amazonaws.com" does not match host name'
           ' "10.78.0.2"',
     "server certificate does not match the host name (set DB_HOST to the RDS endpoint name, not an IP or CNAME)"),
    (_AT + 'FATAL:  password authentication failed for user "vc_master"',
     "password authentication failed (check DB_USER and DB_PASSWORD)"),
    (_AT + 'FATAL:  role "vc_master" does not exist', "role does not exist (check DB_USER)"),
    (_AT + 'FATAL:  database "vc_career_agent" does not exist', "database does not exist (check DB_NAME)"),
    (_AT + 'FATAL:  no pg_hba.conf entry for host "10.78.0.9", user "vc_master", database "vc_career_agent",'
           " no encryption",
     "connection rejected by pg_hba.conf (check PGSSLMODE: RDS requires SSL)"),
    ('connection failed: root certificate file "/run/vc-ca/ca.pem" does not exist',
     "CA bundle file is missing or unreadable (check PGSSLROOTCERT)"),
    (_AT + "server does not support SSL, but SSL was required",
     "server does not support SSL (check DB_HOST and PGSSLMODE)"),
]
_TRANSIENT_CONNECT_ERRORS = [
    (psycopg.OperationalError, _AT + "Connection refused\n\tIs the server running on that host and accepting TCP/IP"
                                     " connections?",
     "OperationalError: connection refused (check DB_HOST and DB_PORT)"),
    (psycopg.OperationalError, _AT + "FATAL:  the database system is starting up",
     "OperationalError: database server is starting up, shutting down or in recovery"),
    (psycopg.errors.ConnectionTimeout, "connection timeout expired",
     "ConnectionTimeout: connection timed out (check DB_HOST, DB_PORT and security groups)"),
    (psycopg.OperationalError, _AT + "FATAL:  sorry, too many clients already",
     "OperationalError: too many connections (check RDS max_connections and DB_MAX_CONNECTIONS)"),
    (psycopg.OperationalError, 'connection failed: could not translate host name "vc-db.abc123" to address',
     "OperationalError: host name not resolved (check DB_HOST)"),
]


class _FakeClock:
    """migrate 모듈의 time 자리에 넣는 가짜 시계 (sleep이 시간을 앞당긴다. 실제로 기다리지 않는다)."""

    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _refusing_connect(monkeypatch, runner, error: BaseException) -> list:
    calls = []

    def connect(**kwargs):
        calls.append(kwargs)
        raise error

    monkeypatch.setattr(runner.psycopg, "connect", connect)
    return calls


@pytest.mark.parametrize("raw,summary", _PERMANENT_CONNECT_ERRORS,
                         ids=[f"permanent-{index}" for index in range(len(_PERMANENT_CONNECT_ERRORS))])
def test_connect_waiting_stops_at_once_on_errors_that_retrying_cannot_fix(monkeypatch, caplog, raw, summary):
    # 회귀(DFX-RUN-1): 마이그레이션 태스크가 인증서 확인 실패에도 60초 동안 다시 시도하며 DB IP가 든 libpq 원문을 30번 넘게
    # 남긴 뒤 같은 원문으로 끝났다. 다시 해도 같은 오류는 기다리지 않고, 운영 시작 DB 점검과 같은 안전한 요약 한 줄로 끝난다.
    import app.migrate as runner

    clock = _FakeClock()
    monkeypatch.setattr(runner, "time", clock)
    calls = _refusing_connect(monkeypatch, runner, psycopg.OperationalError(raw))
    with caplog.at_level(logging.INFO, logger="app.migrate"):
        with pytest.raises(runner.DBConnectError) as error:
            runner._connect_waiting({"host": "vc-db.abc123.ap-northeast-2.rds.amazonaws.com"}, wait_seconds=60)
    assert len(calls) == 1 and clock.sleeps == []
    assert str(error.value) == f"DB에 접속하지 못했습니다: OperationalError: {summary}"
    assert error.value.transient is False and isinstance(error.value.__cause__, psycopg.OperationalError)
    assert isinstance(error.value, runner.MigrationError)  # CLI는 `마이그레이션 실패:` 한 줄 + 종료 코드 1
    assert "DB 연결 대기 중" not in caplog.text
    for detail in _RAW_DETAILS:
        assert detail not in str(error.value) and detail not in caplog.text


@pytest.mark.parametrize("error_type,raw,summary", _TRANSIENT_CONNECT_ERRORS,
                         ids=[f"transient-{index}" for index in range(len(_TRANSIENT_CONNECT_ERRORS))])
def test_connect_waiting_retries_transient_errors_with_safe_lines_until_the_deadline(monkeypatch, caplog, error_type,
                                                                                     raw, summary):
    # DB가 막 뜨는 중(Compose initdb 직후·RDS 재시작)일 수 있는 오류는 지금처럼 2초마다 다시 시도한다. 기다리는 줄과
    # 마지막 오류에는 libpq 원문 대신 안전한 요약만 싣는다.
    import app.migrate as runner

    clock = _FakeClock()
    monkeypatch.setattr(runner, "time", clock)
    calls = _refusing_connect(monkeypatch, runner, error_type(raw))
    with caplog.at_level(logging.INFO, logger="app.migrate"):
        with pytest.raises(runner.DBConnectError) as error:
            runner._connect_waiting({"host": "10.78.0.2"}, wait_seconds=6)
    assert len(calls) == 4 and clock.sleeps == [2, 2, 2]  # 0·2·4·6초에 시도하고 6초(기한)에 끝낸다
    assert str(error.value) == f"DB에 접속하지 못했습니다: {summary}" and error.value.transient is True
    waiting = [r.getMessage() for r in caplog.records if r.name == "app.migrate"]
    assert waiting == [f"DB 연결 대기 중: {summary}"] * 3
    for detail in _RAW_DETAILS:
        assert detail not in str(error.value) and detail not in caplog.text


def test_connect_waiting_returns_the_connection_once_a_transient_error_clears(monkeypatch):
    import app.migrate as runner

    clock = _FakeClock()
    monkeypatch.setattr(runner, "time", clock)
    real, calls = psycopg.connect, []

    def connect(**kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            raise psycopg.OperationalError(_AT + "Connection refused")
        return real(**kwargs)

    monkeypatch.setattr(runner.psycopg, "connect", connect)
    with runner._connect_waiting(_conn_kwargs(TEST_DB_NAME), wait_seconds=60) as conn:
        assert conn.execute("SELECT 1").fetchone() == (1,)
    assert len(calls) == 3 and clock.sleeps == [2, 2]


def test_permanent_connect_fragments_are_classified_by_the_startup_check_table():
    # 다시 시도하지 않는 오류의 조각은 운영 시작 DB 점검의 분류표(_CONNECT_ERRORS)에 있는 것만 쓴다: 그래야 남기는 줄이
    # 늘 정해 둔 요약이다 (원문으로 떨어지지 않는다).
    import app.migrate as runner
    from app.services.db_startup_check import _CONNECT_ERRORS

    classified = {fragments for fragments, _ in _CONNECT_ERRORS}
    assert set(runner._PERMANENT_CONNECT_ERRORS) <= classified
    transient = {("too many",), ("remaining connection slots",), ("the database system is",), ("timeout expired",),
                 ("connection refused",), ("could not translate host name",), ("ssl",)}
    assert not transient & set(runner._PERMANENT_CONNECT_ERRORS)


@pytest.mark.parametrize("case", ["wrong-password", "missing-database", "tls-required"])
def test_migration_cli_ends_a_db_error_that_retrying_cannot_fix_at_once_with_one_safe_line(case):
    # 운영 이미지의 마이그레이션 태스크 명령 그대로(`python -m app.migrate`)를 테스트 DB 서버에 실제로 붙여 본다.
    # 비밀번호가 틀리거나, DB가 없거나, TLS가 없는 서버에 verify-full로 붙으면 60초를 기다리지 않고 한 줄로 끝난다.
    # 그 줄에는 DB 주소·포트·사용자 이름·DB 이름·비밀번호가 없다.
    password = secrets.token_urlsafe(24)
    missing_db = f"{TEST_DB_NAME}_missing_{uuid.uuid4().hex[:8]}"
    overrides = {"APP_ENV": "development"}
    if case == "wrong-password":
        overrides["DB_PASSWORD"] = password
        expected = "password authentication failed (check DB_USER and DB_PASSWORD)"
    elif case == "missing-database":
        overrides["DB_NAME"] = missing_db
        expected = "database does not exist (check DB_NAME)"
    else:  # 테스트 DB 서버는 TLS가 없다
        overrides.update({"PGSSLMODE": "verify-full", "PGSSLROOTCERT": str(BE_DIR / "certs" / "rds-global-bundle.pem")})
        expected = "server does not support SSL (check DB_HOST and PGSSLMODE)"
    env = _cli_env(**overrides)
    started = time.monotonic()
    result = subprocess.run([sys.executable, "-B", "-m", "app.migrate"], cwd=BE_DIR, env=env, capture_output=True,
                            text=True, timeout=120)
    assert time.monotonic() - started < 30, result.stderr[-2000:]  # 60초 접속 대기 없이
    assert result.returncode == 1 and result.stdout == "", result.stderr[-2000:]
    assert result.stderr.splitlines() == [
        f"ERROR:app.migrate:마이그레이션 실패: DB에 접속하지 못했습니다: OperationalError: {expected}"]
    for value in (env["DB_HOST"], f"port {env['DB_PORT']}", f'"{env["DB_USER"]}"', TEST_DB_NAME, missing_db, password,
                  str(BE_DIR)):
        assert value not in result.stderr


# ── 서비스 계정(DB_APP_ROLE) 권한 ────────────────────────────────────

@pytest.fixture
def app_role(scratch_db):
    """BE 서비스 계정(클러스터 전역 역할)과 임시 DB. 끝나면 그 DB에서 받은 권한을 걷고 역할을 지운다."""
    db, role = scratch_db(), f"{TEST_DB_NAME}_role_{uuid.uuid4().hex[:8]}"[-63:]
    password, ident = secrets.token_urlsafe(24), sql.Identifier(role)
    with _connect("postgres") as admin:
        admin.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(ident, sql.Literal(password)))
    try:
        yield db, role, {**_conn_kwargs(db), "user": role, "password": password}
    finally:
        with _connect(db) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(ident))
        with _connect("postgres") as admin:
            admin.execute(sql.SQL("DROP ROLE {}").format(ident))


def test_app_role_gets_only_dml_and_is_reapplied(app_role, monkeypatch):
    import app.migrate as runner

    db, role, as_app = app_role
    _migrate(db, app_role=role)
    with psycopg.connect(**as_app, autocommit=True) as conn:
        conn.execute("INSERT INTO users (email, name, subject_did) VALUES ('r@test.com', 'r', 'did:example:r')")
        assert conn.execute("UPDATE users SET name = 's' RETURNING name").fetchone()[0] == "s"
        conn.execute("INSERT INTO mock_issuances (user_id, template_id) SELECT id, 'tpl' FROM users")
        conn.execute("DELETE FROM users")
        # 스키마 변경·원장·소유자 작업은 서비스 계정으로 할 수 없다.
        for statement in ("CREATE TABLE public.app_role_probe (id INT)", "ALTER TABLE users ADD COLUMN probe INT",
                          "SELECT count(*) FROM public.schema_migrations", "DROP TABLE shares"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)

    # 다음 배포에서 새 테이블(시퀀스 포함)이 생겨도 CLI(DB_APP_ROLE)가 실행될 때마다 권한을 다시 맞춘다.
    with _connect(db) as conn:
        conn.execute("CREATE TABLE public.app_role_later (id BIGSERIAL PRIMARY KEY)")
    monkeypatch.setenv("DB_NAME", db)
    monkeypatch.setenv("DB_APP_ROLE", role)
    monkeypatch.setattr(runner, "load_dotenv", lambda *args, **kwargs: False)
    assert runner.main([]) == 0
    with psycopg.connect(**as_app, autocommit=True) as conn:
        conn.execute("INSERT INTO public.app_role_later DEFAULT VALUES")


def test_missing_or_privileged_app_role_is_refused(app_role):
    from app.migrate import MigrationError

    db, role, _ = app_role
    # 서비스 계정 검사는 lock 직후, 기본 스키마·마이그레이션 SQL보다 먼저 한다: 거부되면 빈 DB는 빈 DB로 남는다.
    empty = "SELECT to_regclass('public.users') IS NULL AND to_regclass('public.schema_migrations') IS NULL"
    with pytest.raises(MigrationError, match="역할이 없습니다"):
        _migrate(db, app_role=f"{TEST_DB_NAME}_missing_{uuid.uuid4().hex[:8]}")
    assert _scalar(db, empty)
    # 마이그레이션 계정 자신(여기서는 슈퍼유저)을 서비스 계정으로 쓰면 안 된다.
    with pytest.raises(MigrationError, match="특권 없는 역할"):
        _migrate(db, app_role=os.environ["DB_USER"])
    # DB 생성 권한이나 서버 파일 읽기 같은 기본 제공 특권 역할이 있어도 거부하고, 고치면 통과한다.
    ident = sql.Identifier(role)
    for grant, revoke in (("ALTER ROLE {} CREATEDB", "ALTER ROLE {} NOCREATEDB"),
                          ("GRANT pg_read_server_files TO {}", "REVOKE pg_read_server_files FROM {}")):
        with _connect("postgres") as admin:
            admin.execute(sql.SQL(grant).format(ident))
        try:
            with pytest.raises(MigrationError, match="특권 없는 역할"):
                _migrate(db, app_role=role)
        finally:
            with _connect("postgres") as admin:
                admin.execute(sql.SQL(revoke).format(ident))
    assert _scalar(db, empty)
    assert _migrate(db, app_role=role) == _names()


def test_app_role_that_can_create_objects_is_refused(app_role):
    """public 스키마나 DB에 객체를 만들 수 있는 서비스 계정은 거부하고(권한도 주지 않음), CREATE를 거두면 통과한다.

    PostgreSQL 14 이하의 기본값(PUBLIC에 public 스키마 CREATE)이 남은 DB에서는 서비스 계정이 만든 함수·연산자
    (예: public.lower)를 마이그레이션 계정과 앱이 search_path로 찾아 실행하게 된다.
    """
    from app.migrate import MigrationError

    db, role, _ = app_role
    _migrate(db)  # 서비스 계정 없이 이미 마이그레이션한 DB (서비스 계정 검사는 마이그레이션 SQL보다 먼저 거부한다)
    names = {"db": sql.Identifier(db), "role": sql.Identifier(role)}
    can_insert = "SELECT has_table_privilege(oid, 'public.users', 'INSERT') FROM pg_roles WHERE rolname = %s"
    # 메시지는 걸린 검사에 맞는 REVOKE 문을 담는다 (이름은 따옴표로 감싼다).
    for grant, revoke, hint in (
            ("GRANT CREATE ON SCHEMA public TO PUBLIC", "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
             "REVOKE CREATE ON SCHEMA public FROM PUBLIC;"),
            ("GRANT CREATE ON DATABASE {db} TO {role}", "REVOKE CREATE ON DATABASE {db} FROM {role}",
             "REVOKE CREATE ON DATABASE {db} FROM {role};")):
        with _connect(db) as admin:
            admin.execute(sql.SQL(grant).format(**names))
            hint = sql.SQL(hint).format(**names).as_string(admin)
        try:
            with pytest.raises(MigrationError, match=re.escape(hint)):
                _migrate(db, app_role=role)
            with _connect(db) as conn:
                assert conn.execute(can_insert, (role,)).fetchone()[0] is False
        finally:
            with _connect(db) as admin:
                admin.execute(sql.SQL(revoke).format(**names))
    assert _migrate(db, app_role=role) == []


def test_database_create_for_public_is_refused_with_a_runnable_fix(scratch_db, app_role):
    """PUBLIC에 준 DB CREATE도 거부하고, 메시지는 DB 검사에 걸렸다고 말하며 그대로 실행할 수 있는 REVOKE 문을 준다.

    이름은 따옴표로 감싸므로 따옴표가 필요한 DB 이름(대문자)에서도 운영자가 메시지의 문장을 그대로 실행하면 통과한다.
    """
    from app.migrate import MigrationError

    _, role, _ = app_role
    db = scratch_db("Mig")
    try:
        with _connect(db) as admin:
            admin.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO PUBLIC").format(sql.Identifier(db)))
            db_q, role_q = sql.Identifier(db).as_string(admin), sql.Identifier(role).as_string(admin)
        with pytest.raises(MigrationError) as error:
            _migrate(db, app_role=role)
        message = str(error.value)
        assert f"DB {db_q}에 객체를 만들 수 있습니다" in message
        assert f"REVOKE CREATE ON DATABASE {db_q} FROM PUBLIC;" in message
        assert "public 스키마" not in message  # 걸리지 않은 검사(public 스키마)의 안내는 넣지 않는다
        statements = re.findall(r"REVOKE CREATE ON [^;]+;", message)
        assert f"REVOKE CREATE ON DATABASE {db_q} FROM {role_q};" in statements
        with _connect(db) as admin:  # 마이그레이션 계정(여기서는 슈퍼유저)으로 메시지의 문장을 그대로 실행
            for statement in statements:
                admin.execute(statement)
        assert _migrate(db, app_role=role) == _names()  # 거부한 실행은 아무것도 적용하지 않았다
    finally:  # 이 DB에서 받은 권한을 걷어야 app_role 픽스처가 역할을 지울 수 있다
        with _connect(db) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))


@pytest.fixture
def owner_db(scratch_db):
    """마이그레이션 계정이 슈퍼유저가 아닌 DB 소유자 역할인 임시 DB (RDS의 전용 소유자 역할·직접 운영하는 PostgreSQL과 같음).

    예전 기본 스키마가 만들던 확장(pgcrypto·vector)이 남아 있는 DB처럼 슈퍼유저가 먼저 만들어 둔다 (지금 기본 스키마는 확장을
    만들지 않고, 있는 확장은 그대로 둔다).
    (DB, 소유자 접속 인자, 역할을 만드는 함수)를 돌려주고, 끝나면 DB와 만든 역할을 지운다.
    """
    db, password = scratch_db(), secrets.token_urlsafe(24)
    roles: list[str] = []

    def new_role(options: str = "LOGIN") -> str:
        name = f"{TEST_DB_NAME}_role_{uuid.uuid4().hex[:8]}"[-63:]
        with _connect("postgres") as admin:
            admin.execute(sql.SQL(f"CREATE ROLE {{}} {options}").format(sql.Identifier(name)))
        roles.append(name)
        return name

    try:
        owner = new_role()
        with _connect("postgres") as admin:
            admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(owner), sql.Literal(password)))
            admin.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(sql.Identifier(db), sql.Identifier(owner)))
        with _connect(db) as admin:
            admin.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
        yield db, {**_conn_kwargs(db), "user": owner, "password": password}, new_role
    finally:
        with _connect("postgres") as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
            for name in reversed(roles):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


def test_migration_account_and_its_members_are_refused_as_app_role(owner_db):
    """마이그레이션 계정 자신이나 그 소속(권한을 상속하지 않아도 SET ROLE로 소유자가 될 수 있음)은 서비스 계정으로 쓸 수 없다.

    마이그레이션 계정이 슈퍼유저가 아니면 이 검사(pg_has_role ... current_user)만 그 소속을 잡는다.
    """
    from app.migrate import MigrationError, migrate

    _, as_owner, new_role = owner_db
    # 최소 권한 구성(소유자 역할로 마이그레이션 + DB_APP_ROLE)은 그대로 통과한다.
    assert migrate(app_role=new_role(), **as_owner) == _names()
    owner, member, noinherit_member = as_owner["user"], new_role(), new_role("LOGIN NOINHERIT")
    with _connect("postgres") as admin:
        for name in (member, noinherit_member):
            admin.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(owner), sql.Identifier(name)))
    for role in (owner, member, noinherit_member):
        with pytest.raises(MigrationError, match="특권 없는 역할"):
            migrate(app_role=role, **as_owner)


def test_app_role_that_can_set_role_to_a_creator_is_refused(owner_db):
    """CREATE를 가진 역할의 소속은 권한을 상속하지 않아도(NOINHERIT 역할, PostgreSQL 16의 GRANT ... WITH INHERIT FALSE)
    SET ROLE로 그 CREATE를 쓸 수 있으므로(서비스 계정이 public.lower 같은 함수를 심을 수 있음) 거부한다.

    메시지는 걸린 검사(public 스키마·DB)에 맞는 REVOKE 문만 담고, 거부하면 권한도 주지 않으며, 소속을 거두면 통과한다.
    """
    from app.migrate import MigrationError, migrate

    db, as_owner, new_role = owner_db
    assert migrate(**as_owner) == _names()
    schema_group, db_group = new_role("NOLOGIN"), new_role("NOLOGIN")
    with _connect(db) as admin:
        admin.execute(sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(sql.Identifier(schema_group)))
        admin.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(db_group)))
        db_hint = sql.SQL("REVOKE CREATE ON DATABASE {} FROM PUBLIC;").format(sql.Identifier(db)).as_string(admin)
        inherit_false = admin.info.server_version >= 160000  # GRANT ... WITH INHERIT FALSE는 PostgreSQL 16부터
    schema_hint = "REVOKE CREATE ON SCHEMA public FROM PUBLIC;"
    cases = [(schema_group, "LOGIN NOINHERIT", "", schema_hint), (db_group, "LOGIN NOINHERIT", "", db_hint)]
    if inherit_false:
        cases.append((schema_group, "LOGIN", " WITH INHERIT FALSE", schema_hint))
    can_insert = "SELECT has_table_privilege(oid, 'public.users', 'INSERT') FROM pg_roles WHERE rolname = %s"
    for group, options, grant_options, hint in cases:
        role = new_role(options)
        with _connect(db) as admin:
            admin.execute(sql.SQL("GRANT {} TO {}" + grant_options).format(sql.Identifier(group), sql.Identifier(role)))
            assert not admin.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE') OR has_database_privilege("
                                     "%s, current_database(), 'CREATE')", (role, role)).fetchone()[0]  # 자기 권한으로는 못 만든다
        with pytest.raises(MigrationError) as error:
            migrate(app_role=role, **as_owner)
        message = str(error.value)
        assert hint in message
        assert ("ON SCHEMA public" in message, "ON DATABASE" in message) == (hint == schema_hint, hint == db_hint)
        with _connect(db) as conn:
            assert conn.execute(can_insert, (role,)).fetchone()[0] is False
        with _connect("postgres") as admin:
            admin.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(group), sql.Identifier(role)))
        assert migrate(app_role=role, **as_owner) == []


@pytest.fixture
def cluster():
    """임시 역할(new_role)과 임시 DB(new_db, 소유자를 줄 수 있음). 마이그레이션 계정은 슈퍼유저(DB_USER)다.

    끝나면 DB를 먼저 지운 뒤 역할을 지운다 (역할이 DB·public 스키마를 소유하거나 그 DB에서 권한을 받았어도 지울 수 있게).
    """
    dbs: list[str] = []
    roles: list[str] = []

    def new_role(options: str = "LOGIN") -> str:
        name = f"{TEST_DB_NAME}_role_{uuid.uuid4().hex[:8]}"[-63:]
        with _connect("postgres") as admin:
            admin.execute(sql.SQL(f"CREATE ROLE {{}} {options}").format(sql.Identifier(name)))
        roles.append(name)
        return name

    def new_db(owner: str | None = None) -> str:
        name = f"{TEST_DB_NAME}_mig_{uuid.uuid4().hex[:8]}"
        with _connect("postgres") as admin:
            admin.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(owner or os.environ["DB_USER"])))
        dbs.append(name)
        return name

    try:
        yield new_role, new_db
    finally:
        with _connect("postgres") as admin:
            for name in dbs:
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
            for name in reversed(roles):
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


def _as_admin(dbname: str, *statements: str, **names: str) -> None:
    """마이그레이션 계정(슈퍼유저)으로 문장을 실행한다. {이름}은 따옴표로 감싼 식별자로 바꾼다."""
    with _connect(dbname) as admin:
        for statement in statements:
            admin.execute(sql.SQL(statement).format(**{key: sql.Identifier(value) for key, value in names.items()}))


_CAN_INSERT = "SELECT has_table_privilege(oid, 'public.users', 'INSERT') FROM pg_roles WHERE rolname = %s"


def _db_dir_with(tmp_path, name: str, text: str):
    """DB 폴더를 복사해 아직 적용하지 않은 마이그레이션 파일 하나를 더한다."""
    db_dir = tmp_path / "DB"
    shutil.copytree(BE_DIR / "DB", db_dir)
    (db_dir / "migrations" / name).write_text(text, encoding="utf-8")
    return db_dir


def _log_table(dbname: str) -> None:
    """심은 함수가 불리면 (실행한 계정, 이름)을 남기는 표. 누구나 쓸 수 있다."""
    _as_admin(dbname, "CREATE SCHEMA vclog", "CREATE TABLE vclog.hits (who TEXT, what TEXT)",
              "GRANT USAGE ON SCHEMA vclog TO PUBLIC", "GRANT INSERT ON vclog.hits TO PUBLIC")


def _hits(dbname: str) -> list:
    with _connect(dbname) as conn:
        return conn.execute("SELECT who, what FROM vclog.hits").fetchall()


def _plant_operator(conn, name: str, left: str, right: str) -> None:
    """public.=(left, right): 불리면 vclog.hits에 남기고 false를 돌려준다 (그 연결의 현재 역할이 소유한다)."""
    conn.execute(f"CREATE FUNCTION public.{name}({left}, {right}) RETURNS boolean LANGUAGE plpgsql AS"
                 f" $$ BEGIN INSERT INTO vclog.hits VALUES (current_user, '{name}'); RETURN false; END $$")
    conn.execute(f"CREATE OPERATOR public.= (LEFTARG = {left}, RIGHTARG = {right}, FUNCTION = public.{name})")


def test_least_privilege_master_that_creates_the_app_role_passes(cluster):
    """deploy/CHECKLIST.md 'DB 마이그레이션'의 구성: 슈퍼유저가 아닌 마스터 사용자(LOGIN CREATEDB CREATEROLE)가 DB를 소유하고
    CREATE ROLE로 서비스 계정을 만든다 (PostgreSQL 16은 만든 역할에 마스터의 ADMIN 소속을 남긴다: 마스터가 서비스 계정에
    속하는 것이지 반대가 아니다). 소속 역할의 특권 속성·소유·public의 객체를 보는 검사도 이 구성은 통과시키고 권한을 준다."""
    from app.migrate import migrate

    new_role, new_db = cluster
    master, password = new_role("LOGIN CREATEDB CREATEROLE"), secrets.token_urlsafe(24)
    app = f"{TEST_DB_NAME}_role_{uuid.uuid4().hex[:8]}"[-63:]
    with _connect("postgres") as admin:
        admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(master), sql.Literal(password)))
    db = new_db(owner=master)  # 확장을 미리 만들지 않는다: 기본 스키마가 확장을 만들지 않으므로 마스터에게 그 권한이 필요 없다
    as_master = {**_conn_kwargs(db), "user": master, "password": password}
    with psycopg.connect(**as_master, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(app)))
    try:
        assert migrate(app_role=app, production=True, **as_master) == _names()
        assert migrate(app_role=app, production=True, **as_master) == []
        with _connect(db) as conn:
            assert conn.execute(_CAN_INSERT, (app,)).fetchone()[0] is True
            assert conn.execute("SELECT array_agg(extname::text) FROM pg_extension").fetchone()[0] == ["plpgsql"]
    finally:  # 마스터가 만든 역할이라 픽스처 목록에 없다: 이 DB에서 받은 권한을 걷고 지운다
        with _connect(db) as conn:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(app)))
        with _connect("postgres") as admin:
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(app)))


@pytest.mark.parametrize("case", ["owns_db", "owns_upgraded_db", "owns_public_schema", "member_of_db_owner"])
def test_app_role_that_owns_the_database_or_public_schema_is_refused(cluster, case):
    """소유자는 자기 CREATE를 거둬도 스스로 다시 줄 수 있으므로 DB나 public 스키마의 소유(그 역할이 속한 역할의 소유,
    DB 소유자가 pg_database_owner로 갖는 public 스키마 포함)도 CREATE 권한처럼 거부한다 (자기 CREATE는 모두 거둔 상태).

    메시지는 소유를 알리고 그대로 실행할 수 있는 ALTER ... OWNER 문을 주며, 거부하면 권한도 주지 않고,
    마이그레이션 계정(여기서는 슈퍼유저)으로 메시지의 문장을 실행하면 통과한다.
    """
    from app.migrate import MigrationError

    new_role, new_db = cluster
    app = new_role("LOGIN NOINHERIT" if case == "member_of_db_owner" else "LOGIN")
    owner = new_role("NOLOGIN") if case == "member_of_db_owner" else app
    db = new_db(owner=None if case == "owns_public_schema" else owner)
    names = {"db": db, "app": app, "owner": owner, "migrator": os.environ["DB_USER"]}
    if case == "owns_upgraded_db":  # PostgreSQL 14 이하에서 만들어 올라온 DB처럼 public 스키마는 부트스트랩 슈퍼유저 소유
        _as_admin(db, "ALTER SCHEMA public OWNER TO {migrator}", **names)
    _migrate(db)  # 서비스 계정 없이 이미 마이그레이션한 DB
    if case == "owns_public_schema":
        _as_admin(db, "ALTER SCHEMA public OWNER TO {app}", "REVOKE CREATE ON SCHEMA public FROM {app}", **names)
        with _connect(db) as conn:  # pg_database_owner가 public 스키마를 소유하는 것은 PostgreSQL 15부터
            new_owner = "pg_database_owner" if conn.info.server_version >= 150000 else "{migrator}"
        fix = f"ALTER SCHEMA public OWNER TO {new_owner};"
    else:
        _as_admin(db, "REVOKE CREATE ON DATABASE {db} FROM {owner}",
                  "REVOKE CREATE ON SCHEMA public FROM pg_database_owner", **names)
        fix = "ALTER DATABASE {db} OWNER TO {migrator};"
    if case == "member_of_db_owner":
        _as_admin("postgres", "GRANT {owner} TO {app}", **names)
    with _connect(db) as conn:
        fix = sql.SQL(fix).format(**{key: sql.Identifier(value) for key, value in names.items()}).as_string(conn)
        assert not conn.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE') OR has_database_privilege("
                                "%s, current_database(), 'CREATE')", (app, app)).fetchone()[0]  # 자기 권한으로는 못 만든다
    with pytest.raises(MigrationError, match="소유자") as error:
        _migrate(db, app_role=app)
    message = str(error.value)
    assert fix in message
    if case == "member_of_db_owner":  # 소속 역할의 이름은 알려 주고 소속을 거두는 문장은 주지 않는다
        with _connect(db) as conn:
            owner_q, app_q = (sql.Identifier(name).as_string(conn) for name in (owner, app))
        assert f"그 역할이 속한 역할 {owner_q}" in message and f"REVOKE {owner_q} FROM {app_q}" not in message
    with _connect(db) as conn:
        assert conn.execute(_CAN_INSERT, (app,)).fetchone()[0] is False  # 거부하면 권한도 주지 않는다
    with _connect(db) as admin:  # 마이그레이션 계정으로 메시지의 문장을 그대로 실행
        for statement in re.findall(r"(?:ALTER (?:DATABASE|SCHEMA) |REVOKE )[^;]+;", message):
            admin.execute(statement)
    assert _migrate(db, app_role=app) == []
    with _connect(db) as conn:
        assert conn.execute(_CAN_INSERT, (app,)).fetchone()[0] is True


@pytest.mark.parametrize("target", ["schema", "db", "nested_db"])
def test_refusal_names_the_member_role_that_holds_create(cluster, target):
    """CREATE를 그 역할이 속한 역할로 가지면 메시지가 그 역할의 이름(따옴표)과 그 역할에서 거둘 REVOKE 문을 알려 준다.
    소속을 거두는 REVOKE 문은 주지 않는다(소속을 두어야 다음 실행이 그 역할의 객체를 찾는다). 메시지의 REVOKE CREATE 문을 그대로
    실행하면 통과한다."""
    from app.migrate import MigrationError

    new_role, new_db = cluster
    db = new_db()
    _migrate(db)
    group, app = new_role("NOLOGIN"), new_role("LOGIN NOINHERIT")
    names = {"db": db, "group": group, "app": app}
    _as_admin(db, "GRANT CREATE ON SCHEMA public TO {group}" if target == "schema"
              else "GRANT CREATE ON DATABASE {db} TO {group}", **names)
    if target == "nested_db":  # 서비스 계정 → 중간 역할(NOINHERIT) → CREATE를 가진 역할
        middle = new_role("NOLOGIN NOINHERIT")
        _as_admin("postgres", "GRANT {group} TO {middle}", "GRANT {middle} TO {app}", middle=middle, **names)
    else:
        _as_admin("postgres", "GRANT {group} TO {app}", **names)
    with _connect(db) as conn:
        quoted = {key: sql.Identifier(value).as_string(conn) for key, value in names.items()}
    on = "SCHEMA public" if target == "schema" else f"DATABASE {quoted['db']}"
    with pytest.raises(MigrationError) as error:
        _migrate(db, app_role=app)
    message = str(error.value)
    assert f"소속 역할 {quoted['group']}: REVOKE CREATE ON {on} FROM {quoted['group']};" in message
    assert f"REVOKE {quoted['group']} FROM {quoted['app']}" not in message
    with _connect(db) as admin:
        for statement in re.findall(r"REVOKE CREATE ON [^;]+;", message):
            admin.execute(statement)
    assert _migrate(db, app_role=app) == []


@pytest.mark.parametrize("attribute", ["SUPERUSER", "CREATEROLE", "CREATEDB", "REPLICATION", "BYPASSRLS"])
def test_member_of_a_role_with_privileged_attributes_is_refused(cluster, attribute):
    """그 역할이 속한 역할의 특권 속성도 거부한다 (권한을 상속하지 않는 NOINHERIT 소속도 SET ROLE로 그 역할이 될 수 있다).
    메시지는 그 소속 역할의 이름을 알려 주고, 거부하면 권한도 주지 않으며, 소속을 거두면 통과한다."""
    from app.migrate import MigrationError

    new_role, new_db = cluster
    db = new_db()
    _migrate(db)
    group, app = new_role(f"NOLOGIN {attribute}"), new_role("LOGIN NOINHERIT")
    _as_admin("postgres", "GRANT {group} TO {app}", group=group, app=app)
    with pytest.raises(MigrationError, match="특권 없는 역할") as error:
        _migrate(db, app_role=app)
    with _connect(db) as conn:
        assert sql.Identifier(group).as_string(conn) in str(error.value)
        assert conn.execute(_CAN_INSERT, (app,)).fetchone()[0] is False
    _as_admin("postgres", "REVOKE {group} FROM {app}", group=group, app=app)
    assert _migrate(db, app_role=app) == []


def test_objects_the_app_role_owns_in_public_are_refused_before_any_migration(app_role, tmp_path):
    """서비스 계정이 public 스키마에 만든 객체(PostgreSQL 14 이하 기본값처럼 PUBLIC에 CREATE가 있던 동안 만든 함수·연산자·
    타입·테이블)는 CREATE를 거둔 뒤에도 남아 마이그레이션 계정·앱이 search_path로 찾아 실행할 수 있다.

    실행기는 남은 마이그레이션 파일을 적용하기 전에 그 객체를 10개까지 알려 주며 거부하고(권한도 주지 않음), 지우면 통과한다.
    """
    from app.migrate import MigrationError

    db, role, _ = app_role
    _migrate(db)
    db_dir = tmp_path / "DB"
    shutil.copytree(BE_DIR / "DB", db_dir)
    (db_dir / "migrations" / "99_pending_probe.sql").write_text(
        "CREATE TABLE IF NOT EXISTS public.pending_probe (id INT);\n", encoding="utf-8")
    with _connect(db) as admin:
        admin.execute("GRANT CREATE ON SCHEMA public TO PUBLIC")
        admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))  # 서비스 계정으로 만든다
        admin.execute("CREATE FUNCTION public.lower(varchar) RETURNS text LANGUAGE sql AS $$ SELECT 'planted' $$")
        admin.execute("CREATE FUNCTION public.app_eq(varchar, varchar) RETURNS boolean LANGUAGE sql"
                      " AS $$ SELECT true $$")
        admin.execute("CREATE OPERATOR public.= (LEFTARG = varchar, RIGHTARG = varchar, FUNCTION = public.app_eq)")
        admin.execute("CREATE TYPE public.app_type AS (id INT)")
        admin.execute("CREATE TABLE public.app_table (id INT)")
        for number in range(8):
            admin.execute(f"CREATE VIEW public.app_view_{number} AS SELECT 1 AS one")
        admin.execute("RESET ROLE")
        admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    with pytest.raises(MigrationError, match="public 스키마에 객체를 소유합니다") as error:
        _migrate(db, app_role=role, db_dir=db_dir)
    message = str(error.value)
    for identity in ("function public.lower(character varying)",
                     "function public.app_eq(character varying,character varying)",
                     "operator public.=(character varying,character varying)", "type public.app_type",
                     "table public.app_table", "view public.app_view_0"):
        assert identity in message
    assert "view public.app_view_7" not in message and "외 3개" in message  # 13개 중 10개만 알려 준다
    assert "99_pending_probe.sql" not in _ledger(db)
    assert not _scalar(db, "SELECT to_regclass('public.pending_probe') IS NOT NULL")
    with _connect(db) as conn:
        assert conn.execute(_CAN_INSERT, (role,)).fetchone()[0] is False
        conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))  # 확인한 뒤 지운다
    assert _migrate(db, app_role=role, db_dir=db_dir) == ["99_pending_probe.sql"]


# 실행기가 부르는 카탈로그 함수·뷰와 같은 이름 (불리면 calls에 이름을 남기고 pg_catalog의 것을 돌려준다)
_PLANTED_FUNCTIONS = (("pg_advisory_lock", "bigint", "void"), ("to_regclass", "text", "regclass"),
                      ("has_schema_privilege", "oid, text, text", "boolean"),
                      ("has_database_privilege", "oid, text, text", "boolean"),
                      ("pg_has_role", "oid, oid, text", "boolean"), ("current_database", "", "name"),
                      ("now", "", "timestamptz"), ("gen_random_uuid", "", "uuid"))
_PLANTED_VIEWS = ("pg_roles", "pg_namespace", "pg_database", "pg_shdepend")


@pytest.mark.parametrize("where", ["user_schema", "database_search_path"])
def test_runner_never_calls_functions_planted_on_the_search_path(app_role, where):
    """실행기는 모든 연결의 search_path를 public으로 고정하고 카탈로그 함수에 pg_catalog.를 붙인다.

    서비스 계정이 (DB CREATE가 있던 동안) 마이그레이션 계정 이름의 스키마("$user")나 DB의 search_path 설정
    (ALTER DATABASE ... SET search_path = planted, pg_catalog, public)이 가리키는 스키마에 실행기가 부르는 것과 같은
    이름의 함수·뷰를 심어 두어도 실행기는 그것을 부르지 않고, 기본 스키마의 타입·테이블도 public에 만든다.
    서비스 계정이 소유한 그 스키마는 먼저 거부하고(빈 DB는 빈 채로), 메시지의 ALTER SCHEMA로 소유자만 바꾸면(심은 함수·뷰는
    서비스 계정 소유로 남는다) 통과한다.
    """
    from app.migrate import MigrationError

    db, role, _ = app_role
    schema = sql.Identifier(os.environ["DB_USER"] if where == "user_schema" else "planted")
    with _connect(db) as admin:
        admin.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(sql.Identifier(db), sql.Identifier(role)))
        admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))  # 서비스 계정으로 심는다
        s = schema.as_string(admin)
        admin.execute(f"CREATE SCHEMA {s}")
        admin.execute(f"GRANT USAGE ON SCHEMA {s} TO PUBLIC")
        admin.execute(f"CREATE TABLE {s}.calls (name TEXT)")
        admin.execute(f"GRANT INSERT ON {s}.calls TO PUBLIC")
        admin.execute(f"CREATE FUNCTION {s}.called(text) RETURNS boolean LANGUAGE sql"
                      f" AS $$ INSERT INTO {s}.calls VALUES ($1) RETURNING true $$")
        for name, args, returns in _PLANTED_FUNCTIONS:
            params = ", ".join(f"${number}" for number in range(1, len(args.split(",")) + 1)) if args else ""
            admin.execute(f"CREATE FUNCTION {s}.{name}({args}) RETURNS {returns} LANGUAGE sql AS"
                          f" $$ INSERT INTO {s}.calls VALUES ('{name}'); SELECT pg_catalog.{name}({params}) $$")
        for view in _PLANTED_VIEWS:
            admin.execute(f"CREATE VIEW {s}.{view} AS SELECT * FROM pg_catalog.{view} WHERE {s}.called('{view}')")
        admin.execute("RESET ROLE")
        admin.execute(sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(sql.Identifier(db), sql.Identifier(role)))
        if where == "database_search_path":
            admin.execute(sql.SQL("ALTER DATABASE {} SET search_path = planted, pg_catalog, public").format(
                sql.Identifier(db)))
    with pytest.raises(MigrationError, match="스키마를 소유합니다") as error:
        _migrate(db, app_role=role)
    with _connect(db) as conn:  # 이 세션도 같은 search_path를 받으므로 pg_catalog.를 붙인다
        assert conn.execute("SELECT pg_catalog.to_regclass('public.users') IS NULL").fetchone()[0]
        owner = sql.Identifier(os.environ["DB_USER"]).as_string(conn)
        statements = re.findall(r"ALTER SCHEMA [^;]+;", str(error.value))
        assert statements == [f"ALTER SCHEMA {s} OWNER TO {owner};"]
        for statement in statements:
            conn.execute(statement)
    assert _migrate(db, app_role=role) == _names()  # 빈 DB: 기본 스키마 + 마이그레이션 전부
    assert _migrate(db, app_role=role, production=True) == []
    with _connect(db) as conn:  # 이 세션도 같은 search_path를 받으므로 pg_catalog.를 붙여 확인한다
        assert conn.execute(f"SELECT pg_catalog.count(*) FROM {s}.calls").fetchone()[0] == 0
        assert conn.execute("SELECT pg_catalog.to_regclass('public.users') IS NOT NULL").fetchone()[0]
        assert conn.execute("SELECT pg_catalog.array_agg(DISTINCT t.typnamespace::pg_catalog.regnamespace::pg_catalog.text)"
                            " FROM pg_catalog.pg_type t WHERE t.typname IN ('credential_format', 'verification_status',"
                            " 'check_result', 'check_name_type')").fetchone()[0] == ["public"]
        assert conn.execute("SELECT pg_catalog.count(*) FROM pg_catalog.pg_class c WHERE c.relkind = 'r'"
                            " AND c.relnamespace = %s::pg_catalog.regnamespace", (schema.as_string(conn),)
                            ).fetchone()[0] == 1  # 심은 스키마에는 calls 표뿐


def test_runner_never_resolves_operators_the_app_role_planted_in_public(app_role, tmp_path):
    """실행기 자신의 SQL(서비스 계정 검사·원장·시드 해시 확인)은 pg_catalog만 찾는 lock 연결에서 실행한다.

    PUBLIC에 public 스키마 CREATE가 있던 동안 서비스 계정이 실행기의 비교와 형이 정확히 맞는 연산자(=(oid, regclass)는 소유
    객체 검사를 숨기고, =(varchar, varchar)는 시드 해시 확인·마이그레이션 SQL의 비교에 걸린다)를 심어 두어도, deploy/CHECKLIST.md 단계
    (REVOKE CREATE ... FROM PUBLIC) 뒤의 실행은 그 객체를 알려 주며 거부하고(운영 검사도 같다) 심은 함수는 실행되지 않는다.
    """
    from app.migrate import MigrationError

    db, role, _ = app_role
    _migrate(db)
    db_dir = _db_dir_with(tmp_path, "99_pending_compare.sql",
                          "UPDATE public.users SET name = name WHERE email = 'nobody@example.com';\n")
    _log_table(db)
    with _connect(db) as admin:
        admin.execute("INSERT INTO users (email, name, subject_did, password_hash)"
                      " VALUES ('a@test.com', 'a', 'did:example:a', 'h')")
        admin.execute("GRANT CREATE ON SCHEMA public TO PUBLIC")
        admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))  # 서비스 계정으로 심는다
        _plant_operator(admin, "hide", "oid", "regclass")
        _plant_operator(admin, "same", "varchar", "varchar")
        admin.execute("RESET ROLE")
        admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    for production in (False, True):
        with pytest.raises(MigrationError, match="public 스키마에 객체를 소유합니다") as error:
            _migrate(db, app_role=role, production=production, db_dir=db_dir)
        for identity in ("operator public.=(pg_catalog.oid,pg_catalog.regclass)",
                         "operator public.=(character varying,character varying)",
                         "function public.hide(pg_catalog.oid,pg_catalog.regclass)"):
            assert identity in str(error.value)
    assert _hits(db) == []
    assert "99_pending_compare.sql" not in _ledger(db)


def test_runner_own_sql_ignores_operators_any_role_planted_in_public(cluster):
    """lock 연결(search_path = pg_catalog)의 SQL은 public에 심은 연산자를 소유자와 상관없이 부르지 않는다: 소유 객체 검사가 보지
    않는 다른 역할이 심은 것도 서비스 계정 검사·원장 읽기·시드 해시 확인에서 실행되지 않는다 (deploy/CHECKLIST.md의 기존 DB 단계가 찾는다)."""
    from app.migrate import MigrationError

    new_role, new_db = cluster
    app, other, db = new_role(), new_role("NOLOGIN"), new_db()
    _migrate(db)
    _log_table(db)
    with _connect(db) as admin:
        admin.execute("INSERT INTO users (email, name, subject_did, password_hash)"
                      " VALUES ('a@test.com', 'a', 'did:example:a', 'h')")
        admin.execute("GRANT CREATE ON SCHEMA public TO PUBLIC")
        admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(other)))
        _plant_operator(admin, "hide", "oid", "regclass")
        _plant_operator(admin, "same", "varchar", "varchar")
        admin.execute("RESET ROLE")
        admin.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    for production in (False, True):
        try:  # 거부 여부와 상관없이 실행기 자신의 SQL이 심은 함수를 부르지 않아야 한다
            _migrate(db, app_role=app, production=production)
        except MigrationError:
            pass
    assert _hits(db) == []


@pytest.mark.parametrize("owner", ["pg_database_owner", "pg_monitor"])
def test_objects_given_to_predefined_roles_in_public_are_refused(cluster, tmp_path, owner):
    """기본 제공 역할(pg_database_owner·pg_monitor 등)이 소유한 객체는 소유 기록(pg_shdepend)이 없어도 거부한다.

    pg_database_owner: 서비스 계정이 DB를 소유하던 동안 public에 심어 pg_database_owner에게 준 연산자는, 메시지대로 DB 소유자를
    마이그레이션 계정으로 바꾸면 마이그레이션 계정의 것이 된다. pg_monitor: 서비스 계정이 속한 역할에 준 연산자(예전 ACL에서
    심음). 둘 다 그 객체를 알려 주며 거부하고 심은 함수는 실행되지 않으며, 지우면 통과한다.
    """
    from app.migrate import MigrationError

    new_role, new_db = cluster
    app, db = new_role(), new_db()
    names = {"db": db, "app": app, "owner": owner}
    _migrate(db)
    db_dir = _db_dir_with(tmp_path, "99_pending_compare.sql",
                          "UPDATE public.users SET name = name WHERE email = 'nobody@example.com';\n")
    _log_table(db)
    _as_admin(db, "INSERT INTO users (email, name, subject_did, password_hash)"
              " VALUES ('a@test.com', 'a', 'did:example:a', 'h')")
    if owner == "pg_database_owner":  # 서비스 계정이 DB 소유자(pg_database_owner로 public 스키마도 소유)
        _as_admin("postgres", "ALTER DATABASE {db} OWNER TO {app}", **names)
    else:  # PostgreSQL 14 이하 기본값처럼 PUBLIC에 CREATE, 서비스 계정은 pg_monitor 소속
        _as_admin(db, "GRANT CREATE ON SCHEMA public TO PUBLIC", "GRANT {owner} TO {app}", **names)
    with _connect(db) as admin:
        admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(app)))  # 서비스 계정으로 심어 기본 제공 역할에게 준다
        _plant_operator(admin, "same", "varchar", "varchar")
        admin.execute(sql.SQL("ALTER FUNCTION public.same(varchar, varchar) OWNER TO {}").format(sql.Identifier(owner)))
        admin.execute(sql.SQL("ALTER OPERATOR public.=(varchar, varchar) OWNER TO {}").format(sql.Identifier(owner)))
        admin.execute("RESET ROLE")
    if owner == "pg_database_owner":  # 처음에는 DB 소유로 거부하고, 메시지의 ALTER DATABASE 문을 실행한다
        with pytest.raises(MigrationError, match="DB 소유자") as error:
            _migrate(db, app_role=app, production=True, db_dir=db_dir)
        _as_admin(db, *re.findall(r"ALTER DATABASE [^;]+;", str(error.value)))
    else:
        _as_admin(db, "REVOKE CREATE ON SCHEMA public FROM PUBLIC")  # deploy/CHECKLIST.md 단계
    with pytest.raises(MigrationError, match="public 스키마에 객체를 소유합니다") as error:
        _migrate(db, app_role=app, production=True, db_dir=db_dir)
    assert "operator public.=(character varying,character varying)" in str(error.value)
    assert "function public.same(character varying,character varying)" in str(error.value)
    assert _hits(db) == []
    assert "99_pending_compare.sql" not in _ledger(db)
    _as_admin(db, "DROP OPERATOR public.=(varchar, varchar)", "DROP FUNCTION public.same(varchar, varchar)")
    assert _migrate(db, app_role=app, production=True, db_dir=db_dir) == ["99_pending_compare.sql"]


def test_trigger_privilege_and_triggers_calling_temp_functions_are_refused(app_role, tmp_path):
    """TRIGGER 권한(예전에 준 GRANT ALL 등)이 있는 서비스 계정은 마이그레이션이 바꾸는 테이블에 임시 스키마(pg_temp)의 함수를 부르는
    트리거를 걸어 마이그레이션 계정의 권한으로 실행하게 할 수 있다. 그 권한과 그런 트리거를 남은 마이그레이션보다 먼저 거부하고,
    메시지의 REVOKE를 실행하고 그 세션이 끝나면(트리거가 함께 지워짐) 통과한다. 권한은 DML만 남는다(예전의 TRUNCATE 등도 거둠).
    TRIGGER 권한 메시지는 그 권한으로 이미 만든 트리거가 권한을 거둔 뒤에도 남는다고 말하고 public 테이블의 트리거를 모두 알려
    준다(pg_catalog 함수만 부르는 트리거 keep 포함. keep은 그것만으로는 거부하지 않는다).
    """
    from app.migrate import MigrationError

    db, role, as_app = app_role
    _migrate(db, app_role=role)
    db_dir = _db_dir_with(tmp_path, "99_backfill_probe.sql", "UPDATE public.users SET name = name;\n")
    _log_table(db)
    # 앱 스키마에는 이제 시퀀스가 없다 (하나뿐이던 user_skills_id_seq를 13 마이그레이션이 표와 함께 지웠다).
    # 아래의 시퀀스 권한 검사가 계속 뜻이 있도록 시퀀스가 딸린 표를 하나 둔다 (다음 배포에서 생길 수 있는 표).
    _as_admin(db, "CREATE TABLE public.sequence_probe (id BIGSERIAL PRIMARY KEY)")
    _as_admin(db, "GRANT ALL ON ALL TABLES IN SCHEMA public TO {role}", role=role)
    with psycopg.connect(**as_app, autocommit=True) as app:  # 마이그레이션 태스크가 도는 동안 열려 있는 BE 연결
        app.execute("CREATE FUNCTION pg_temp.probe() RETURNS trigger LANGUAGE plpgsql AS"
                    " $$ BEGIN INSERT INTO vclog.hits VALUES (current_user, 'pg_temp trigger'); RETURN NULL; END $$")
        app.execute("CREATE TRIGGER probe AFTER UPDATE ON public.users FOR EACH STATEMENT"
                    " EXECUTE FUNCTION pg_temp.probe()")
        app.execute("CREATE TRIGGER keep BEFORE UPDATE ON public.users FOR EACH ROW"
                    " EXECUTE FUNCTION pg_catalog.suppress_redundant_updates_trigger()")
        with pytest.raises(MigrationError, match="TRIGGER 권한") as error:
            _migrate(db, app_role=role, db_dir=db_dir)
        message = str(error.value)
        assert "trigger probe on public.users" in message and "권한을 거둔 뒤에도 남" in message
        assert "['trigger keep on public.users', 'trigger probe on public.users']" in message
        statements = re.findall(r"REVOKE TRIGGER ON [^;]+;", message)
        with _connect(db) as admin:
            role_q = sql.Identifier(role).as_string(admin)
            assert statements == [f"REVOKE TRIGGER ON ALL TABLES IN SCHEMA public FROM {role_q};"]
            # GRANT ALL은 REFERENCES와 원장 쓰기 권한도 주므로 그 REVOKE 문도 나온다: 메시지의 REVOKE 문을 모두 실행한다.
            statements = re.findall(r"REVOKE [A-Z]+ ON [^;]+;", message)
            assert statements[1:] == [f"REVOKE REFERENCES ON ALL TABLES IN SCHEMA public FROM {role_q};",
                                      f"REVOKE ALL ON public.schema_migrations FROM {role_q};"]
            for statement in statements:
                admin.execute(statement)
        with pytest.raises(MigrationError, match="트리거가 임시 스키마의 함수") as error:  # 이미 걸린 트리거는 그 세션 동안 남는다
            _migrate(db, app_role=role, db_dir=db_dir)
        assert "TRIGGER 권한" not in str(error.value) and "trigger keep" not in str(error.value)
        assert "REFERENCES 권한" not in str(error.value) and "원장" not in str(error.value)
    for _ in range(100):  # 세션이 끝나면 임시 함수와 함께 트리거가 지워진다
        if not _scalar(db, "SELECT count(*) FROM pg_trigger WHERE tgname = 'probe'"):
            break
        time.sleep(0.05)
    assert _hits(db) == [] and "99_backfill_probe.sql" not in _ledger(db)
    assert _migrate(db, app_role=role, db_dir=db_dir) == ["99_backfill_probe.sql"]
    with _connect(db) as conn:
        granted = conn.execute(
            "SELECT c.relname, c.relkind, array_agg(a.privilege_type::text ORDER BY a.privilege_type)"
            " FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid = a.grantee"
            " WHERE c.relnamespace = 'public'::regnamespace AND r.rolname = %s GROUP BY 1, 2", (role,)).fetchall()
        tables = {name for (name,) in conn.execute(
            "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'")}
    assert {name: privileges for name, kind, privileges in granted if kind == "r"} == {
        name: ["DELETE", "INSERT", "SELECT", "UPDATE"] for name in tables - {"schema_migrations"}}
    assert {tuple(privileges) for _, kind, privileges in granted if kind == "S"} == {("SELECT", "USAGE")}


@pytest.mark.parametrize("case", ["temp_function", "rds_master", "app_schema", "temp_operator", "sql_text"])
def test_trigger_when_clauses_stay_refused_after_the_printed_revoke(cluster, tmp_path, case):
    """TRIGGER 권한(예전에 준 GRANT ALL)이 있던 동안 서비스 계정이 건 트리거는 메시지의 REVOKE TRIGGER를 실행한 뒤에도 남는다.
    트리거 함수가 pg_catalog의 것이어도 WHEN 조건은 그 테이블을 바꾸는 마이그레이션 계정의 권한으로 실행되므로(조건이 거짓이면
    트리거 함수는 불리지 않는다) WHEN 조건이 있는 트리거는 거부하고 목록에 알려 주며, 그 코드는 남은 마이그레이션에서 실행되지 않는다.
    temp_function: 임시 스키마의 함수(그 세션이 열려 있음), rds_master: 슈퍼유저가 아닌 마스터 사용자가 마이그레이션 계정,
    app_schema: 서비스 계정이 소유한 스키마의 함수(세션이 끝나도 남음), temp_operator: 임시 스키마의 연산자,
    sql_text: pg_catalog.query_to_xml에 넘긴 SQL 문자열(트리거에 의존 기록이 남지 않음).
    """
    from app.migrate import MigrationError, migrate

    new_role, new_db = cluster
    app, password = new_role(), secrets.token_urlsafe(24)
    as_migrator: dict = {}
    with _connect("postgres") as admin:
        admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(app), sql.Literal(password)))
        if case == "rds_master":
            master = new_role("LOGIN CREATEDB CREATEROLE")
            as_migrator = {"user": master, "password": secrets.token_urlsafe(24)}
            admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(master),
                                                                      sql.Literal(as_migrator["password"])))
    db = new_db(owner=as_migrator.get("user"))
    _as_admin(db, "CREATE EXTENSION IF NOT EXISTS pgcrypto", "CREATE EXTENSION IF NOT EXISTS vector")
    kwargs = {**_conn_kwargs(db), **as_migrator}
    assert migrate(app_role=app, **kwargs) == _names()
    db_dir = _db_dir_with(tmp_path, "99_backfill_probe.sql", "UPDATE public.users SET name = name;\n")
    _log_table(db)
    _as_admin(db, "INSERT INTO users (email, name, subject_did, password_hash)"
              " VALUES ('a@test.com', 'a', 'did:example:a', 'h')", "GRANT ALL ON ALL TABLES IN SCHEMA public TO {app}",
              app=app)
    if case in ("app_schema", "sql_text"):
        _as_admin(db, "CREATE SCHEMA app_schema AUTHORIZATION {app}", app=app)
    schema = "app_schema" if case in ("app_schema", "sql_text") else "pg_temp"
    level = "AFTER UPDATE ON public.users FOR EACH STATEMENT"
    conn = psycopg.connect(**{**_conn_kwargs(db), "user": app, "password": password}, autocommit=True)
    try:
        if case == "temp_operator":
            conn.execute("CREATE FUNCTION pg_temp.probe_op(text, text) RETURNS boolean LANGUAGE plpgsql AS"
                         f" $$ BEGIN INSERT INTO vclog.hits VALUES (current_user, '{case}'); RETURN false; END $$")
            conn.execute("CREATE OPERATOR pg_temp.=== (LEFTARG = text, RIGHTARG = text, FUNCTION = pg_temp.probe_op)")
            when, level = "NEW.name::text OPERATOR(pg_temp.===) 'x'::text", "BEFORE UPDATE ON public.users FOR EACH ROW"
        else:
            conn.execute(f"CREATE FUNCTION {schema}.probe_when() RETURNS boolean LANGUAGE plpgsql AS"
                         f" $$ BEGIN INSERT INTO vclog.hits VALUES (current_user, '{case}'); RETURN false; END $$")
            when = (f"pg_catalog.query_to_xml('SELECT {schema}.probe_when()', false, false, '') IS NULL"
                    if case == "sql_text" else f"{schema}.probe_when()")
        conn.execute(f"CREATE TRIGGER probe_when {level} WHEN ({when})"
                     " EXECUTE FUNCTION pg_catalog.suppress_redundant_updates_trigger()")
        with pytest.raises(MigrationError, match="TRIGGER 권한") as error:
            migrate(app_role=app, production=True, db_dir=db_dir, **kwargs)
        first = str(error.value)
        # 메시지의 REVOKE(GRANT ALL이 준 TRIGGER·REFERENCES·원장 쓰기)를 마이그레이션 계정으로 모두 실행
        with psycopg.connect(**kwargs, autocommit=True) as migrator:
            for statement in re.findall(r"REVOKE [A-Z]+ ON [^;]+;", first):
                migrator.execute(statement)
        if schema == "app_schema":
            conn.close()  # 서비스 계정 세션이 끝나도 그 스키마의 함수와 트리거는 남는다
        with pytest.raises(MigrationError, match="WHEN 조건이 있거나") as error:
            migrate(app_role=app, production=True, db_dir=db_dir, **kwargs)
        assert "TRIGGER 권한" not in str(error.value)
        assert "trigger probe_when on public.users (WHEN 조건)" in str(error.value)
        assert _hits(db) == [] and "99_backfill_probe.sql" not in _ledger(db)
        assert "권한을 거둔 뒤에도 남" in first and "['trigger probe_when on public.users']" in first
        # 서비스 계정이 소유한 스키마(app_schema)도 거부하고 알려 준다
        assert ('"app_schema" (소유자' in first) is (schema == "app_schema")
        _as_admin(db, "DROP TRIGGER probe_when ON public.users")  # 확인한 뒤 지운다
        _as_admin(db, *re.findall(r"ALTER SCHEMA [^;]+;", str(error.value)))  # 소유자만 바꾼다 (함수는 서비스 계정 소유로 남음)
        assert migrate(app_role=app, production=True, db_dir=db_dir, **kwargs) == ["99_backfill_probe.sql"]
    finally:
        conn.close()
    assert _hits(db) == []
    with _connect(db) as check:
        assert check.execute("SELECT has_table_privilege(%s, 'public.users', 'TRIGGER')", (app,)).fetchone()[0] is False


@pytest.mark.parametrize("fix", ["printed", "membership"])
@pytest.mark.parametrize("case", ["schema_create", "db_owner", "rds_master"])
def test_objects_planted_as_a_member_role_stay_refused_after_the_fix(cluster, tmp_path, case, fix):
    """서비스 계정이 CREATE를 가진(또는 DB를 소유한) 소속 역할로 SET ROLE해 public에 연산자를 심어 두면, 메시지의 문장을 나온 순서대로
    실행해도(printed: 소속은 그대로라 그 역할의 객체가 보인다) 소속만 거둬도(membership: 그 역할은 public에 CREATE가 있거나 DB를
    소유한 채 남는다) 다음 실행은 그 객체를 알려 주며 거부하고, 심은 함수는 남은 마이그레이션에서 실행되지 않는다.
    메시지는 소속 역할의 이름은 알려 주되 소속을 거두는 문장(REVOKE "<소속 역할>" FROM "<서비스 계정>")은 주지 않는다.
    rds_master: 슈퍼유저가 아닌 마스터 사용자가 서비스 계정·소속 역할을 만들고 소속을 준 구성 (마스터가 소속을 거둘 수 있다).
    """
    from app.migrate import MigrationError, migrate

    new_role, new_db = cluster
    as_migrator: dict = {}
    if case == "rds_master":
        master, password = new_role("LOGIN CREATEDB CREATEROLE"), secrets.token_urlsafe(24)
        with _connect("postgres") as admin:
            admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(master), sql.Literal(password)))
        db = new_db(owner=master)
        _as_admin(db, "CREATE EXTENSION IF NOT EXISTS pgcrypto", "CREATE EXTENSION IF NOT EXISTS vector")
        as_migrator = {"user": master, "password": password}
        app, group = (f"{TEST_DB_NAME}_role_{uuid.uuid4().hex[:8]}"[-63:] for _ in range(2))
    else:  # db_owner: 권한을 상속하지 않는 소속(NOINHERIT)이어도 SET ROLE로 DB 소유자가 된다
        app, group = new_role("LOGIN NOINHERIT" if case == "db_owner" else "LOGIN"), new_role("NOLOGIN")
        db = new_db()
    kwargs = {**_conn_kwargs(db), **as_migrator}
    names = {"db": db, "app": app, "group": group}
    try:
        if case == "rds_master":  # 마스터 사용자가 만든 역할 (PostgreSQL 16은 마스터에게 ADMIN 소속을 남긴다)
            with psycopg.connect(**kwargs, autocommit=True) as conn:
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(app)))
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(group)))
        migrate(**kwargs)  # 서비스 계정 없이 이미 마이그레이션한 DB
        db_dir = _db_dir_with(tmp_path, "99_pending_compare.sql",
                              "UPDATE public.users SET name = name WHERE email = 'nobody@example.com';\n")
        _log_table(db)
        _as_admin(db, "INSERT INTO users (email, name, subject_did, password_hash)"
                  " VALUES ('a@test.com', 'a', 'did:example:a', 'h')")
        with psycopg.connect(**kwargs, autocommit=True) as conn:  # 마이그레이션 계정이 준 권한·소속
            if case != "db_owner":
                conn.execute(sql.SQL("GRANT CREATE ON SCHEMA public TO {}").format(sql.Identifier(group)))
            conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(app)))
        if case == "db_owner":  # 소속 역할이 DB 소유자(pg_database_owner로 public 스키마도 소유), 자기 DB CREATE는 거둠
            _as_admin("postgres", "ALTER DATABASE {db} OWNER TO {group}", **names)
            _as_admin(db, "REVOKE CREATE ON DATABASE {db} FROM {group}", **names)
        with _connect(db) as admin:  # 서비스 계정이 소속 역할로 심는다
            admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(app)))
            admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(group)))
            _plant_operator(admin, "same", "varchar", "varchar")
            admin.execute("RESET ROLE")
        with pytest.raises(MigrationError, match="객체를 만들 수 있습니다") as error:
            migrate(app_role=app, production=True, db_dir=db_dir, **kwargs)
        message = str(error.value)
        with psycopg.connect(**kwargs, autocommit=True) as conn:  # 마이그레이션 계정으로
            if fix == "printed":  # 메시지의 문장을 나온 순서대로 그대로 실행
                for statement in re.findall(r"(?:GRANT|ALTER|REVOKE) [^;]+;", message):
                    conn.execute(statement)
            else:  # 소속만 거둔다 (예전 메시지가 주던 다른 방법)
                conn.execute(sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(group), sql.Identifier(app)))
            still_member = conn.execute("SELECT pg_has_role(%s, %s, 'MEMBER')", (app, group)).fetchone()[0]
        assert still_member is (fix == "printed")
        with pytest.raises(MigrationError, match="public 스키마에 객체를 소유합니다") as error:
            migrate(app_role=app, production=True, db_dir=db_dir, **kwargs)
        assert "operator public.=(character varying,character varying)" in str(error.value)
        assert "function public.same(character varying,character varying)" in str(error.value)
        assert _hits(db) == [] and "99_pending_compare.sql" not in _ledger(db)
        with _connect(db) as conn:
            quoted = {key: sql.Identifier(value).as_string(conn) for key, value in names.items()}
        assert f" 역할 {quoted['group']}" in message  # 소속 역할의 이름은 알려 준다 (CREATE를 받은 소속 역할·DB 소유자)
        assert f"REVOKE {quoted['group']} FROM {quoted['app']}" not in message
        _as_admin(db, "DROP OPERATOR public.=(varchar, varchar)", "DROP FUNCTION public.same(varchar, varchar)")
        assert migrate(app_role=app, production=True, db_dir=db_dir, **kwargs) == ["99_pending_compare.sql"]
        assert _hits(db) == []
    finally:  # 마스터 사용자가 만든 역할은 픽스처 목록에 없다: DB를 먼저 지우고 역할을 지운다
        if case == "rds_master":
            with _connect("postgres") as admin:
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
                for name in (app, group):
                    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.mark.parametrize("case", ["schema", "db", "db_without_extensions", "db_and_schema"])
def test_owner_fixes_for_a_non_superuser_master_run_in_order(cluster, case):
    """deploy/CHECKLIST.md 'DB 마이그레이션'처럼 슈퍼유저가 아닌 마스터 사용자(LOGIN CREATEDB CREATEROLE)가 DB를 만들고 서비스 계정을
    만든 구성에서, 서비스 계정이 public 스키마나 DB를 소유하고 그 몫의 CREATE를 이미 거둔 상태(소유자를 바꾸기 전에 REVOKE를
    먼저 실행함)면 메시지는 지금 소유자의 권한을 받는 GRANT, ALTER ... OWNER, 마이그레이션에 필요한 CREATE를 다시 주는 GRANT를
    순서대로 준다(DB CREATE는 public 스키마 소유자를 바꿀 때만. 기본 스키마가 확장을 만들지 않으므로 확장이 하나도 없는 DB
    (db_without_extensions)에서도 DB CREATE 없이 통과한다). 마스터 사용자가 나온 순서대로 실행하면 빈 DB의 마이그레이션이
    권한 오류 없이 통과한다.
    """
    from app.migrate import MigrationError, migrate

    new_role, new_db = cluster
    master, password = new_role("LOGIN CREATEDB CREATEROLE"), secrets.token_urlsafe(24)
    app = f"{TEST_DB_NAME}_role_{uuid.uuid4().hex[:8]}"[-63:]
    with _connect("postgres") as admin:
        admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(master), sql.Literal(password)))
    db = new_db(owner=master)
    _as_admin(db, *(f"CREATE EXTENSION IF NOT EXISTS {name}"  # 예전 버전이 만든 확장이 남은 DB (없는 DB도 본다)
                    for name in (() if case == "db_without_extensions" else ("pgcrypto", "vector"))))
    as_master = {**_conn_kwargs(db), "user": master, "password": password}
    with psycopg.connect(**as_master, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(app)))
    names = {"db": db, "app": app, "master": master}
    try:
        if case != "schema":  # 서비스 계정이 DB를 소유하고 자기 DB CREATE와 pg_database_owner의 public CREATE는 거둠
            _as_admin(db, "ALTER DATABASE {db} OWNER TO {app}", "REVOKE CREATE ON DATABASE {db} FROM {app}", **names)
        if case in ("schema", "db_and_schema"):  # 서비스 계정이 public 스키마를 소유하고 자기 CREATE는 거둠
            _as_admin(db, "ALTER SCHEMA public OWNER TO {app}", "REVOKE CREATE ON SCHEMA public FROM {app}", **names)
        else:
            _as_admin(db, "REVOKE CREATE ON SCHEMA public FROM pg_database_owner")
        with pytest.raises(MigrationError) as error:
            migrate(app_role=app, **as_master)
        statements = re.findall(r"(?:GRANT|ALTER|REVOKE) [^;]+;", str(error.value))
        with psycopg.connect(**as_master, autocommit=True) as conn:  # 마스터 사용자가 나온 순서대로 실행
            for statement in statements:
                conn.execute(statement)
        assert migrate(app_role=app, **as_master) == _names()
        with _connect(db) as conn:
            assert conn.execute(_CAN_INSERT, (app,)).fetchone()[0] is True
            q = {key: sql.Identifier(value).as_string(conn) for key, value in names.items()}
            inherit = " WITH INHERIT TRUE" if conn.info.server_version >= 160000 else ""
        expected = [f"GRANT {q['app']} TO {q['master']}{inherit};"]
        if case != "schema":
            expected.append(f"ALTER DATABASE {q['db']} OWNER TO {q['master']};")
            if case == "db_and_schema":
                expected.append(f"GRANT CREATE ON DATABASE {q['db']} TO {q['master']};")
        if case in ("schema", "db_and_schema"):
            expected.append("ALTER SCHEMA public OWNER TO pg_database_owner;")
        expected.append("GRANT CREATE ON SCHEMA public TO pg_database_owner;")
        assert statements == expected  # 필요한 문장만 (DB CREATE는 public 스키마 소유자를 바꿀 때만)
    finally:  # 마스터 사용자가 만든 역할이라 픽스처 목록에 없다: DB(서비스 계정이 소유했을 수 있음)를 먼저 지우고 역할을 지운다
        with _connect("postgres") as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(app)))


def _rds_master(new_role, new_db) -> tuple[str, dict]:
    """슈퍼유저가 아닌 마스터 사용자(LOGIN CREATEDB CREATEROLE)가 소유한 임시 DB(예전 버전이 만든 확장이 남은 DB처럼 슈퍼유저가
    먼저 만들어 둠)와 그 접속 인자."""
    master, password = new_role("LOGIN CREATEDB CREATEROLE"), secrets.token_urlsafe(24)
    with _connect("postgres") as admin:
        admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(master), sql.Literal(password)))
    db = new_db(owner=master)
    _as_admin(db, "CREATE EXTENSION IF NOT EXISTS pgcrypto", "CREATE EXTENSION IF NOT EXISTS vector")
    return db, {**_conn_kwargs(db), "user": master, "password": password}


@pytest.mark.parametrize("holder, schema_owner, remedy, rds", [
    ("group", "app", "DROP SCHEMA", False), ("public", "app", "ALTER SCHEMA", False),
    ("group", "group", "ALTER SCHEMA", False), ("group", "app", "DROP SCHEMA", True)])
def test_references_and_ledger_writes_through_a_group_or_public_are_refused(cluster, tmp_path, holder, schema_owner,
                                                                           remedy, rds):
    """예전에 준 GRANT ALL ON ALL TABLES IN SCHEMA public이 서비스 계정이 속한 역할(group)이나 PUBLIC에 남아 있으면, 실행이 끝날 때
    거두는 그 계정 자신의 권한과 달리 REFERENCES와 원장 쓰기 권한이 그대로 남는다. 외래 키 검사는 참조되는 테이블 소유자(마이그레이션
    계정)의 권한으로 실행되므로 서비스 계정은 자기 스키마(예전 DB CREATE로 만든 appsch)에 public.users를 참조하는 외래 키와 암시적
    캐스트를 두고 행을 넣을 때마다 그 함수를 마이그레이션 계정의 권한으로 실행하게 할 수 있다. 원장에 행을 넣으면 남은 마이그레이션을
    건너뛰게 할 수 있다. 실행기는 남은 마이그레이션보다 먼저 거부하고 권한을 받은 역할마다 REVOKE 문, 그 권한으로 이미 만든 외래 키,
    그 스키마와 DROP SCHEMA ... CASCADE·ALTER SCHEMA ... OWNER 문(슈퍼유저가 아닌 마스터 사용자면 먼저 소유자의 권한을 받는 GRANT
    문)을 알려 준다. 나온 순서대로 실행하면 통과하고, 서비스 계정은 심어 둔 외래 키 테이블에 닿지 못하며 새로 심을 수도 없다
    (심어 둔 캐스트는 한 번도 실행되지 않는다).
    """
    from app.migrate import MigrationError, migrate

    new_role, new_db = cluster
    if rds:  # 마스터 사용자가 서비스 계정·소속 역할을 만든 구성 (마스터에게 두 역할의 ADMIN 권한이 남는다)
        db, kwargs = _rds_master(new_role, new_db)
        app, group = (f"{TEST_DB_NAME}_role_{uuid.uuid4().hex[:8]}"[-63:] for _ in range(2))
    else:
        app, group, db = new_role(), new_role("NOLOGIN"), new_db()
        kwargs = _conn_kwargs(db)
    names = {"app": app, "group": group, "owner": app if schema_owner == "app" else group}
    as_app = {**_conn_kwargs(db), "user": app, "password": secrets.token_urlsafe(24)}
    try:
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            if rds:
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(app)))
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(group)))
        with _connect("postgres") as admin:
            admin.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(app), sql.Literal(as_app["password"])))
        migrate(**kwargs)  # 서비스 계정 없이 이미 마이그레이션한 DB
        db_dir = _db_dir_with(tmp_path, "99_pending_compare.sql",
                              "UPDATE public.users SET name = name WHERE email = 'nobody@example.com';\n")
        _log_table(db)
        with psycopg.connect(**kwargs, autocommit=True) as conn:  # 마이그레이션 계정이 예전에 준 GRANT ALL과 소속
            user_id = conn.execute("INSERT INTO users (email, name, subject_did, password_hash)"
                                   " VALUES ('a@test.com', 'a', 'did:example:a', 'h') RETURNING id").fetchone()[0]
            target = sql.SQL("PUBLIC") if holder == "public" else sql.Identifier(group)
            conn.execute(sql.SQL("GRANT ALL ON ALL TABLES IN SCHEMA public TO {}").format(target))
            if "group" in (holder, schema_owner):
                conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(app)))
        _as_admin(db, "CREATE SCHEMA appsch AUTHORIZATION {owner}", **names)  # 예전 DB CREATE 권한으로 만든 스키마
        with psycopg.connect(**as_app, autocommit=True) as conn:  # 예전 권한이 남아 있는 동안 서비스 계정이 심는다 (행은 넣지 않음)
            conn.execute("CREATE TYPE appsch.vc_ct AS (v uuid)")
            conn.execute("CREATE FUNCTION appsch.vc_cast(appsch.vc_ct) RETURNS uuid LANGUAGE plpgsql AS $$ BEGIN"
                         " INSERT INTO vclog.hits VALUES (current_user, 'cast in RI check'); RETURN ($1).v; END $$")
            conn.execute("CREATE CAST (appsch.vc_ct AS uuid) WITH FUNCTION appsch.vc_cast(appsch.vc_ct) AS IMPLICIT")
            conn.execute("CREATE TABLE appsch.vc_fk (u appsch.vc_ct REFERENCES public.users (id))")
        with pytest.raises(MigrationError) as error:
            migrate(app_role=app, production=True, db_dir=db_dir, **kwargs)
        message = str(error.value)
        assert _hits(db) == [] and "99_pending_compare.sql" not in _ledger(db)
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            q = {key: sql.Identifier(value).as_string(conn) for key, value in names.items()}
            migrator_q = sql.Identifier(conn.execute("SELECT current_user").fetchone()[0]).as_string(conn)
            inherit = " WITH INHERIT TRUE" if conn.info.server_version >= 160000 else ""
        held = "PUBLIC" if holder == "public" else q["group"]
        for hint in (f"REVOKE TRIGGER ON ALL TABLES IN SCHEMA public FROM {held};",
                     f"REVOKE REFERENCES ON ALL TABLES IN SCHEMA public FROM {held};",
                     f"REVOKE ALL ON public.schema_migrations FROM {held};",
                     "['constraint vc_fk_u_fkey on appsch.vc_fk']", f"""['"appsch" (소유자 {q["owner"]})']""",
                     'DROP SCHEMA "appsch" CASCADE;', f'ALTER SCHEMA "appsch" OWNER TO {migrator_q};'):
            assert hint in message
        assert (f"GRANT {q['owner']} TO {migrator_q}{inherit};" in message) is rds
        # 마이그레이션 계정으로 메시지의 문장을 나온 순서대로 실행한다 (스키마는 DROP SCHEMA나 ALTER SCHEMA 중 하나)
        statements = re.findall(rf"(?:GRANT|REVOKE [A-Z]+ ON|{remedy}) [^;]+;", message)
        assert len(statements) == 4 + rds
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            for statement in statements:
                conn.execute(statement)
        assert migrate(app_role=app, production=True, db_dir=db_dir, **kwargs) == ["99_pending_compare.sql"]
        with psycopg.connect(**as_app, autocommit=True) as conn:
            for statement in (f"INSERT INTO appsch.vc_fk VALUES (ROW('{user_id}'::uuid)::appsch.vc_ct)",
                              "CREATE TABLE appsch.vc_fk2 (u uuid REFERENCES public.users (id))",
                              "CREATE SCHEMA vc_new", "CREATE TABLE public.vc_fk3 (u uuid)"):
                with pytest.raises((psycopg.errors.InsufficientPrivilege, psycopg.errors.InvalidSchemaName,
                                    psycopg.errors.UndefinedTable)):  # 지운 스키마는 없는 테이블·스키마로 나온다
                    conn.execute(statement)
            assert conn.execute("SELECT has_any_column_privilege('public.users', 'REFERENCES'),"
                                " has_table_privilege('public.schema_migrations', 'INSERT, UPDATE, DELETE, TRUNCATE'),"
                                " has_any_column_privilege('public.schema_migrations', 'INSERT, UPDATE')"
                                ).fetchone() == (False, False, False)
        assert migrate(app_role=app, production=True, db_dir=db_dir, **kwargs) == []
        assert _hits(db) == []
    finally:  # 마스터 사용자가 만든 역할은 픽스처 목록에 없다: DB를 먼저 지우고 역할을 지운다
        if rds:
            with _connect("postgres") as admin:
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
                for name in (app, group):
                    admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(name)))


@pytest.mark.parametrize("grant, holder", [
    ("GRANT REFERENCES (id) ON public.users TO {group}", "group"),
    ("GRANT REFERENCES ON public.shares TO {app}", "app"),
    ("GRANT INSERT (filename, checksum) ON public.schema_migrations TO PUBLIC", "PUBLIC"),
    ("GRANT UPDATE (checksum) ON public.schema_migrations TO {group}", "group"),
    ("GRANT DELETE, TRUNCATE ON public.schema_migrations TO {app}", "app")])
def test_references_or_ledger_write_alone_is_refused_with_a_runnable_revoke(cluster, tmp_path, grant, holder):
    """REFERENCES(열 단위 포함)나 원장의 INSERT·UPDATE(열 단위 포함)·DELETE·TRUNCATE 하나만 있어도, 그 역할 자신·속한 역할(NOINHERIT
    소속 포함)·PUBLIC 어느 쪽에 있든 남은 마이그레이션보다 먼저 거부한다. 메시지는 그 권한을 받은 역할의 REVOKE 문 하나만 알려
    주고(테이블 단위 REVOKE는 열 단위 권한도 거둔다), 그 문을 실행하면 통과한다."""
    from app.migrate import MigrationError

    new_role, new_db = cluster
    app, group, db = new_role("LOGIN NOINHERIT"), new_role("NOLOGIN"), new_db()
    names = {"app": app, "group": group}
    _migrate(db)
    db_dir = _db_dir_with(tmp_path, "99_pending_compare.sql",
                          "UPDATE public.users SET name = name WHERE email = 'nobody@example.com';\n")
    _as_admin("postgres", "GRANT {group} TO {app}", **names)
    _as_admin(db, grant, **names)
    with pytest.raises(MigrationError) as error:
        _migrate(db, app_role=app, db_dir=db_dir)
    message = str(error.value)
    with _connect(db) as conn:
        target = "PUBLIC" if holder == "PUBLIC" else sql.Identifier(names[holder]).as_string(conn)
    expected = (f"REVOKE REFERENCES ON ALL TABLES IN SCHEMA public FROM {target};" if "REFERENCES" in grant
                else f"REVOKE ALL ON public.schema_migrations FROM {target};")
    assert re.findall(r"REVOKE [A-Z]+ ON [^;]+;", message) == [expected]
    assert "TRIGGER 권한" not in message and "스키마를 소유합니다" not in message
    assert "99_pending_compare.sql" not in _ledger(db)
    with _connect(db) as conn:
        conn.execute(expected)
    assert _migrate(db, app_role=app, db_dir=db_dir) == ["99_pending_compare.sql"]


@pytest.mark.parametrize("owner, rds", [("app", False), ("group", False), ("app", True)])
def test_schemas_owned_by_the_app_role_are_refused_before_any_migration(cluster, owner, rds):
    """서비스 계정이나 그 역할이 속한 역할이 소유한 스키마(예전 DB CREATE 권한으로 만든 것)에는 타입·캐스트·함수를 둘 수 있으므로
    (REFERENCES와 함께 쓰면 외래 키 검사로 마이그레이션 계정의 권한으로 실행됨) 다른 권한이 없어도 거부한다. 검사는 마이그레이션
    SQL보다 먼저라 빈 DB는 빈 채로 남는다. 메시지는 스키마와 소유자, DROP SCHEMA ... CASCADE 또는 ALTER SCHEMA ... OWNER 문(슈퍼유저가
    아닌 마스터 사용자면 먼저 지금 소유자의 권한을 받는 GRANT 문)을 알려 주고, GRANT·ALTER 문을 나온 순서대로 실행하면 통과한다."""
    from app.migrate import MigrationError, migrate

    new_role, new_db = cluster
    if rds:  # 마스터 사용자가 만든 서비스 계정 (마스터에게 ADMIN 권한이 남는다)
        db, kwargs = _rds_master(new_role, new_db)
        app, group = f"{TEST_DB_NAME}_role_{uuid.uuid4().hex[:8]}"[-63:], None
        with psycopg.connect(**kwargs, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(app)))
    else:
        app, group, db = new_role("LOGIN NOINHERIT"), new_role("NOLOGIN"), new_db()
        kwargs = _conn_kwargs(db)
        _as_admin("postgres", "GRANT {group} TO {app}", group=group, app=app)
    try:
        _as_admin(db, "CREATE SCHEMA legacy AUTHORIZATION {owner}", owner=app if owner == "app" else group)
        with pytest.raises(MigrationError, match="스키마를 소유합니다") as error:
            migrate(app_role=app, **kwargs)
        message = str(error.value)
        assert _scalar(db, "SELECT to_regclass('public.users') IS NULL AND to_regclass('public.schema_migrations') IS NULL")
        assert "REVOKE" not in message
        with psycopg.connect(**kwargs, autocommit=True) as conn:  # 마이그레이션 계정으로 나온 순서대로 실행
            owner_q = sql.Identifier(app if owner == "app" else group).as_string(conn)
            migrator_q = sql.Identifier(conn.execute("SELECT current_user").fetchone()[0]).as_string(conn)
            inherit = " WITH INHERIT TRUE" if conn.info.server_version >= 160000 else ""
            statements = re.findall(r"(?:GRANT|ALTER SCHEMA) [^;]+;", message)
            assert statements == ([f"GRANT {owner_q} TO {migrator_q}{inherit};"] if rds else []) + [
                f'ALTER SCHEMA "legacy" OWNER TO {migrator_q};']
            assert f"""['"legacy" (소유자 {owner_q})']""" in message and 'DROP SCHEMA "legacy" CASCADE;' in message
            for statement in statements:
                conn.execute(statement)
        assert migrate(app_role=app, **kwargs) == _names()
    finally:  # 마스터 사용자가 만든 역할은 픽스처 목록에 없다: DB를 먼저 지우고 역할을 지운다
        if rds:
            with _connect("postgres") as admin:
                admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(app)))


# ── 13 마이그레이션: 이메일 유일성(대소문자 무시) + 쓰지 않는 인덱스·표 정리 ─────────────

MIGRATION_13 = "13_email_unique_drop_unused_tables.sql"
# 13 이전의 기본 스키마(DB/schema)가 만들던 확장과 표 (그때의 SQL 그대로). 그 버전으로 만든 DB·Compose 볼륨·RDS에 남아 있다.
_LEGACY_USER_SKILLS = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE TABLE user_skills (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill_id            VARCHAR(100) NOT NULL,
    label_ko            VARCHAR(100) NOT NULL,
    label_en            VARCHAR(100),
    taxonomy_source     VARCHAR(50),
    taxonomy_code       VARCHAR(50),
    level               NUMERIC,
    confidence          NUMERIC DEFAULT 1.0,
    source_record_ids   UUID[] NOT NULL DEFAULT '{}',
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, skill_id)
);
CREATE INDEX idx_user_skills_user ON user_skills(user_id);
CREATE INDEX idx_user_skills_sources ON user_skills USING gin(source_record_ids);
"""
_LEGACY_EMBEDDINGS = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE student_credential_embeddings (
    record_id           UUID PRIMARY KEY REFERENCES credentials(record_id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content_text        TEXT NOT NULL,
    source_text_hash    VARCHAR(64) NOT NULL,
    embedding           vector(1536) NOT NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_cred_embeddings_hnsw ON student_credential_embeddings
USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
"""


def _db_dir_before_13(tmp_path):
    """13 마이그레이션이 생기기 전의 DB 폴더 (12까지만 있다)."""
    db_dir = tmp_path / "DB"
    shutil.copytree(BE_DIR / "DB", db_dir)
    (db_dir / "migrations" / MIGRATION_13).unlink()
    return db_dir


def _add_legacy_objects(dbname: str) -> list[str]:
    """예전 기본 스키마가 만들던 확장·표를 마이그레이션 계정(슈퍼유저)으로 만들고, 그 DB의 확장 목록을 돌려준다.

    vector 확장이 없는 서버(pgvector 이미지가 아닌 PostgreSQL)에서는 임베딩 표만 건너뛴다.
    """
    with _connect(dbname) as admin:
        admin.execute(_LEGACY_USER_SKILLS)
        if admin.execute("SELECT count(*) FROM pg_available_extensions WHERE name = 'vector'").fetchone()[0]:
            admin.execute(_LEGACY_EMBEDDINGS)
        return [name for (name,) in admin.execute("SELECT extname::text FROM pg_extension ORDER BY 1")]


def test_database_made_before_13_is_upgraded_and_keeps_its_extensions(scratch_db, app_role, tmp_path):
    """예전 버전으로 만든 DB(쓰지 않는 표 두 개·중복 인덱스·pgcrypto·vector 확장이 있음)에 13만 적용된다.

    표(딸린 시퀀스·인덱스 포함)와 idx_users_did는 없어지고 확장은 그대로 남으며, 스키마는 새로 만든 DB와 같아진다.
    서비스 계정(DB_APP_ROLE) 검사도 확장이 public에 둔 객체(마이그레이션 계정 소유) 때문에 걸리지 않는다.
    """
    old, role, as_app = app_role
    fresh = scratch_db()
    assert _migrate(fresh, seed=True) == _names()
    assert _migrate(old, seed=True, app_role=role, db_dir=_db_dir_before_13(tmp_path)) == _names()[:-1]
    extensions = _add_legacy_objects(old)
    assert "pgcrypto" in extensions
    with _connect(old) as admin:  # 누군가 직접 넣어 둔 행이 있어도 표와 함께 없어진다 (어떤 코드도 읽지 않는다)
        admin.execute("INSERT INTO user_skills (user_id, skill_id, label_ko)"
                      " SELECT id, 'SK.TEST', '테스트' FROM users LIMIT 1")
    assert _scalar(old, "SELECT to_regclass('public.idx_users_did') IS NOT NULL"
                        " AND to_regclass('public.user_skills_id_seq') IS NOT NULL")

    assert _migrate(old, app_role=role) == [MIGRATION_13]
    assert _migrate(old, app_role=role) == []
    with _connect(old) as conn:
        assert conn.execute(
            "SELECT to_regclass('public.user_skills'), to_regclass('public.student_credential_embeddings'),"
            " to_regclass('public.user_skills_id_seq'), to_regclass('public.idx_cred_embeddings_hnsw'),"
            " to_regclass('public.idx_users_did')").fetchone() == (None,) * 5
        # 확장은 지우지 않는다 (DROP EXTENSION 없음)
        assert [name for (name,) in conn.execute("SELECT extname::text FROM pg_extension ORDER BY 1")] == extensions
    assert _catalog(old) == _catalog(fresh)
    with psycopg.connect(**as_app, autocommit=True) as conn:  # 서비스 계정은 지금처럼 앱 테이블을 쓴다
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == _scalar(fresh, "SELECT count(*) FROM users")
        conn.execute("INSERT INTO users (email, name, subject_did) VALUES ('legacy@test.com', 'r', 'did:example:legacy')")


def test_migration_13_stops_on_emails_that_differ_only_by_case(scratch_db, tmp_path, monkeypatch, caplog):
    """대소문자만 다른 이메일의 계정이 이미 있으면 13은 인덱스를 만들기 전에 알기 쉬운 오류로 멈춘다 (CLI 종료 코드 1).

    오류에는 겹치는 수만 있고 이메일 주소는 없다. 13의 다른 변경과 원장 기록도 함께 롤백되어 다시 실행할 수 있고,
    계정을 정리하면 통과한다. 그 뒤에는 DB가 대소문자만 다른 이메일의 두 번째 계정을 거절한다.
    """
    import app.migrate as runner

    db = scratch_db()
    _migrate(db, db_dir=_db_dir_before_13(tmp_path))
    _add_legacy_objects(db)
    with _connect(db) as conn:
        for email, did in (("Mixed@Example.Test", "did:example:m1"), ("mixed@example.test", "did:example:m2"),
                           ("other@example.test", "did:example:o1")):
            conn.execute("INSERT INTO users (email, name, subject_did) VALUES (%s, '테스트', %s)", (email, did))
    with pytest.raises(psycopg.errors.RaiseException, match="대소문자만 다른 이메일") as error:
        _migrate(db)
    assert "겹치는 이메일 1개" in str(error.value) and "mixed@example.test" not in str(error.value).lower()
    untouched = ("SELECT to_regclass('public.users_email_lower_key') IS NULL"
                 " AND to_regclass('public.idx_users_did') IS NOT NULL AND to_regclass('public.user_skills') IS NOT NULL")
    assert MIGRATION_13 not in _ledger(db) and _scalar(db, untouched)

    monkeypatch.setenv("DB_NAME", db)
    monkeypatch.setattr(runner, "load_dotenv", lambda *args, **kwargs: False)
    with caplog.at_level(logging.ERROR, logger="app.migrate"):
        assert runner.main([]) == 1
    assert "대소문자만 다른 이메일" in caplog.text and "mixed@example.test" not in caplog.text.lower()
    assert MIGRATION_13 not in _ledger(db) and _scalar(db, untouched)

    with _connect(db) as conn:  # 이메일마다 계정을 하나만 남긴다
        conn.execute("DELETE FROM users WHERE email = 'Mixed@Example.Test'")
    assert runner.main([]) == 0
    assert MIGRATION_13 in _ledger(db) and _scalar(db, "SELECT to_regclass('public.user_skills') IS NULL")
    with _connect(db) as conn, pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO users (email, name, subject_did) VALUES ('MIXED@example.test', 'n', 'did:example:m3')")


def test_email_lookup_uses_the_case_insensitive_unique_index(db):
    """로그인·Google 연동의 lower(email) 조회는 13의 인덱스를 타고, 대소문자만 다른 이메일로는 두 번째 계정을 만들 수 없다
    (가입 API는 소문자로 바꿔 넣지만, 그러지 않는 경로가 생겨도 DB가 막는다)."""
    from app.services.database import EmailAlreadyRegistered, db_service

    email = f"case-{uuid.uuid4().hex[:10]}@test.com"
    user = db_service.create_user(email=email, name="테스트", did=f"did:example:{uuid.uuid4().hex}", password_hash="h")
    with pytest.raises(EmailAlreadyRegistered):
        db_service.create_user(email=email.upper(), name="테스트", did=f"did:example:{uuid.uuid4().hex}",
                               password_hash="h")
    assert db_service.get_user_by_email(email.upper())["id"] == user["id"]
    index = db.execute(
        "SELECT indisunique, indisvalid, indisready, indcheckxmin, pg_get_indexdef(indexrelid) AS definition"
        " FROM pg_index WHERE indexrelid = 'public.users_email_lower_key'::regclass").fetchone()
    assert index["indisunique"] and index["indisvalid"] and index["indisready"]
    assert index["definition"].endswith("ON public.users USING btree (lower((email)::text))")
    if index["indcheckxmin"]:
        # 인덱스를 만들 때 같은 PostgreSQL 서버(다른 DB 포함)에 그보다 오래된 트랜잭션이 열려 있었다 (04가 방금 고친 시드 행
        # 때문에 붙는 표시다). 그 트랜잭션들이 끝날 때까지 플래너가 이 인덱스를 쓰지 않으므로 계획은 보지 않는다: 이 서버를
        # 함께 쓰는 다른 세션에 따라 결과가 달라지기 때문이다. 유일성은 위에서 본 대로 그동안에도 지켜진다.
        return
    with db.transaction():
        db.execute("SET LOCAL enable_seqscan = off")  # 표가 작으면 순차 탐색을 고르므로 인덱스를 쓸 수 있는지만 본다
        plan = "\n".join(row["QUERY PLAN"] for row in db.execute(
            "EXPLAIN SELECT * FROM users WHERE lower(email) = lower('USER001@example.com')"))
    assert "users_email_lower_key" in plan


# ── 09·10 마이그레이션의 열·표를 쓰는 DB 함수 ───────────────────────────

def _new_user(db) -> str:
    row = db.execute("INSERT INTO users (email, name, subject_did) VALUES (%s, %s, %s) RETURNING id",
                     (f"m-{uuid.uuid4().hex[:10]}@test.com", "테스트", f"did:example:{uuid.uuid4().hex}")).fetchone()
    return str(row["id"])


def test_token_version_starts_at_zero_and_logout_bumps_it(db):
    from app.services.database import db_service

    user_id, other = _new_user(db), _new_user(db)
    assert db_service.get_token_version(user_id) == 0  # 09의 기본값: 이 열이 생기기 전 토큰(ver 없음)과 같은 0
    db_service.bump_token_version(user_id)
    db_service.bump_token_version(user_id)
    version = db_service.get_token_version(user_id)
    assert version == 2 and type(version) is int
    assert db_service.get_token_version(other) == 0  # 다른 사용자의 토큰은 그대로
    missing = str(uuid.uuid4())
    assert db_service.get_token_version(missing) is None  # 없는 사용자 → None (토큰 거절)
    db_service.bump_token_version(missing)  # 없는 사용자는 아무것도 바꾸지 않는다
    assert db_service.get_token_version(missing) is None


def test_mock_issuance_reservation_counts_last_24_hours_per_user(db):
    from app.services.database import db_service

    alice, bob = _new_user(db), _new_user(db)
    # 예약은 (발급 기록 id, None), 한도에 닿으면 (None, 막은 한도)를 돌려준다.
    first, _ = db_service.reserve_mock_issuance(alice, "tpl-default", 2)
    second, _ = db_service.reserve_mock_issuance(alice, "tpl", 2)
    assert first and second and first != second
    # 한도에 닿으면 id가 없고 기록도 남기지 않는다
    assert db_service.reserve_mock_issuance(alice, "tpl-default", 2) == (None, "user")
    assert db_service.reserve_mock_issuance(bob, "tpl-default", 2)[0] is not None  # 사용자별로 센다

    digest = hashlib.sha256(b"header.payload.signature").hexdigest()
    db_service.complete_mock_issuance(first, digest)
    rows = {str(r["id"]): r for r in db.execute("SELECT * FROM mock_issuances WHERE user_id = %s", (alice,))}
    assert set(rows) == {first, second}
    assert (rows[first]["template_id"], rows[first]["token_sha256"]) == ("tpl-default", digest)
    assert (rows[second]["template_id"], rows[second]["token_sha256"]) == ("tpl", None)

    # 협력사 발급이 실패해 취소한 자리는 한도에 세지 않는다.
    db_service.cancel_mock_issuance(second)
    assert db_service.reserve_mock_issuance(alice, "tpl-default", 2)[0] is not None
    assert db_service.reserve_mock_issuance(alice, "tpl-default", 2)[0] is None

    # 24시간이 지난 기록은 세지 않는다.
    db.execute("UPDATE mock_issuances SET created_at = NOW() - INTERVAL '25 hours' WHERE id = %s", (first,))
    assert db_service.reserve_mock_issuance(alice, "tpl-default", 2)[0] is not None
    assert db_service.reserve_mock_issuance(alice, "tpl-default", 2)[0] is None

    # 계정을 지우면 발급 기록도 함께 지워진다 (ON DELETE CASCADE).
    db.execute("DELETE FROM users WHERE id = %s", (bob,))
    assert db.execute("SELECT count(*) AS n FROM mock_issuances WHERE user_id = %s", (bob,)).fetchone()["n"] == 0


def test_mock_issuance_reservation_holds_under_concurrency(db):
    from app.services.database import db_service

    user_id = _new_user(db)
    # 같은 사용자의 동시 요청(여러 태스크)도 advisory lock으로 줄을 서서 한도(3)를 넘지 못한다.
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(lambda _: db_service.reserve_mock_issuance(user_id, "tpl-default", 3)[0], range(12)))
    assert sum(i is not None for i in ids) == 3
    assert db.execute("SELECT count(*) AS n FROM mock_issuances WHERE user_id = %s",
                      (user_id,)).fetchone()["n"] == 3


def test_checksum_ignores_comments_blank_lines_and_crlf_but_not_sql(tmp_path):
    from app.migrate import checksum

    applied = BE_DIR / "DB" / "migrations" / "08_career_curriculum.sql"
    text = applied.read_text(encoding="utf-8")
    edited = tmp_path / applied.name
    # 설명 주석·빈 줄·줄 끝 공백·CRLF(Windows 체크아웃)·BOM 차이는 같은 파일로 본다.
    edited.write_bytes(b"\xef\xbb\xbf" + ("-- 설명만 고침\n\n" + text + "\n  \n").replace("\n", "\r\n").encode("utf-8"))
    assert checksum(edited) == checksum(applied)
    edited.write_text(text + "\nCREATE INDEX IF NOT EXISTS idx_extra ON curricula(created_at);\n", encoding="utf-8")
    assert checksum(edited) != checksum(applied)


def test_failed_migration_rolls_back_with_its_ledger_row(scratch_db, tmp_path):
    db_dir = tmp_path / "DB"
    shutil.copytree(BE_DIR / "DB", db_dir)
    (db_dir / "migrations" / "99_broken.sql").write_text(
        "CREATE TABLE migrate_probe (id INT);\nSELECT 1/0;\n", encoding="utf-8")
    db = scratch_db()

    with pytest.raises(psycopg.errors.DivisionByZero):
        _migrate(db, db_dir=db_dir)
    assert not _scalar(db, "SELECT to_regclass('public.migrate_probe') IS NOT NULL")  # 같은 파일의 변경은 롤백
    assert _ledger(db) == set(_names())  # 앞선 파일은 각자 커밋되고, 실패한 파일은 기록되지 않는다


def test_migration_files_are_ordered_by_number_and_validated(tmp_path):
    from app.migrate import MigrationError, migration_files

    folder = tmp_path / "migrations"
    folder.mkdir()
    for name in ("100_c.sql", "09_a.sql", "10_b.sql"):
        (folder / name).write_text("SELECT 1;\n", encoding="utf-8")
    assert [path.name for path in migration_files(tmp_path)] == ["09_a.sql", "10_b.sql", "100_c.sql"]

    (folder / "10_dup.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="번호가 겹칩니다"):
        migration_files(tmp_path)
    (folder / "10_dup.sql").unlink()
    (folder / "11-bad.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="NN_name.sql"):
        migration_files(tmp_path)
    (folder / "11-bad.sql").unlink()
    # 한글 이름은 macOS(NFD)·Linux(NFC)에서 바이트가 달라 원장에 두 번 기록될 수 있으므로 받지 않는다.
    (folder / "11_설명.sql").write_text("SELECT 1;\n", encoding="utf-8")
    with pytest.raises(MigrationError, match="NN_name.sql"):
        migration_files(tmp_path)


# 예: "      - ./DB/migrations/07_shares.sql:/docker-entrypoint-initdb.d/07_shares.sql" (끝의 :ro 같은 옵션 허용)
_MOUNT = re.compile(
    r"""^\s*-\s*["']?\./(?P<src>[^:"'\s]+):/docker-entrypoint-initdb\.d/(?P<dst>[^:"'\s/]+)"""
    r"""(?::[a-z,]+)?["']?\s*(?:#.*)?$"""
)


def test_compose_initdb_mounts_follow_runner_order():
    """새 Compose 볼륨(initdb)과 app.migrate(빈 DB·테스트·RDS)가 같은 파일을 같은 순서로 적용해야 한다.

    새 마이그레이션은 DB/migrations에 다음 번호로 추가하고 docker-compose.yml에도 같은 이름으로 마운트한다.
    """
    from app.migrate import bootstrap_files, migration_files

    mounts = []
    for line in (BE_DIR / "docker-compose.yml").read_text(encoding="utf-8").splitlines():
        if "docker-entrypoint-initdb.d" not in line or line.lstrip().startswith("#"):
            continue
        match = _MOUNT.match(line)
        assert match, f"initdb 마운트를 해석하지 못함 (파일 단위 마운트만 지원): {line.strip()}"
        mounts.append((match["src"], match["dst"]))
    targets = [dst for _, dst in mounts]
    # 엔트리포인트는 컨테이너 쪽 이름의 정렬 순서로 실행한다. 로케일과 상관없도록 두 자리 번호 + 소문자 이름만 쓴다.
    assert all(re.fullmatch(r"\d{2}_[a-z0-9_]+\.sql", target) for target in targets), targets
    assert len({target[:2] for target in targets}) == len(targets), f"initdb 번호가 겹침: {targets}"

    runner = [path.relative_to(BE_DIR).as_posix() for path in bootstrap_files(seed=True) + migration_files()]
    assert [src for src, _ in sorted(mounts, key=lambda mount: mount[1])] == runner
    # DB/ 아래 SQL 파일은 빠짐없이 두 경로 모두에서 적용된다.
    assert {path.relative_to(BE_DIR).as_posix() for path in (BE_DIR / "DB").rglob("*.sql")} == set(runner)

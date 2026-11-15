"""앱의 DB 접근 상한과 사용자별 생성 상한 (실제 PostgreSQL).

- app_db_connection: 프로세스가 동시에 여는 연결 수(DB_MAX_CONNECTIONS), 자리 대기 시간, 세션 시간 제한(문장·잠금·유휴 트랜잭션),
  같은 스레드의 중첩 사용, 503 + Retry-After 응답. get_db_connection(마이그레이션 실행기)은 상한·시간 제한을 받지 않는다.
- 공유 링크·포트폴리오의 사용자별 상한(409)과 목록 조회의 행 수 상한.
"""
import ast
import asyncio
import logging
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import anyio.to_thread
import httpx
import psycopg
import pytest

from tests.conftest import BE_DIR
from tests.test_features import create_portfolio, create_share, store_badge

BUSY = {"detail": "Service is busy. Try again later."}
SESSION_SETTINGS = ("statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout")


@pytest.fixture
def db_slots(monkeypatch):
    """DB_MAX_CONNECTIONS를 바꿔 끼우는 함수. 테스트가 끝나면 원래 값으로 자리를 다시 만든다."""
    from app.services import database

    def use(limit: int, wait_seconds: float | None = None) -> None:
        monkeypatch.setenv("DB_MAX_CONNECTIONS", str(limit))
        if wait_seconds is not None:
            monkeypatch.setattr(database, "DB_SLOT_WAIT_SECONDS", wait_seconds)
        database.app_db_slots.reset()

    yield use
    monkeypatch.undo()
    database.app_db_slots.reset()


@pytest.fixture
def connections(monkeypatch):
    """앱이 여는 DB 연결을 센다: total(연 횟수)·open(지금 열린 수)·peak(동시에 열려 있던 최대 수)."""
    from app.services import database

    state = Counter()
    lock = threading.Lock()
    real = psycopg.connect

    def counting(*args, **kwargs):
        conn = real(*args, **kwargs)
        with lock:
            state["total"] += 1
            state["open"] += 1
            state["peak"] = max(state["peak"], state["open"])
        real_close = conn.close

        def close():
            if not conn.closed:
                with lock:
                    state["open"] -= 1
            real_close()

        conn.close = close
        return conn

    monkeypatch.setattr(database.psycopg, "connect", counting)
    return state


def _settings_of(conn) -> list[str]:
    return [conn.execute(f"SHOW {name}").fetchone()[name] for name in SESSION_SETTINGS]


class Holders:
    """다른 스레드에서 앱 연결을 n개 쥐고 있다가 release()에서 놓는다."""

    def __init__(self, count: int):
        from app.services.database import app_db_connection

        self._release = threading.Event()
        ready = [threading.Event() for _ in range(count)]

        def hold(event: threading.Event) -> None:
            with app_db_connection() as conn:
                conn.execute("SELECT 1")
                event.set()
                self._release.wait(30)

        self._threads = [threading.Thread(target=hold, args=(event,)) for event in ready]
        for thread in self._threads:
            thread.start()
        assert all(event.wait(10) for event in ready)

    def release(self) -> None:
        self._release.set()
        for thread in self._threads:
            thread.join(10)


# ── 연결 접근자 ──────────────────────────────────────────────────────

def test_app_sessions_have_timeouts_and_direct_connections_do_not(monkeypatch):
    import app.migrate as runner
    from app.services.database import app_db_connection, get_db_connection

    with app_db_connection() as conn:
        assert _settings_of(conn) == ["15s", "5s", "30s"]
        assert conn.autocommit is True and conn.execute("SELECT 1 AS one").fetchone() == {"one": 1}
    assert conn.closed  # 블록이 끝나면 닫힌다 (연결 풀이 아니다)
    # 마이그레이션 실행기·테스트가 쓰는 직접 연결은 지금처럼 시간 제한이 없다 (인덱스 생성 등은 오래 걸릴 수 있다).
    with get_db_connection() as conn:
        assert _settings_of(conn) == ["0", "0", "0"]
    with runner._connect({}) as conn:
        assert [conn.execute(f"SHOW {name}").fetchone()[0] for name in SESSION_SETTINGS] == ["0", "0", "0"]

    # libpq의 PGOPTIONS는 지금처럼 앱 연결에도 적용된다. 같은 설정이 겹치면 앱의 시간 제한이 이긴다.
    monkeypatch.setenv("PGOPTIONS", "-c default_transaction_isolation=serializable -c statement_timeout=1min")
    with app_db_connection() as conn:
        assert _settings_of(conn) == ["15s", "5s", "30s"]
        assert conn.execute("SHOW default_transaction_isolation").fetchone() == {
            "default_transaction_isolation": "serializable"}


def test_at_most_db_max_connections_are_open_and_the_rest_wait(db_slots, connections):
    from app.services.database import app_db_connection

    db_slots(3)

    def work(_) -> int:
        with app_db_connection() as conn:
            return conn.execute("SELECT 1 AS one, pg_sleep(0.05)").fetchone()["one"]

    with ThreadPoolExecutor(12) as pool:
        assert list(pool.map(work, range(36))) == [1] * 36  # 기다린 호출도 자리가 나면 그대로 처리된다
    assert connections["total"] == 36 and connections["peak"] == 3 and connections["open"] == 0


def test_no_free_slot_raises_database_busy_without_connecting(db_slots, connections):
    from app.services.database import DatabaseBusy, app_db_connection

    db_slots(2, wait_seconds=0.2)
    holders = Holders(2)
    try:
        started = time.monotonic()
        with pytest.raises(DatabaseBusy):
            with app_db_connection():
                pytest.fail("must not get a connection")
        assert 0.15 <= time.monotonic() - started < 2
        assert connections["total"] == 2  # 자리를 얻지 못한 호출은 연결을 열지 않는다
    finally:
        holders.release()
    with app_db_connection() as conn:  # 자리가 돌아오면 다시 쓴다
        assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1


def test_nested_use_in_one_thread_shares_the_connection_and_the_slot(db, db_slots, connections):
    from app.services.database import app_db_connection

    db_slots(2, wait_seconds=0.2)
    holders = Holders(1)  # 남은 자리는 하나뿐이다
    try:
        marker = f"nested-{uuid.uuid4().hex}"
        with app_db_connection() as outer:
            with app_db_connection() as inner, app_db_connection() as third:  # 자기 자신을 기다리다 멈추지 않는다
                assert inner is outer and third is outer
                with pytest.raises(ZeroDivisionError):
                    with outer.transaction():
                        outer.execute("INSERT INTO users (email, name, subject_did) VALUES (%s, 'n', %s)",
                                      (f"{marker}@test.com", f"did:example:{marker}"))
                        with inner.transaction():  # 안쪽 트랜잭션은 바깥 트랜잭션의 세이브포인트다
                            1 / 0
            assert not outer.closed  # 안쪽 블록이 끝나도 바깥 연결은 열려 있다
            assert outer.execute("SELECT 1 AS one").fetchone()["one"] == 1
        assert outer.closed and connections["total"] == 2  # 쥐고 있는 스레드의 연결 + 이 스레드의 연결 하나
        assert db.execute("SELECT count(*) AS n FROM users WHERE email = %s", (f"{marker}@test.com",)).fetchone()["n"] == 0
        with app_db_connection() as again:  # 블록이 끝나면 이 스레드의 다음 호출은 새 연결을 받는다
            assert again is not outer
    finally:
        holders.release()


def test_block_left_on_another_thread_does_not_leave_a_dead_connection_behind(db_slots, connections):
    """회귀: 블록에 들어온 스레드와 나간 스레드가 다르면(FastAPI가 동기 yield 의존성을 그렇게 돌린다) 들어온 스레드에 닫힌
    연결이 남아, 그 스레드의 다음 호출부터 프로세스가 끝날 때까지 'the connection is closed' 오류가 났다."""
    from app.services.database import app_db_connection

    db_slots(2, wait_seconds=0.2)

    def use() -> tuple[int, int]:
        with app_db_connection() as conn:
            return id(conn), conn.execute("SELECT 1 AS one").fetchone()["one"]

    with ThreadPoolExecutor(1) as worker:  # 스레드 하나만 쓰는 풀: 들어가는 쪽은 늘 같은 스레드다
        for _ in range(3):
            manager = app_db_connection()
            conn = worker.submit(manager.__enter__).result(10)
            assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
            assert manager.__exit__(None, None, None) is False and conn.closed  # 이 스레드에서 나간다
            # 들어갔던 스레드의 다음 호출: 닫힌 연결이 아니라 새 연결을 받고, 블록이 끝나면 닫는다
            first, second = worker.submit(use).result(10), worker.submit(use).result(10)
            assert first[1] == second[1] == 1 and id(conn) not in (first[0], second[0])
    assert connections["total"] == 9 and connections["open"] == 0
    holders = Holders(2)  # 자리 두 개가 모두 돌아와 있다
    holders.release()


def test_slot_is_returned_when_the_block_or_the_connect_fails(db_slots, monkeypatch):
    from app.services import database

    db_slots(2, wait_seconds=0.2)
    for _ in range(5):  # 블록 안의 오류
        with pytest.raises(psycopg.errors.UndefinedTable):
            with database.app_db_connection() as conn:
                conn.execute("SELECT * FROM no_such_table")
    real = psycopg.connect

    def refuse(*args, **kwargs):
        raise psycopg.OperationalError("connection refused (test)")

    monkeypatch.setattr(database.psycopg, "connect", refuse)
    for _ in range(5):  # 접속 실패 (DB 장애): 지금처럼 그 오류가 그대로 나가고 자리는 돌아온다
        with pytest.raises(psycopg.OperationalError, match="connection refused"):
            with database.app_db_connection():
                pytest.fail("must not get a connection")
    monkeypatch.setattr(database.psycopg, "connect", real)
    holders = Holders(2)  # 자리 두 개가 모두 남아 있다
    holders.release()


def _direct_connections(source: str) -> list[str]:
    """소스에서 DB 연결을 직접 여는 곳: get_db_connection(...), 어떤 객체든 .connect(...) 호출(psycopg.connect,
    psycopg.Connection.connect, psycopg.AsyncConnection.connect 등), 이름만으로 부르는 connect(...), psycopg에서 connect를
    들여오는 import (다른 이름으로 들여와 부르는 것까지 잡는다). 상한 없는 접근자 get_db_connection은 모듈을 거쳐 부르는 것
    (database.get_db_connection(...))과 들여오는 import(다른 이름으로 들여와 부르는 것까지)도 잡는다."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "psycopg":
            found += [f"from {node.module} import {alias.name}" for alias in node.names if alias.name == "connect"]
        if isinstance(node, ast.ImportFrom):
            found += [f"from {node.module} import {alias.name}" for alias in node.names
                      if alias.name == "get_db_connection"]
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name) and target.id in ("get_db_connection", "connect"):
            found.append(target.id)
        elif isinstance(target, ast.Attribute) and target.attr in ("connect", "get_db_connection"):
            found.append(ast.unparse(target))
    return sorted(found)


def test_app_code_reaches_the_database_only_through_the_bounded_accessor():
    """새 DB 호출을 상한·시간 제한 없이 더하지 못하게 한다: 직접 연결은 database.py(정의)와 마이그레이션 실행기만 쓴다."""
    # 검사 자체: 상한을 비켜 가는 여러 표기를 모두 알아본다 (예전에는 psycopg.connect(와 get_db_connection(만 알아봤다).
    assert _direct_connections("with app_db_connection() as conn:\n    conn.execute('SELECT 1')\n") == []
    for source, found in {
        "conn = psycopg.connect(host=h)": ["psycopg.connect"],
        "conn = psycopg.Connection.connect(dsn)": ["psycopg.Connection.connect"],
        "conn = await psycopg.AsyncConnection.connect(dsn)": ["psycopg.AsyncConnection.connect"],
        "from psycopg import Connection\nconn = Connection.connect(dsn)": ["Connection.connect"],
        "from psycopg import connect\nconn = connect(dsn)": ["connect", "from psycopg import connect"],
        "from psycopg import connect as open_db\nconn = open_db(dsn)": ["from psycopg import connect"],
        "import psycopg as pg\nconn = pg.connect(dsn)": ["pg.connect"],
        "with get_db_connection() as conn:\n    pass": ["get_db_connection"],
        # 상한 없는 접근자를 모듈을 거쳐 부르거나 다른 이름으로 들여와 부르는 표기 (예전에는 이름 그대로 부를 때만 알아봤다)
        "from app.services import database\nconn = database.get_db_connection()": ["database.get_db_connection"],
        "import app.services.database as db\nconn = db.get_db_connection(options=o)": ["db.get_db_connection"],
        "from app.services.database import get_db_connection as _raw\nconn = _raw()": [
            "from app.services.database import get_db_connection"],
        "from .database import get_db_connection": ["from database import get_db_connection"],
    }.items():
        assert _direct_connections(source) == found, source

    calls = {path.relative_to(BE_DIR).as_posix(): _direct_connections(path.read_text(encoding="utf-8"))
             for path in [BE_DIR / "main.py", *sorted((BE_DIR / "app").rglob("*.py"))]}
    assert {name: found for name, found in calls.items() if found} == {
        # 정의(psycopg.connect)와 app_db_connection 안의 한 번
        "app/services/database.py": ["get_db_connection", "psycopg.connect"],
        # 마이그레이션 실행기만 상한 없는 접근자를 들여와 쓴다 (다른 파일이 들여오면 여기서 걸린다)
        "app/migrate.py": ["from app.services.database import get_db_connection", "get_db_connection",
                           "psycopg.connect"],
    }


# ── 503 응답 ─────────────────────────────────────────────────────────

def _busy_logs(caplog) -> list[str]:
    return [record.getMessage() for record in caplog.records
            if record.name == "vc-be" and record.getMessage().startswith("Database busy:")]


def _assert_busy(res) -> None:
    assert res.status_code == 503 and res.json() == BUSY and res.headers["retry-after"] == "5", res.text


def test_no_free_slot_is_answered_503_with_retry_after(client, register, db_slots, caplog):
    user = register()
    db_slots(2, wait_seconds=0.2)
    holders = Holders(2)
    try:
        with caplog.at_level(logging.WARNING):
            res = client.get("/api/v1/users/me", headers={**user["headers"], "Origin": "http://localhost:5173"})
        _assert_busy(res)
        assert res.headers["access-control-allow-origin"] == "http://localhost:5173"  # FE가 응답을 읽을 수 있다
        # 요청마다 경고 한 줄만 남긴다 (추적(traceback)·'Unhandled error' 없음).
        assert _busy_logs(caplog) == ["Database busy: method=GET path=/api/v1/users/me reason=DatabaseBusy"]
        assert not [r for r in caplog.records if r.exc_info or "Unhandled error" in r.getMessage()]
        assert client.get("/health").status_code == 200  # DB를 쓰지 않는 생존 확인은 그대로다
    finally:
        holders.release()
    assert client.get("/api/v1/users/me", headers=user["headers"]).status_code == 200


async def test_health_answers_while_every_db_slot_and_worker_thread_is_taken(register, db_slots):
    """회귀: /health가 동기 함수라 워커 스레드를 하나 써야 했다. DB 자리를 기다리는 요청이 워커 스레드(40개)를 모두 잡고
    있으면(각 최대 5초) 상태 확인도 그 뒤에 줄을 서서 ALB·ECS 상태 확인 제한(4~5초)을 넘겼다. 이제 이벤트 루프에서 바로 답한다."""
    from main import app

    user = register()
    db_slots(2, wait_seconds=5.0)
    holders = Holders(2)  # DB 자리를 모두 쥔다: 이제 DB를 쓰는 요청은 워커 스레드에서 자리를 기다린다 (최대 5초)
    limiter = anyio.to_thread.current_default_thread_limiter()
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as c:
            waiting = [asyncio.create_task(c.get("/api/v1/users/me", headers=user["headers"]))
                       for _ in range(int(limiter.total_tokens) + 5)]  # 워커 스레드 수보다 많이
            for _ in range(400):
                if limiter.borrowed_tokens >= limiter.total_tokens:
                    break
                await asyncio.sleep(0.01)
            assert limiter.borrowed_tokens == limiter.total_tokens  # 워커 스레드가 모두 DB 자리를 기다리고 있다
            started = time.monotonic()
            # 고치기 전에는 여기서 워커 스레드가 날 때까지(자리 대기 5초) 기다렸다. 그때는 1초 제한에 걸려 실패한다.
            health = await asyncio.wait_for(c.get("/health"), timeout=1.0)
            waited = time.monotonic() - started
            holders.release()
            answers = await asyncio.wait_for(asyncio.gather(*waiting), timeout=30.0)
    finally:
        holders.release()
    assert health.status_code == 200 and health.json()["status"] == "ok" and waited < 0.5
    # 자리가 나자 기다리던 요청도 모두 끝났다 (느린 환경에서 자리 대기 5초를 넘긴 요청은 503일 수 있다)
    assert {res.status_code for res in answers} <= {200, 503} and 200 in {res.status_code for res in answers}


def test_lock_wait_and_statement_timeouts_are_answered_503(client, register, db, monkeypatch, caplog):
    from app.services import database

    user = register()
    monkeypatch.setattr(database, "APP_SESSION_OPTIONS",
                        "-c statement_timeout=300 -c lock_timeout=150 -c idle_in_transaction_session_timeout=30000")
    # 잠금 대기: 다른 세션이 portfolios를 잠그고 있으면(마이그레이션 등) 끝없이 기다리지 않는다.
    with caplog.at_level(logging.WARNING), db.transaction():
        # 잠근 세션은 아래 요청을 기다리는 동안 트랜잭션을 연 채 쉰다. 10초를 넘기면 PostgreSQL이 이 세션을 끊어 잠금이
        # 풀리게 한다: 앱 세션의 시간 제한이 빠지는 회귀가 생겨도 요청이 잠금을 끝없이 기다려 테스트가 멈추는 대신
        # (그 요청이 200으로 끝나) 여기서 실패한다.
        db.execute("SET LOCAL idle_in_transaction_session_timeout = '10s'")
        db.execute("LOCK TABLE portfolios IN ACCESS EXCLUSIVE MODE")
        started = time.monotonic()
        _assert_busy(client.get("/api/v1/portfolios", headers=user["headers"]))
        assert time.monotonic() - started < 3
        # 그 표를 쓰지 않는 요청은 그대로 처리된다.
        assert client.get("/api/v1/users/me", headers=user["headers"]).status_code == 200
    assert _busy_logs(caplog) == ["Database busy: method=GET path=/api/v1/portfolios reason=LockNotAvailable"]
    assert client.get("/api/v1/portfolios", headers=user["headers"]).status_code == 200

    # 느린 문장: 문장 시간 제한에 걸리면 서버가 취소한다.
    def slow_list(user_id):
        with database.app_db_connection() as conn:
            return conn.execute("SELECT pg_sleep(5)").fetchall()

    monkeypatch.setattr(database.db_service, "list_shares", slow_list)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        started = time.monotonic()
        _assert_busy(client.get("/api/v1/shares", headers=user["headers"]))
        assert time.monotonic() - started < 3
    assert _busy_logs(caplog) == ["Database busy: method=GET path=/api/v1/shares reason=QueryCanceled"]
    assert not [r for r in caplog.records if r.exc_info or "Unhandled error" in r.getMessage()]


def test_burst_of_parallel_requests_stays_within_db_max_connections(client, register, db_slots, connections):
    user = register()
    db_slots(4)
    connections.clear()
    paths = ["/api/v1/users/me", "/api/v1/shares", "/api/v1/portfolios", f"/api/v1/subjects/{user['did']}/careers"]

    def get(index: int) -> int:
        return client.get(paths[index % len(paths)], headers=user["headers"]).status_code

    with ThreadPoolExecutor(32) as pool:
        statuses = Counter(pool.map(get, range(160)))
    assert statuses == {200: 160}
    assert connections["total"] >= 160 * 2  # 요청마다 토큰 확인 + 조회로 연결을 여러 번 연다
    assert 1 < connections["peak"] <= 4 and connections["open"] == 0


# ── 사용자별 생성 상한 ────────────────────────────────────────────────

def test_default_limits():
    from app.integrations import ai_service
    from app.services import database

    assert (database.MAX_ACTIVE_SHARES_PER_USER, database.MAX_PORTFOLIOS_PER_USER, database.LIST_LIMIT) == (100, 100, 200)
    # 자리를 기다리는 시간(초)의 기본값: DB 연결 자리 5초, AI 묶음 요청 자리 10초. 다른 테스트는 이 값을 바꿔 끼우기만 하므로
    # 여기서 고정한다 (늘리면 그만큼 워커 스레드·요청이 오래 붙잡힌다. 503의 Retry-After는 둘 다 5초다).
    assert (database.DB_SLOT_WAIT_SECONDS, ai_service.AI_SLOT_WAIT_SECONDS) == (5.0, 10.0)
    assert (database.DB_BUSY_RETRY_AFTER_SECONDS, ai_service.AI_BUSY_RETRY_AFTER_SECONDS) == (5, 5)


def test_share_link_cap_counts_only_active_links(client, register, db, monkeypatch):
    from app.services import database

    monkeypatch.setattr(database, "MAX_ACTIVE_SHARES_PER_USER", 3)
    alice, bob = register(), register()
    record_id, _ = store_badge(client, alice)
    bob_record, _ = store_badge(client, bob)
    created = [create_share(client, alice, [record_id]) for _ in range(3)]
    assert [res.status_code for res in created] == [201, 201, 201]

    refused = create_share(client, alice, [record_id])
    assert refused.status_code == 409
    assert refused.json() == {"detail": "Share link limit reached. Revoke unused links first."}
    assert db.execute("SELECT count(*) AS n FROM shares WHERE user_id = %s", (alice["id"],)).fetchone()["n"] == 3
    assert create_share(client, bob, [bob_record]).status_code == 201  # 사용자별로 센다

    # 철회하거나 만료된 링크는 세지 않는다: 쓰지 않는 링크를 철회하면 다시 만들 수 있다.
    first, second = (res.json()["data"]["id"] for res in created[:2])
    assert client.delete(f"/api/v1/shares/{first}", headers=alice["headers"]).status_code == 200
    assert create_share(client, alice, [record_id]).status_code == 201
    assert create_share(client, alice, [record_id]).status_code == 409
    db.execute("UPDATE shares SET expires_at = NOW() - interval '1 minute' WHERE id = %s", (second,))
    assert create_share(client, alice, [record_id]).status_code == 201
    assert create_share(client, alice, [record_id]).status_code == 409


ISOLATION_OPTIONS = {"read committed": "", "repeatable read": r"-c default_transaction_isolation=repeatable\ read",
                     "serializable": "-c default_transaction_isolation=serializable"}


@pytest.mark.parametrize("level", list(ISOLATION_OPTIONS))
def test_share_link_cap_holds_for_concurrent_requests(client, register, db, monkeypatch, level):
    # 같은 사용자의 동시 요청 16건, 상한 3: DB 기본 격리 수준과 관계없이 정확히 3건만 만들어진다 (500 없음).
    from app.services import database

    monkeypatch.setattr(database, "MAX_ACTIVE_SHARES_PER_USER", 3)
    user = register()
    record_id, _ = store_badge(client, user)
    monkeypatch.setenv("PGOPTIONS", ISOLATION_OPTIONS[level])
    with ThreadPoolExecutor(8) as pool:
        statuses = Counter(pool.map(lambda _: create_share(client, user, [record_id]).status_code, range(16)))
    assert statuses == {201: 3, 409: 13}
    assert db.execute("SELECT count(*) AS n FROM shares WHERE user_id = %s", (user["id"],)).fetchone()["n"] == 3


def test_portfolio_cap_is_checked_before_the_verifier_and_at_insert(client, register, fake_dx, db, monkeypatch):
    from app.services import database

    monkeypatch.setattr(database, "MAX_PORTFOLIOS_PER_USER", 2)
    alice, bob = register(), register()
    record_id, token = store_badge(client, alice)
    bob_record, _ = store_badge(client, bob)
    created = [create_portfolio(client, alice, [record_id]) for _ in range(2)]
    assert [res.status_code for res in created] == [201, 201]

    calls = []
    original = fake_dx.verify_badge

    async def counting(vc_jwt):
        calls.append(vc_jwt)
        return await original(vc_jwt)

    monkeypatch.setattr(fake_dx, "verify_badge", counting)
    refused = create_portfolio(client, alice, [record_id])
    assert refused.status_code == 409 and refused.json() == {"detail": "Portfolio limit reached."}
    assert calls == []  # 이미 상한이면 검증기(DXWorks)를 부르지 않는다
    assert create_portfolio(client, bob, [bob_record]).status_code == 201  # 사용자별로 센다

    # 미리 보는 검사를 지나친 요청(동시 요청이 먼저 채운 경우)도 저장할 때 막힌다.
    monkeypatch.setattr(database.db_service, "portfolio_limit_reached", lambda user_id: False)
    late = create_portfolio(client, alice, [record_id])
    assert late.status_code == 409 and late.json() == {"detail": "Portfolio limit reached."}
    assert db.execute("SELECT count(*) AS n FROM portfolios WHERE user_id = %s", (alice["id"],)).fetchone()["n"] == 2
    monkeypatch.undo()

    # 지우면 다시 만들 수 있다.
    monkeypatch.setattr(database, "MAX_PORTFOLIOS_PER_USER", 2)
    portfolio_id = created[0].json()["data"]["id"]
    assert client.delete(f"/api/v1/portfolios/{portfolio_id}", headers=alice["headers"]).status_code == 200
    assert create_portfolio(client, alice, [record_id]).status_code == 201


@pytest.mark.parametrize("level", list(ISOLATION_OPTIONS))
def test_portfolio_cap_holds_for_concurrent_inserts(register, db, monkeypatch, level):
    # 같은 사용자의 동시 저장 16건, 상한 3: DB 기본 격리 수준과 관계없이 정확히 3건만 저장되고 나머지는 None(→ 409)이다.
    from app.services import database

    monkeypatch.setattr(database, "MAX_PORTFOLIOS_PER_USER", 3)
    user = register()
    monkeypatch.setenv("PGOPTIONS", ISOLATION_OPTIONS[level])

    def save(_):
        return database.db_service.create_portfolio(
            user_id=user["id"], title="t", target_role=None, bio_summary="", content_markdown="# t",
            sections={"source": "VC_ONLY"}, referenced_record_ids=[])

    with ThreadPoolExecutor(8) as pool:
        rows = list(pool.map(save, range(16)))
    assert sum(row is not None for row in rows) == 3 and sum(row is None for row in rows) == 13
    assert db.execute("SELECT count(*) AS n FROM portfolios WHERE user_id = %s", (user["id"],)).fetchone()["n"] == 3


# ── 목록 조회의 행 수 상한 ────────────────────────────────────────────

def test_share_list_returns_at_most_200_and_never_hides_active_links(client, register, db):
    user = register()
    record_id, _ = store_badge(client, user)
    # 오래된 유효한 링크 3개 + 그 뒤에 만들고 철회한 링크 205개 (철회·만료된 링크는 지워지지 않고 쌓인다)
    db.execute(
        """INSERT INTO shares (user_id, token_hash, record_ids, label, expires_at, revoked_at, created_at)
           SELECT %s, md5(g::text) || md5(%s || g::text), ARRAY[%s]::uuid[], 'link-' || g,
                  NOW() + interval '7 days', CASE WHEN g > 3 THEN NOW() END, NOW() - (300 - g) * interval '1 minute'
           FROM generate_series(1, 208) AS g""", (user["id"], user["id"], record_id))

    listed = client.get("/api/v1/shares", headers=user["headers"]).json()["data"]
    assert len(listed) == 200
    assert [row["created_at"] for row in listed] == sorted((row["created_at"] for row in listed), reverse=True)
    labels = [row["label"] for row in listed]
    # 가장 최근 것부터 싣되, 아직 열리는 링크는 오래됐어도 빠지지 않는다 (목록에 없으면 철회할 수 없다).
    assert labels[:3] == ["link-208", "link-207", "link-206"] and labels[-3:] == ["link-3", "link-2", "link-1"]
    assert [row["label"] for row in listed if not row["revoked"]] == ["link-3", "link-2", "link-1"]
    assert "link-4" not in labels  # 밀려난 것은 가장 오래된 철회 링크들이다


def test_portfolio_list_returns_the_newest_200_without_loading_bodies(client, register, db):
    from app.services.database import db_service

    user = register()
    db.execute(
        """INSERT INTO portfolios (user_id, title, content_markdown, sections, referenced_record_ids, updated_at)
           SELECT %s, 'p-' || g, repeat('x', 20000), '{"evidence": []}'::jsonb, ARRAY[gen_random_uuid()],
                  NOW() - (300 - g) * interval '1 minute'
           FROM generate_series(1, 205) AS g""", (user["id"],))

    res = client.get("/api/v1/portfolios", headers=user["headers"])
    listed = res.json()["data"]
    assert res.status_code == 200 and len(listed) == 200 and len(res.content) < 60_000
    assert [row["title"] for row in listed[:2]] == ["p-205", "p-204"] and listed[-1]["title"] == "p-6"
    assert set(listed[0]) == {"id", "title", "target_role", "status", "credential_count", "created_at", "updated_at"}
    assert listed[0]["credential_count"] == 1 and listed[0]["status"] == "draft"
    # 목록은 요약에 쓰는 열만 읽는다 (본문·근거는 상세 조회에서만 읽는다).
    rows = db_service.list_portfolios(user["id"])
    assert set(rows[0]) == {"id", "title", "target_role", "status", "referenced_record_ids", "created_at", "updated_at"}
    detail = client.get(f"/api/v1/portfolios/{listed[0]['id']}", headers=user["headers"]).json()["data"]
    assert len(detail["content_markdown"]) == 20000

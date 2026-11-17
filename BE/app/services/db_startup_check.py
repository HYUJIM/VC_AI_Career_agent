"""운영(APP_ENV=production) 서버 시작 때 DB 자체 점검.

/health는 DB를 보지 않는다 (main.py). 그래서 DB 주소·계정·비밀번호·TLS(PGSSLMODE·PGSSLROOTCERT)·마이그레이션·권한이 틀린
BE도 상태 확인(Docker HEALTHCHECK 등)을 통과하고 DB를 쓰는 요청만 모두 500이 된다 (배포가 실패로 드러나지 않는다).
그래서 production에서는 요청을 받기 전(ASGI lifespan startup) 한 번, 요청과 같은 접근자(app_db_connection: 같은 계정·TLS·
세션 시간 제한)로 CHECK_STATEMENTS(`SELECT 1`, 그리고 마이그레이션이 안 됐거나 권한이 없으면 실패하는 핵심 표 읽기)를 한다.
- 실패하면 RETRY_INTERVAL_SECONDS 간격으로 다시 시도한다 (DB가 막 뜨는 중이거나 마이그레이션이 막 끝난 경우).
  RETRY_SECONDS(약 30초) 안에 통과하지 못하면 한 줄(`DB startup check failed: <오류 종류>: <안전한 요약>`)을 남기고 시작을
  거부한다: uvicorn은 그 줄과 "Application startup failed. Exiting."을 남기고 종료 코드 3으로 끝난다 (운영 compose는
  restart: unless-stopped로 다시 시작하고 docker compose ps·logs에 드러난다). 다시 시도할 때마다 같은 요약으로 경고 한 줄을 남긴다.
- 요약은 아래 정해 둔 문구만 쓴다. libpq 오류 원문에는 DB 호스트·사용자 이름·DB 이름·인증서 경로가 들어 있으므로 싣지
  않는다 (비밀번호는 원문에도 없다). 설정 오류(RuntimeError)는 변수 이름만 담고 있어 그대로 싣는다.
- 새 경로(/ready 등)는 만들지 않는다: 요청마다 DB 연결을 여는 인증 없는 경로는 연결 고갈 위험이 있다. /health는 그대로다.
- development·테스트에서는 하지 않는다. ENABLED는 production 앱을 띄우는 테스트가 끄는 내부 스위치다 (tests/conftest.py의
  production_env). 환경변수로는 끄지 않는다.
"""
import asyncio
import logging
import time
from typing import Optional

import psycopg
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings
from app.services.database import DatabaseBusy, app_db_connection

logger = logging.getLogger("vc-be")

ENABLED = True
RETRY_SECONDS = 30.0
RETRY_INTERVAL_SECONDS = 5.0
FAILED_PREFIX = "DB startup check failed: "
# 앱이 가장 먼저 쓰는 표(가입·로그인). 없으면 마이그레이션 전이고, 읽지 못하면 서비스 계정(DB_APP_ROLE) 권한이 없다.
CHECK_STATEMENTS = ("SELECT 1", "SELECT 1 FROM users LIMIT 1")

# libpq 접속 오류 원문(소문자로 바꿔 비교)에 모두 들어 있는 조각 → 안전한 요약. 위에서부터 처음 맞는 것을 쓴다.
_CONNECT_ERRORS = (
    (("root certificate file",), "CA bundle file is missing or unreadable (check PGSSLROOTCERT)"),
    (("does not match host name",),
     "server certificate does not match the host name (set DB_HOST to the RDS endpoint name, not an IP or CNAME)"),
    (("certificate verify failed",), "server certificate verification failed (check PGSSLROOTCERT: RDS CA bundle)"),
    (("server does not support ssl",), "server does not support SSL (check DB_HOST and PGSSLMODE)"),
    (("pg_hba.conf",), "connection rejected by pg_hba.conf (check PGSSLMODE: RDS requires SSL)"),
    (("password authentication failed",), "password authentication failed (check DB_USER and DB_PASSWORD)"),
    (('role "', "does not exist"), "role does not exist (check DB_USER)"),
    (('database "', "does not exist"), "database does not exist (check DB_NAME)"),
    (("too many",), "too many connections (check RDS max_connections and DB_MAX_CONNECTIONS)"),
    (("remaining connection slots",), "too many connections (check RDS max_connections and DB_MAX_CONNECTIONS)"),
    (("the database system is",), "database server is starting up, shutting down or in recovery"),
    (("timeout expired",), "connection timed out (check DB_HOST, DB_PORT and security groups)"),
    (("connection refused",), "connection refused (check DB_HOST and DB_PORT)"),
    (("could not translate host name",), "host name not resolved (check DB_HOST)"),
    (("ssl",), "SSL connection failed (check PGSSLMODE and PGSSLROOTCERT)"),
)


def describe_error(exc: BaseException) -> str:
    """로그에 남길 `<오류 종류>: <안전한 요약>`. 오류 원문(호스트·사용자 이름·경로가 들어 있다)은 넣지 않는다."""
    kind = type(exc).__name__
    if isinstance(exc, psycopg.errors.UndefinedTable):
        return f"{kind}: table users not found (run the migration task first)"
    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return f"{kind}: permission denied on table users (run the migration task with DB_APP_ROLE set to this DB_USER)"
    if isinstance(exc, psycopg.errors.QueryCanceled):
        return f"{kind}: query timed out"
    if isinstance(exc, psycopg.OperationalError):
        message = str(exc).lower()
        for fragments, summary in _CONNECT_ERRORS:
            if all(fragment in message for fragment in fragments):
                return f"{kind}: {summary}"
    if isinstance(exc, psycopg.Error):
        sqlstate = getattr(exc, "sqlstate", None)
        return f"{kind}: SQLSTATE {sqlstate}" if sqlstate else f"{kind}: unclassified database error"
    if isinstance(exc, DatabaseBusy):
        return f"{kind}: no free DB connection slot"
    if isinstance(exc, RuntimeError):
        return f"{kind}: {exc}"  # app.core.config의 설정 오류 (변수 이름만 담는다)
    if isinstance(exc, ValueError):
        return f"{kind}: DB_PORT and DB_CONNECT_TIMEOUT must be integers"  # get_db_connection의 int()
    return kind


def check_database() -> None:
    """요청과 같은 접근자로 한 번 접속해 CHECK_STATEMENTS를 실행한다. 실패하면 그 예외를 그대로 올린다."""
    with app_db_connection() as conn:
        for statement in CHECK_STATEMENTS:
            conn.execute(statement).fetchall()


async def run_check() -> Optional[str]:
    """점검을 통과하면 None, RETRY_SECONDS 안에 통과하지 못하면 남길 한 줄을 돌려준다.

    DB 오류(psycopg.Error)·연결 자리 부족만 다시 시도한다. 설정 오류 등 다시 해도 같은 오류는 바로 실패한다.
    접속·쿼리는 워커 스레드에서 해서 기다리는 동안에도 이벤트 루프(종료 신호 처리)를 막지 않는다.
    """
    deadline = time.monotonic() + RETRY_SECONDS
    attempt = 0
    while True:
        attempt += 1
        try:
            await run_in_threadpool(check_database)
        except (psycopg.Error, DatabaseBusy) as exc:
            reason = describe_error(exc)
            if time.monotonic() + RETRY_INTERVAL_SECONDS >= deadline:
                return FAILED_PREFIX + reason
            logger.warning("DB startup check: attempt %d failed (%s); retrying in %gs",
                           attempt, reason, RETRY_INTERVAL_SECONDS)
            await asyncio.sleep(RETRY_INTERVAL_SECONDS)
        except Exception as exc:  # noqa: BLE001  (설정 오류 등: 메시지는 describe_error가 고른다)
            return FAILED_PREFIX + describe_error(exc)
        else:
            if attempt > 1:
                logger.info("DB startup check passed (attempt %d)", attempt)
            return None


class DBStartupCheckMiddleware:
    """lifespan startup 메시지를 앱에 넘기기 전에 run_check()를 한다 (APP_ENV=production이고 ENABLED일 때만).

    통과하면 받은 startup 메시지를 그대로 앱에 넘겨 지금처럼 시작한다. 실패하면 앱의 lifespan을 시작하지 않고
    lifespan.startup.failed를 그 한 줄과 함께 보낸다. 예외를 올리면 Starlette가 traceback 전체를 메시지로 보내므로(수십 줄)
    올리지 않는다. HTTP·WebSocket 요청은 그대로 넘긴다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan" or not (ENABLED and get_settings().app_env == "production"):
            await self.app(scope, receive, send)
            return
        message = await receive()
        if message["type"] == "lifespan.startup":
            failure = await run_check()
            if failure is not None:
                await send({"type": "lifespan.startup.failed", "message": failure})
                return
        pending = [message]

        async def replay() -> Message:
            return pending.pop() if pending else await receive()

        await self.app(scope, replay, send)

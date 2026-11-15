import json
import logging
import os
import re
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv()

import psycopg  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from starlette._utils import get_route_path  # noqa: E402
from starlette.datastructures import MutableHeaders  # noqa: E402
from starlette.middleware.body_limit import RequestBodyLimitMiddleware  # noqa: E402
from starlette.types import ASGIApp, Message, Receive, Scope, Send  # noqa: E402

from app.api import auth, career, market, mock_issuer, oauth, portfolios, reverify, shares, user, verify  # noqa: E402
from app.core.config import get_settings, log_level  # noqa: E402
from app.core.text import UNSTORABLE_TEXT_MESSAGE  # noqa: E402
from app.integrations.ai_service import AIServiceError  # noqa: E402
from app.integrations.ports import ProviderError  # noqa: E402
from app.services.database import DB_BUSY, DB_BUSY_RETRY_AFTER_SECONDS, DatabaseBusy  # noqa: E402
from app.services.verifier_budget import TOO_MANY_VERIFICATIONS, VerifierBudgetExceeded  # noqa: E402

LOG_LEVEL = log_level()  # 없거나 비우면 INFO
logging.basicConfig(level=LOG_LEVEL)
# httpx는 INFO로 요청마다 전체 URL(비공개 DXWorks 호스트 포함)을, httpcore는 DEBUG로 접속 호스트·응답 헤더를 남긴다.
# LOG_LEVEL=DEBUG일 때만 그대로 두고, 그 밖의 값(루트가 모든 수준을 남기는 NOTSET 포함)에서는 둘 다 WARNING으로 묶는다.
if LOG_LEVEL != "DEBUG":
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("vc-be")

# 요청 본문 상한(바이트). FastAPI는 인증 의존성보다 먼저 본문 전체를 읽고 JSON으로 파싱하므로, 상한이 없으면
# 토큰 없는 요청 하나가 본문 크기의 수십 배 메모리를 쓴다. 상한은 경로에 따라 네 가지다 (body_limit).
# 선언한 Content-Length가 상한을 넘으면 본문을 읽기 전에 413을 돌려주고, Content-Length 없이(chunked) 오는 본문은
# 상한까지 읽은 뒤 413을 돌려준다. 본문을 읽지 않는 라우트(로그아웃 등)는 Content-Length가 상한을 넘으면 그대로 실행되고
# 응답만 413으로 바뀌며, chunked 본문이면 본문을 읽지 않으므로 413 없이 그대로 처리된다.
# - 가장 큰 상한: 아래 LARGE_BODY_PATHS(연구자 공고·학습자료 반입)만 쓴다. 가장 큰 정상 요청(공고 반입: 원문 10만 자 +
#   인용 50개, 한글을 \u 이스케이프하면 약 1.76MiB)보다 크게 잡는다. 어느 경로도 이 값을 넘지 못한다.
#   이 세 API는 본문 형식 검사보다 먼저 연구자인지 확인한다 (app/api/career.py의 researcher 의존성): 연구자가 아닌 계정의
#   큰 본문은 형식 검사 없이 403으로 끝난다.
MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
LARGE_BODY_PATHS = frozenset({
    "/api/v1/research/job-postings/preview", "/api/v1/research/job-postings", "/api/v1/research/learning-resources",
})
# - 기본 상한: 아래 목록에 없는 모든 경로·메서드. 로그인한 요청도 본문을 끝까지 읽고 검사하므로 작게 둔다: 모르는 키를 금지한
#   모델(공유 링크·포트폴리오·직무 비교·커리큘럼)에 모르는 키 13만 개(1.8 MB)를 보내면 요청마다 이벤트 루프를 0.2초씩 잡고
#   프로세스 메모리가 1.2 GiB까지 늘었다. 이 경로들의 가장 큰 정상 본문은 커리큘럼 요청(대안 선택 200개, 약 35 KB)이다.
#   본문을 받는 라우트를 새로 만들면 가장 큰 정상 본문을 재서 이 값을 넘을 때만 목록에 넣는다 (tests/test_app_security.py의
#   본문 라우트 목록이 새 라우트를 알려 준다).
MAX_DEFAULT_BODY_BYTES = 64 * 1024
# - 배지·신원 검증(POST /api/v1/verify): 서명 원본이 MAX_TOKEN_CHARS(256 KiB)까지라 기본 상한에 그만큼을 더한다 (320 KiB).
MAX_VERIFY_BODY_BYTES = verify.MAX_TOKEN_CHARS + MAX_DEFAULT_BODY_BYTES
VERIFY_BODY_PATHS = frozenset({"/api/v1/verify"})
# - 로그인 없이 부르는 가입·로그인·Google 콜백(POST): 토큰이 없어도 본문을 끝까지 읽고 검사하므로 정상 본문(6.4 KB 이하:
#   이메일 255자, 비밀번호 72바이트, 인가 코드·state·redirect_uri 각 2,048자 이하)보다 넉넉한 16 KiB에서 끊는다.
MAX_PUBLIC_AUTH_BODY_BYTES = 16 * 1024
PUBLIC_AUTH_BODY_PATHS = frozenset({
    "/api/v1/users/login", "/api/v1/users/register", "/api/v1/users/oauth/google/callback",
})
# POST 경로 → 본문 상한. 여기에 없는 경로와 POST가 아닌 메서드는 기본 상한이다.
_POST_BODY_LIMITS = {
    **dict.fromkeys(PUBLIC_AUTH_BODY_PATHS, MAX_PUBLIC_AUTH_BODY_BYTES),
    **dict.fromkeys(VERIFY_BODY_PATHS, MAX_VERIFY_BODY_BYTES),
    **dict.fromkeys(LARGE_BODY_PATHS, MAX_REQUEST_BODY_BYTES),
}


def body_limit(method: str, path: str) -> int:
    """이 메서드·경로(라우터가 보는 경로: root_path를 뗀 값)의 요청 본문 상한(바이트)."""
    return _POST_BODY_LIMITS.get(path, MAX_DEFAULT_BODY_BYTES) if method == "POST" else MAX_DEFAULT_BODY_BYTES


# 요청 검증 실패(422) 응답에 싣는 오류 수와 loc 항목(필드·키 이름) 길이의 상한
MAX_VALIDATION_ERRORS = 20
MAX_VALIDATION_LOC_CHARS = 64

# 접근 로그(uvicorn.access → stdout → CloudWatch)에 남기지 않는 경로 조각.
# 공유 링크 토큰은 가진 사람 누구나 링크를 여는 비밀이라(DB에도 해시만 둔다) 그 뒤 경로를 모두 가리고, DID는 그 조각만 가린다.
# Google 콜백의 쿼리(인가 코드·state·code_verifier)는 통째로 가린다: BE의 GET 콜백(개발용)뿐 아니라 BE로 잘못 들어온
# FE 콜백 주소(/auth/google/callback?code=…)도 같은 모양이다.
_ACCESS_LOG_REDACTIONS = (
    (re.compile(r"(/public/+shares/)[^?\s]+", re.IGNORECASE), r"\1[redacted]"),
    (re.compile(r"(/subjects/+)[^/?\s]+", re.IGNORECASE), r"\1[did]"),
    (re.compile(r"(/google/+callback/*\?)\S*", re.IGNORECASE), r"\1[redacted]"),
)


def redact_path(path: str) -> str:
    """로그에 남길 경로에서 공유 링크 토큰은 [redacted], DID는 [did]로 바꾼다.

    쿼리 문자열은 그대로 두되, Google 콜백(…/google/callback?…)의 쿼리만 [redacted]로 바꾼다.
    """
    for pattern, replacement in _ACCESS_LOG_REDACTIONS:
        path = pattern.sub(replacement, path)
    return path


class AccessLogRedactionFilter(logging.Filter):
    """uvicorn 접근 로그 줄의 경로에서 공유 링크 토큰과 DID, Google 콜백의 쿼리를 가린다.

    uvicorn은 앱을 import하기 전에 로깅을 설정하고(dictConfig는 로거의 필터를 지우지 않음), --reload·워커 프로세스도
    같은 순서라서 import 시점에 붙인 필터가 그대로 유지된다. 인자 순서가 바뀌어도 가리도록 모든 문자열 인자에 적용한다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(redact_path(a) if isinstance(a, str) else a for a in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(AccessLogRedactionFilter())


def _log_path(scope: Scope) -> str:
    """로그용 경로: 퍼센트 인코딩된 원문(쿼리 제외)에서 공유 링크 토큰·DID를 가린 값.

    디코딩된 경로를 쓰지 않아 제어 문자가 로그 줄을 깨지 못한다. raw_path는 ASGI에서 선택 항목이라
    없으면 디코딩된 경로를 다시 퍼센트 인코딩해 쓴다.
    """
    raw_path = scope.get("raw_path")
    path = raw_path.decode("latin-1") if raw_path else quote(scope.get("path", ""), safe="/")
    return redact_path(path)


def _is_unstorable_text_error(exc: Exception) -> bool:
    """입력의 NUL·짝 없는 서로게이트 때문에 UTF-8 인코딩이나 DB 저장이 실패했는지. 그 밖의 오류는 계속 500이다."""
    if isinstance(exc, UnicodeEncodeError):
        return exc.reason == "surrogates not allowed"
    if isinstance(exc, psycopg.errors.UntranslatableCharacter):  # jsonb 값의 \u0000 (SQLSTATE 22P05)
        return True
    # text 매개변수의 NUL은 psycopg가 서버로 보내기 전에 SQLSTATE 없이 거절한다.
    return isinstance(exc, psycopg.DataError) and exc.sqlstate is None and "NUL (0x00)" in str(exc)


class UnhandledErrorMiddleware:
    """처리되지 않은 예외를 JSON 500으로 바꾼다.

    @app.exception_handler(Exception)은 CORS 미들웨어 바깥에서 실행되어 응답에 CORS 헤더가 붙지 않는다
    (다른 출처의 FE에서는 네트워크 오류로 보임). 이 미들웨어는 CORS 안쪽에서 같은 500 응답을 만든다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if not response_started and _is_unstorable_text_error(exc):
                # 요청 검사를 지나친 저장할 수 없는 문자(NUL·짝 없는 서로게이트)는 서버 결함이 아니다: 422로 답하고,
                # 'Unhandled error' 경보에 걸리지 않도록 추적(traceback) 없이 한 줄만 남긴다.
                # 예외 메시지에는 사용자 입력이 들어 있을 수 있어 예외 클래스 이름만 남긴다.
                logger.warning("Rejected unstorable text: method=%s path=%s error=%s",
                               scope.get("method"), _log_path(scope), type(exc).__name__)
                detail = [{"type": "value_error", "loc": [], "msg": f"Value error, {UNSTORABLE_TEXT_MESSAGE}"}]
                await JSONResponse(status_code=422, content={"detail": detail})(scope, receive, send)
                return
            # 내부 예외 메시지(SQL, 스택 등)는 응답에 싣지 않고 로그로만 남긴다.
            logger.exception("Unhandled error")
            if response_started:
                raise  # 응답을 이미 보내기 시작했으면 바꿀 수 없으므로 서버가 연결을 끊게 둔다.
            await JSONResponse(status_code=500, content={"detail": "Internal server error"})(scope, receive, send)


class SecurityHeadersMiddleware:
    """모든 HTTP 응답에 캐시 금지·브라우저 보안 헤더를 붙인다. 엔드포인트가 같은 헤더를 이미 넣었으면 그 값을 둔다.

    - Cache-Control: no-store: 공유 링크 조회(이름·DID·서명 원본), 로그인 토큰, 개인정보 응답을 브라우저 디스크 캐시·
      프록시·CDN에 남기지 않고, 철회·만료한 공유 링크가 캐시에서 다시 보이지 않게 한다.
    - API를 FE와 다른 도메인(ALB 직접)으로 서비스해도 같은 보호를 받도록 BE가 직접 붙인다.
      FE(S3+CloudFront) 쪽 헤더는 README "AWS 배포 체크리스트"의 CloudFront 응답 헤더 정책으로 붙인다.
    - CSP는 frame-ancestors만 둔다: /docs(Swagger UI, development에서만 제공)가 CDN 스크립트·인라인 스크립트를 쓰므로
      불러오기 제한은 넣지 않는다.
    - HSTS는 APP_ENV=production에서만 보낸다. 브라우저는 HTTPS 응답의 HSTS만 따르는데, 개발 중 https://localhost 로 띄운
      BE가 보내면 그 브라우저에서 다른 http://localhost 개발 서버까지 1년 동안 HTTPS로 바뀌기 때문이다.
    CORS 사전 요청(OPTIONS)은 CORSMiddleware가 바깥에서 직접 답하므로 이 헤더가 붙지 않는다.
    """

    HEADERS = (
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "no-referrer"),
        ("X-Frame-Options", "DENY"),
        ("Content-Security-Policy", "frame-ancestors 'none'"),
    )
    HSTS = ("Strict-Transport-Security", "max-age=31536000")

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers_to_add = self.HEADERS + ((self.HSTS,) if get_settings().app_env == "production" else ())

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])  # ASGI에서 headers는 선택 항목이다
                headers = MutableHeaders(scope=message)
                for name, value in headers_to_add:
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RouteBodyLimitMiddleware:
    """요청마다 그 경로의 본문 상한(body_limit)을 건다: 기본 64 KiB, 공개 인증 API 16 KiB, 검증 320 KiB, 연구자 반입 2 MiB.

    바깥의 RequestBodyLimitMiddleware(2 MiB) 안쪽에 둔다: 안쪽에 같은 미들웨어를 한 번 더 두면 그 요청의 상한만 이 값으로
    바뀌고, 413 응답(Content-Length가 크면 텍스트, chunked면 JSON)은 바깥 것과 똑같이 나간다.
    경로는 라우터와 같은 규칙으로 본다 (get_route_path: root_path를 뗀 값). scope["path"]를 그대로 비교하면 uvicorn을
    --root-path로 띄웠을 때 경로 앞에 root_path가 붙어 목록과 맞지 않는다.
    줄바꿈으로 끝나는 경로('…/login%0A')는 라우터에 넘기지 않고 여기서 404로 답한다 (__call__의 설명 참고).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.limited = {limit: RequestBodyLimitMiddleware(app, max_body_size=limit)
                        for limit in {MAX_DEFAULT_BODY_BYTES, *_POST_BODY_LIMITS.values()}}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = get_route_path(scope)
        if path.endswith("\n"):
            # 라우터의 경로 식은 `$`로 끝나 끝의 줄바꿈 하나를 무시한다 ('/api/v1/users/login\n'도 로그인 라우트에 닿는다).
            # 그런 경로는 위의 경로별 상한 목록(글자 그대로 비교)과 맞지 않아 기본 상한을 받으므로, 라우터에 넘기지 않고 없는
            # 경로로 답한다 (경로별 상한·WAF의 경로 규칙과 어긋나지 않게). 다른 제어 문자가 든 경로는 지금처럼 라우터가 정한다.
            await JSONResponse(status_code=404, content={"detail": "Not Found"})(scope, receive, send)
            return
        await self.limited[body_limit(scope.get("method", ""), path)](scope, receive, send)


# - redirect_slashes=False: 끝에 /가 붙은 경로를 307로 돌려보내지 않는다. 그 Location은 요청의 Host 헤더로 만들어져
#   CloudFront 뒤에서는 ALB 주소가 http://로 드러난다. FE는 끝에 /가 붙은 경로를 부르지 않으며, 그런 경로는 이제 404다.
# - API 문서(/docs·/redoc·/openapi.json)는 APP_ENV=production에서는 만들지 않는다 (development는 그대로).
_DOCS_OFF = {"docs_url": None, "redoc_url": None, "openapi_url": None} if get_settings().app_env == "production" else {}
app = FastAPI(title="VC AI Career Agent BE", redirect_slashes=False, **_DOCS_OFF)

# add_middleware는 나중에 추가한 것이 바깥이 된다.
# 바깥 → 안쪽: CORS → 보안 헤더 → 처리되지 않은 예외 → 본문 상한 → 경로별 본문 상한 → 앱.
# - CORS는 항상 마지막(가장 바깥)에 추가해 500·413·422 응답에도 CORS 헤더가 붙게 한다.
# - 보안 헤더는 오류 미들웨어 바깥에 두어 500 응답에도 붙게 한다.
# - 본문 상한은 오류 미들웨어 안쪽에 둔다 (바깥에 두면 413을 보낼 때 쓰는 내부 예외가 "Unhandled error"로 기록된다).
#   본문을 읽는 미들웨어를 새로 넣으면 본문 상한보다 안쪽에 둔다.
app.add_middleware(RouteBodyLimitMiddleware)
app.add_middleware(RequestBodyLimitMiddleware, max_body_size=MAX_REQUEST_BODY_BYTES)
app.add_middleware(UnhandledErrorMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    # 인증은 Authorization 헤더(Bearer)로 하므로 쿠키 자격증명은 쓰지 않는다.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "X-Service-Key"],
)


class _AsciiJSONResponse(JSONResponse):
    """짝 없는 서로게이트도 \\uXXXX로 이스케이프해 항상 인코딩되는 JSON 응답."""

    def render(self, content) -> bytes:
        return json.dumps(content, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    # FastAPI 기본 422는 오류마다 잘못된 입력값(input)을 통째로 되돌려 준다. 로그인 없이 부르는 가입·로그인·Google 콜백에
    # 큰 본문을 보내면 걸린 필드 수만큼 곱해진 응답(1.7 MiB → 3~4.5 MiB)을 만드느라 이벤트 루프가 멈추고 메모리가 몇 배로
    # 늘었다. 그래서 모든 422는 위치(loc)·사유(msg)·종류(type)만 싣는다 (input·ctx는 싣지 않는다. FE도 loc·msg만 읽는다).
    # - 오류는 앞에서부터 MAX_VALIDATION_ERRORS개만 싣는다: 모르는 키를 금지한 모델은 키마다 오류를 하나씩 낸다.
    # - loc에는 보낸 쪽이 정한 키 이름이 들어가므로 MAX_VALIDATION_LOC_CHARS자를 넘는 이름은 자리표시로 바꾼다.
    # - 키 이름에 짝 없는 서로게이트(JSON "\ud800")가 있어도 인코딩되도록 항상 ASCII 이스케이프로 보낸다.
    errors = []
    for error in exc.errors()[:MAX_VALIDATION_ERRORS]:
        loc = [part if isinstance(part, int) else str(part) for part in error.get("loc", ())]
        loc = [part if isinstance(part, int) or len(part) <= MAX_VALIDATION_LOC_CHARS else "<long key>" for part in loc]
        errors.append({"type": error.get("type"), "loc": loc, "msg": error.get("msg")})
    return _AsciiJSONResponse(status_code=422, content={"detail": errors})


# 외부 서비스별 고정 문구: (설정 누락 등으로 호출할 수 없음 503, 그 밖의 실패 502). FE가 이 문구로 안내를 고른다.
_PROVIDER_ERROR_DETAILS = {
    "credential": ("Credential service is not configured", "Credential service error"),
    "google_oauth": ("Google OAuth is not configured", "Google OAuth error"),
}


@app.exception_handler(ProviderError)
async def provider_error_handler(_: Request, exc: ProviderError):
    # 협력사 오류 코드·문구와 설정 이름은 운영 로그에만 남긴다 (message의 한 줄·길이 제한은 ProviderError가 한다).
    # 응답에는 고정 문구와 기계용 code만 싣는다. 상태 코드(설정 누락 등으로 호출할 수 없으면 503, 그 밖 502)는 그대로다.
    # 문구만 서비스(exc.service)에 따라 고른다: Google 로그인 실패를 발급·검증 인프라 문구로 답하지 않는다.
    logger.warning("Provider error: %s (upstream=%s)", exc.message, exc.upstream_status)
    not_configured, failed = _PROVIDER_ERROR_DETAILS.get(exc.service, _PROVIDER_ERROR_DETAILS["credential"])
    if exc.unavailable:
        content = {"detail": not_configured, "code": "PROVIDER_NOT_CONFIGURED"}
        return JSONResponse(status_code=503, content=content)
    return JSONResponse(status_code=502, content={"detail": failed, "code": "PROVIDER_ERROR"})


@app.exception_handler(VerifierBudgetExceeded)
async def verifier_budget_handler(_: Request, exc: VerifierBudgetExceeded):
    # 로그인 사용자·서비스 키의 검증기(DXWorks) 호출 몫이 모자란 요청: 협력사를 부르지 않고 429 + Retry-After로 답한다.
    # 감사 로그(vc-be.audit, event=verifier_budget_denied)는 몫을 가져가는 쪽(verifier_budget.py)이 남긴다.
    return JSONResponse(status_code=429, content={"detail": TOO_MANY_VERIFICATIONS},
                        headers={"Retry-After": str(exc.retry_after)})


@app.exception_handler(DatabaseBusy)
@app.exception_handler(psycopg.errors.QueryCanceled)
@app.exception_handler(psycopg.errors.LockNotAvailable)
async def database_busy_handler(request: Request, exc: Exception):
    # DB가 바쁜 요청: 앱 DB 연결 자리를 얻지 못했거나(DatabaseBusy), 문장이 세션 시간 제한에 걸렸거나(QueryCanceled), 잠금을
    # 제한 시간 안에 얻지 못했다(LockNotAvailable). 한도는 database.app_db_connection이 건다. 서버 결함(500)이 아니라 잠시 뒤
    # 다시 하면 되는 상태라 503 + Retry-After로 답하고, 요청마다 추적(traceback)을 쌓지 않게 경고 한 줄만 남긴다.
    # 예외 메시지에는 SQL 조각이 들어 있을 수 있어 예외 클래스 이름만 남긴다.
    logger.warning("Database busy: method=%s path=%s reason=%s",
                   request.method, _log_path(request.scope), type(exc).__name__)
    return JSONResponse(status_code=503, content={"detail": DB_BUSY},
                        headers={"Retry-After": str(DB_BUSY_RETRY_AFTER_SECONDS)})


@app.exception_handler(AIServiceError)
async def ai_service_error_handler(request: Request, exc: AIServiceError):
    # 상태 코드·detail은 그대로 두고 기계용 code만 더한다.
    # 로그에는 분류 정보만 남긴다: 요청·응답 본문, 토큰, 예외 메시지(형식 검증 오류에는 AI 응답 값이 들어 있음)는 남기지 않는다.
    # 경로는 퍼센트 인코딩된 원문(raw_path, 쿼리 제외)을 써서 디코딩된 제어 문자가 로그 줄을 깨지 못하게 한다.
    # raw_path는 ASGI에서 선택 항목이라 None일 수 있다(그때는 디코딩된 경로를 다시 퍼센트 인코딩해 쓴다).
    raw_path = request.scope.get("raw_path")
    path = raw_path.decode("latin-1") if raw_path else quote(request.url.path, safe="/")
    cause = exc.__cause__
    logger.warning(
        "AI service error: method=%s path=%s status=%s code=%s retryable=%s upstream=%s upstream_status=%s cause=%s",
        request.method, path, exc.status_code, exc.code, exc.retryable, exc.upstream, exc.upstream_status,
        type(cause).__name__ if cause is not None else None,
    )
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code},
                        headers=exc.headers)


# async: I/O가 없으므로 이벤트 루프에서 바로 답한다. 동기 함수면 워커 스레드를 하나 써야 해서, DB 자리·잠금을 기다리는
# 요청이 스레드를 모두 잡고 있는 동안(각 최대 5초) 상태 확인(ALB·ECS, 제한 4~5초)이 함께 밀려 실패한다.
@app.get("/health")
async def health_check():
    return {"status": "ok", "message": "VC AI Career Agent BE (FastAPI) is running"}


app.include_router(verify.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1/users")
app.include_router(oauth.router, prefix="/api/v1/users/oauth")
app.include_router(user.router, prefix="/api/v1/subjects")
app.include_router(mock_issuer.router, prefix="/api/mock-issuer")
app.include_router(reverify.router, prefix="/api/v1")
app.include_router(portfolios.router, prefix="/api/v1/portfolios")
app.include_router(shares.router, prefix="/api/v1/shares")
app.include_router(shares.public_router, prefix="/api/v1/public/shares")
app.include_router(career.router, prefix="/api/v1")
app.include_router(market.router, prefix="/api/v1")

if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", 3000))
    # 코드 변경 자동 반영(reload)은 개발용이라 기본으로 끈다. UVICORN_RELOAD가 참 값일 때만 켠다.
    # uvicorn CLI(Dockerfile·compose의 `uvicorn main:app ...`)도 같은 변수를 읽어 리로더를 켜므로 같은 참 값 목록을 쓰고,
    # 배포 환경(ECS 작업 정의 등)에는 이 변수를 넣지 않는다.
    reload = os.getenv("UVICORN_RELOAD", "").strip().lower() in ("1", "true", "t", "yes", "y", "on")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=reload)

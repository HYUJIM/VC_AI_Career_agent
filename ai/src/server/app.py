"""AI service for the VC AI Career Agent BE.

Run from the `ai/` folder:  uvicorn src.server.app:app --host 127.0.0.1 --port 8100

- Every /v1 route requires `Authorization: Bearer $AI_SERVICE_TOKEN` (>= 32 bytes), checked
  before any byte of the request body is read.
- Request bodies are capped at AI_MAX_BODY_BYTES while they stream, with or without Content-Length.
- Every response except an unhandled-exception 500 carries the contract ID; a client
  announcing another ID is refused.
- Request bodies (verified evidence, job and resource texts) are never logged or echoed.
- No database, no keys, no VC originals: BE re-verifies credentials and stores results.
"""

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette._utils import get_route_path
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import HTTPConnection

from src.career.career_analysis import analyze, search
from src.career.contracts import CONTRACT_HEADER, CONTRACT_ID
from src.career.contracts.api import (
    CareerAnalysisInput,
    CareerAnalysisOutput,
    GenerationInput,
    GenerationOutcome,
    GeneratorInfo,
    JobSearchRequest,
    JobSearchResult,
    JobValidationRequest,
    PlanningInput,
    RequirementPreview,
    ResourceSearchRequest,
    ResourceSearchResult,
    ResourceValidationRequest,
    ValidationResult,
)
from src.career.contracts.career import PreviewRequest, SkillCatalog
from src.career.contracts.curriculum import PlanningContext
from src.career.curriculum_generator import generate
from src.career.curriculum_service import planning_context, search_resources
from src.career.errors import ConfigurationError, EvidenceError, Rejection
from src.career.job_requirements import preview_requirements, validate_annotations
from src.career.learning_retrieval import validate_resource
from src.career.llm_client import generator_from_env
from src.career.skill_catalog import CATALOG

if os.getenv("CAREER_AI_SKIP_DOTENV", "false").lower() != "true":
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # ai/.env; never overrides

DEFAULT_MAX_BODY_BYTES = 32 * 1024 * 1024
TOO_LARGE = "Request too large for the AI service"
bearer = HTTPBearer(auto_error=False)


def error(status, code, message):
    return JSONResponse(status_code=status, content={"detail": message, "code": code})


def require_service_token(
    authorization: HTTPAuthorizationCredentials | None = Depends(bearer),
):
    expected = os.getenv("AI_SERVICE_TOKEN", "")
    if len(expected.encode("utf-8")) < 32:
        raise Rejection(503, "AI_SERVICE_TOKEN_NOT_CONFIGURED", "AI service token not configured")
    if authorization is None or not secrets.compare_digest(
        authorization.credentials.encode("utf-8"), expected.encode("utf-8")
    ):
        raise Rejection(401, "SERVICE_AUTH_REQUIRED", "Valid service token required")


class BodyTooLarge(HTTPException):
    """본문을 읽는 도중 AI_MAX_BODY_BYTES를 넘으면 ServiceGuard의 receive()가 던진다.

    FastAPI는 본문을 읽다 난 예외 중 HTTPException만 그대로 다시 던지고 나머지는 400으로 바꾸므로
    HTTPException으로 두고, create_app의 핸들러가 413 REQUEST_TOO_LARGE로 답한다.
    """

    def __init__(self):
        super().__init__(413, TOO_LARGE)


class ServiceGuard:
    """계약 ID·서비스 토큰·본문 크기를 FastAPI가 본문을 읽기 전에 검사하는 순수 ASGI 미들웨어.

    라우트 의존성(require_service_token)은 FastAPI가 본문을 다 읽고 JSON으로 푼 뒤에야 돌고,
    청크 전송에는 Content-Length가 없다. 그래서 헤더만으로 정할 수 있는 거절(계약 409 → `/v1` 토큰
    503/401 → 선언된 크기 413)은 본문을 한 바이트도 읽지 않고 답하고, 통과한 요청에는 읽은 바이트를
    세다가 상한을 넘는 순간 413으로 끊는 receive()를 넘긴다. 모든 응답에 계약 ID 헤더를 붙인다.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_contract(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])  # ASGI에서 headers는 생략할 수 있다.
                MutableHeaders(scope=message)[CONTRACT_HEADER] = CONTRACT_ID
            await send(message)

        limit = int(os.getenv("AI_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES))
        try:
            await self.check_headers(scope, limit)
        except Rejection as exc:
            response = error(exc.status, exc.code, exc.message)
            await response(scope, receive, send_with_contract)
            return

        received = 0

        async def receive_with_limit():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                raise BodyTooLarge()
            return message

        await self.app(scope, receive_with_limit, send_with_contract)

    @staticmethod
    async def check_headers(scope, limit):
        headers = Headers(scope=scope)
        announced = headers.get(CONTRACT_HEADER)
        if announced is not None and announced != CONTRACT_ID:
            raise Rejection(
                409, "CONTRACT_MISMATCH", "AI contract mismatch; sync BE/app/ai_contract"
            )
        path = get_route_path(scope)  # 라우터가 경로를 찾을 때와 같은 값 (root_path 제외)
        if path == "/v1" or path.startswith("/v1/"):
            # 라우트 의존성과 같은 파서·비교(compare_digest). 의존성은 그대로 두어 한 번 더 막는다.
            require_service_token(await bearer(HTTPConnection(scope)))
        length = headers.get("content-length")
        # 18자를 넘는 값은 int()에 넘기지 않고 413: 4300자리를 넘는 숫자(앞의 0 포함)는 int()가
        # ValueError를 내 처리되지 않은 500이 된다. 0을 앞에 붙이지 않은 19자리 수는 1 EB 이상이다.
        if length is not None and (
            not (length.isascii() and length.isdigit()) or len(length) > 18 or int(length) > limit
        ):
            raise Rejection(413, "REQUEST_TOO_LARGE", TOO_LARGE)


def create_app() -> FastAPI:
    app = FastAPI(
        title="VC AI Career Agent AI",
        version="0.1.0",
        description="Stateless evidence comparison and curriculum AI for the VC career agent BE.",
    )
    # Overridable by tests and research harnesses; production uses the environment.
    app.state.clock = lambda: datetime.now(timezone.utc)
    app.state.generator_loader = generator_from_env
    app.state.selector_client_factory = None

    app.add_middleware(ServiceGuard)

    @app.exception_handler(Rejection)
    async def rejection_handler(request, exc: Rejection):
        return error(exc.status, exc.code, exc.message)

    @app.exception_handler(BodyTooLarge)
    async def body_too_large_handler(request, exc: BodyTooLarge):
        return error(413, "REQUEST_TOO_LARGE", TOO_LARGE)

    @app.exception_handler(EvidenceError)
    async def evidence_handler(request, exc: EvidenceError):
        return error(422, str(exc), str(exc))

    @app.exception_handler(ConfigurationError)
    async def configuration_handler(request, exc: ConfigurationError):
        return error(503, "AI_CONFIGURATION_REQUIRED", "AI service configuration required")

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc: RequestValidationError):
        # Never echo inputs: they may contain personal data from verified credentials.
        errors = [{"loc": i["loc"], "msg": i["msg"], "type": i["type"]} for i in exc.errors()]
        return JSONResponse(
            status_code=422,
            content={"detail": errors, "code": "REQUEST_SCHEMA_INVALID"},
        )

    @app.get("/health")
    def health():
        return {"status": "ok", "contract": CONTRACT_ID}

    secured = [Depends(require_service_token)]

    @app.get("/v1/contract", dependencies=secured)
    def contract():
        return {"contract": CONTRACT_ID}

    # --- Skills, job requirements and comparison ------------------------------------

    @app.get("/v1/skills", response_model=SkillCatalog, dependencies=secured)
    def skills():
        return CATALOG

    @app.post("/v1/job-postings/preview", response_model=RequirementPreview, dependencies=secured)
    def job_preview(body: PreviewRequest):
        return RequirementPreview(candidates=preview_requirements(body.raw_text))

    @app.post("/v1/job-postings/validate", response_model=ValidationResult, dependencies=secured)
    def job_validate(body: JobValidationRequest):
        validate_annotations(body.posting, app.state.clock())
        return ValidationResult()

    @app.post("/v1/job-postings/search", response_model=JobSearchResult, dependencies=secured)
    def job_search(body: JobSearchRequest):
        return search(body.request, body.candidates, app.state.clock())

    @app.post("/v1/career-analyses", response_model=CareerAnalysisOutput, dependencies=secured)
    def career_analysis(body: CareerAnalysisInput):
        result = analyze(
            body.snapshot, body.request, body.candidates, body.target, app.state.clock()
        )
        return CareerAnalysisOutput(analysis=result, catalog=CATALOG)

    # --- Learning resources and curriculum ------------------------------------------

    @app.post(
        "/v1/learning-resources/validate", response_model=ValidationResult, dependencies=secured
    )
    def resource_validate(body: ResourceValidationRequest):
        validate_resource(body.resource, app.state.clock())
        return ValidationResult()

    @app.post(
        "/v1/learning-resources/search", response_model=ResourceSearchResult, dependencies=secured
    )
    def resource_search(body: ResourceSearchRequest):
        sources = search_resources(
            body.resources, body.skill_id, body.budget_krw, app.state.clock()
        )
        return ResourceSearchResult(sources=sources)

    @app.get("/v1/curricula/generator", response_model=GeneratorInfo, dependencies=secured)
    def generator_info():
        try:
            config = app.state.generator_loader()
        except ConfigurationError as exc:
            raise Rejection(
                503,
                "GENERATOR_CONFIGURATION_REQUIRED",
                "Curriculum generator configuration required",
            ) from exc
        return GeneratorInfo(generator=config.public_metadata())

    @app.post("/v1/curricula/context", response_model=PlanningContext, dependencies=secured)
    def curriculum_context(body: PlanningInput):
        return planning_context(body.analysis, body.resources, body.request, app.state.clock())

    @app.post("/v1/curricula/generate", response_model=GenerationOutcome, dependencies=secured)
    async def curriculum_generate(body: GenerationInput):
        return await generate(body, app.state.selector_client_factory, app.state.generator_loader)

    return app


app = create_app()

"""AI service for the VC AI Career Agent BE.

Run from the `ai/` folder:  uvicorn src.server.app:app --host 127.0.0.1 --port 8100

- Every /v1 route requires `Authorization: Bearer $AI_SERVICE_TOKEN` (>= 32 bytes).
- Every response carries the contract ID; a client announcing another ID is refused.
- Request bodies (verified evidence, job and resource texts) are never logged or echoed.
- No database, no keys, no VC originals: BE re-verifies credentials and stores results.
"""

import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

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

    @app.middleware("http")
    async def contract_and_size(request: Request, call_next):
        announced = request.headers.get(CONTRACT_HEADER)
        limit = int(os.getenv("AI_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES))
        length = request.headers.get("content-length")
        if announced is not None and announced != CONTRACT_ID:
            response = error(
                409, "CONTRACT_MISMATCH", "AI contract mismatch; sync BE/app/ai_contract"
            )
        elif length is not None and (not length.isdigit() or int(length) > limit):
            response = error(413, "REQUEST_TOO_LARGE", "Request too large for the AI service")
        else:
            response = await call_next(request)
        response.headers[CONTRACT_HEADER] = CONTRACT_ID
        return response

    @app.exception_handler(Rejection)
    async def rejection_handler(request, exc: Rejection):
        return error(exc.status, exc.code, exc.message)

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

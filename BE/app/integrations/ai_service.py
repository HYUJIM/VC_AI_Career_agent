"""AI 서비스(ai/, `uvicorn src.server.app:app`) 클라이언트.

BE는 자격증명을 실행 시점에 다시 검증한 결과, 연구자가 반입한 공고·학습자료 버전만 AI로 보낸다.
VC 서명 원본·비밀번호·키는 보내지 않는다. AI는 계산만 하고 DB·키를 갖지 않는다.

- 서비스 토큰(AI_SERVICE_TOKEN)과 공유 형식 ID(app/ai_contract)를 모든 요청에 싣고, 응답의 ID도 확인한다.
- 오류는 모두 AIServiceError 하위 클래스다. main.py의 핸들러가 상태 코드·detail은 그대로 두고
  기계용 `code`를 더해 `{"detail", "code"}`로 답하며, 분류 정보(code·upstream 등)만 로그로 남긴다.
  AIRejected: AI가 거절한 요청 — AI의 4xx 상태·메시지·코드를 그대로 전달 (예: 공고 주석 오류 422) /
  AIUnavailable: 연결 실패·시간 초과·AI 5xx·인증 실패·설정 누락(503) /
  AIContractViolation: 형식 ID가 다르거나, AI가 BE 요청을 형식 오류로 거절했거나,
  응답이 형식·BE 재검사를 통과하지 못함(502).
- 공고·학습자료 묶음을 통째로 싣는 요청(공고 검색·직무 비교·학습자료 검색·커리큘럼 계획)은 프로세스마다 동시에
  AI_MAX_CONCURRENCY개(기본 4)까지만 보낸다. 자리가 없으면 AI_SLOT_WAIT_SECONDS 동안 기다린 뒤 AI를 부르지 않고
  503(code AI_SERVICE_BUSY) + Retry-After로 답한다. 이 요청들은 묶음을 DB에서 읽기 전에 AIServiceClient.corpus_slot으로
  자리를 먼저 잡는다: 자리를 얻지 못할 요청이 묶음을 메모리에 올리지 않는다. 자리를 쥔 채로는 검증기(DXWorks)를 부르지
  않는다 (직무 비교는 자격증명 재검증을 마친 뒤에 자리를 잡는다: 협력사가 느려도 다른 묶음 요청이 그 뒤에 서지 않는다).
- 묶음 요청은 사용자마다도 묶는다 (corpus_slot(upstream, caller)). 위 자리는 프로세스 전체에 하나라, 무료 계정 하나가
  공고 목록을 초당 10번씩 부르면 다른 사용자의 학습자료 검색·직무 비교가 자리를 기다리다 503이 됐다.
  사용자 하나가 동시에 쥘 수 있는 자리는 CORPUS_IN_FLIGHT_PER_USER개, 묶음 요청 수는 분당 CORPUS_REQUESTS_PER_MINUTE번·
  시간당 CORPUS_REQUESTS_PER_HOUR번(토큰 버킷)이다. 넘으면 기다리지 않고 DB·AI를 부르지 않은 채 429 + Retry-After로 답한다
  (감사 로그 vc-be.audit 한 줄. 프로세스 메모리라 프로세스마다 따로 세고 재시작하면 지워진다: IP별 제한은 운영 nginx가 맡는다).
"""
import asyncio
import contextvars
import logging
import threading
import weakref
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError

from app.ai_contract import CONTRACT_HEADER, CONTRACT_ID
from app.ai_contract.api import (
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
from app.ai_contract.career import PreviewRequest, SkillCatalog
from app.ai_contract.curriculum import PlanningContext
from app.ai_contract.market import MarketSnapshotOutput, MarketSnapshotRequest
from app.core.config import get_settings
from app.services.verifier_budget import MAX_KEYS, VerifierBudget, VerifierBudgetExceeded

audit = logging.getLogger("vc-be.audit")

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
NON_RETRYABLE = {"AI_CONFIGURATION_REQUIRED", "AI_SERVICE_TOKEN_NOT_CONFIGURED",
                 "GENERATOR_CONFIGURATION_REQUIRED"}
# 공고 검색·직무 비교는 요청마다 그 직무의 검토된 공고 전체(최대 1,000건, 수 MiB)를, 학습자료 검색·커리큘럼 계획은 학습자료
# 전체(최대 500건)를 AI로 보낸다. 한 계정의 병렬 요청 수십 개가 그대로 나가면 BE와 AI의 메모리가 함께 GiB 단위로 늘어나므로
# 동시에 보내는 수를 묶는다 (BE가 AI의 유일한 클라이언트라 AI 쪽 부하도 함께 묶인다).
# BE 쪽 메모리는 자리를 잡은 뒤에 묶음을 읽는 요청(corpus_slot 안에서 DB를 읽는 API)만 묶인다: 자리를 기다리는 요청은
# 아직 묶음을 올리지 않았다. corpus_slot 밖에서 묶음을 먼저 읽고 부르는 호출이 생기면 그 요청 수만큼 다시 늘어난다.
# 자리를 이만큼(초) 기다려도 없으면 AI를 부르지 않고 503 + Retry-After로 답한다.
AI_SLOT_WAIT_SECONDS = 10.0
AI_BUSY_RETRY_AFTER_SECONDS = 5
# asyncio.Semaphore는 처음 기다린 이벤트 루프에 묶이므로(테스트는 루프를 여러 개 만든다) 도는 루프마다 하나씩 둔다
# (dxworks.py의 동시 호출 상한과 같은 방식). 운영에서는 프로세스에 루프가 하나라 프로세스 전체 상한이다.
# 값: (만들 때의 AI_MAX_CONCURRENCY, 세마포어). 설정이 바뀌면 다음 요청부터 새 세마포어를 쓴다.
# 한 번이라도 기다린 요청이 있던 세마포어는 자기 루프를 붙잡고 있어 약한 참조만으로는 지워지지 않으므로, 새 세마포어를
# 만들 때 닫힌 루프의 항목을 지운다.
_corpus_slots: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]]" = (
    weakref.WeakKeyDictionary())
# 이 요청(태스크)이 묶음 요청 자리를 이미 잡고 있는지. 잡고 있으면 그 안의 묶음 호출은 자리를 다시 잡지 않는다
# (자리를 쥔 채 두 번째 자리를 기다리면 요청끼리 서로 막힌다).
_holds_corpus_slot: contextvars.ContextVar[bool] = contextvars.ContextVar("holds_corpus_slot", default=False)

# 사용자별 묶음 요청 한도 (corpus_slot의 caller). 화면은 공고 목록 다시 불러오기와 직무 비교·학습 계획이 겹칠 수 있어
# 동시 2개까지 받는다 (기본 AI_MAX_CONCURRENCY 4자리 중 절반: 계정 하나가 자리를 모두 쥐지 못한다).
CORPUS_IN_FLIGHT_PER_USER = 2
CORPUS_REQUESTS_PER_MINUTE = 30
CORPUS_REQUESTS_PER_HOUR = 300
CORPUS_IN_FLIGHT_RETRY_AFTER_SECONDS = 2
# FE가 이 문구로 안내를 고른다 (FE/src/lib/apiClient.ts). 바꾸면 FE도 함께 바꾼다.
TOO_MANY_ANALYSIS_REQUESTS = "Too many analysis requests. Try again later."
# 사용자별 묶음 요청 수 (분·시간 토큰 버킷: verifier_budget.VerifierBudget과 같은 계산, 키는 MAX_KEYS개까지).
# 프로세스 전체 상한은 위 자리(AI_MAX_CONCURRENCY)가 맡으므로 이 버킷의 프로세스 몫은 닿지 않을 만큼 크게 둔다.
corpus_request_budget = VerifierBudget(calls_per_minute=CORPUS_REQUESTS_PER_MINUTE,
                                       calls_per_hour=CORPUS_REQUESTS_PER_HOUR,
                                       process_calls_per_minute=CORPUS_REQUESTS_PER_MINUTE * MAX_KEYS)
# 사용자 id → 지금 묶음 요청 자리를 쥐었거나 기다리는 요청 수 (0이 되면 지운다).
_corpus_in_flight: dict[str, int] = {}
_corpus_in_flight_lock = threading.Lock()


class AIServiceError(HTTPException):
    def __init__(self, status_code: int, detail: str, code: str, retryable: bool = False):
        super().__init__(status_code=status_code, detail=detail)
        self.code, self.retryable = code, retryable
        # 로그용 분류 정보 (_call이 채운다). 요청·응답 본문은 담지 않는다.
        self.upstream: str | None = None          # 예: "POST /v1/career-analyses"
        self.upstream_status: int | None = None   # AI가 응답한 HTTP 상태 (응답을 못 받았으면 None)


class AIRejected(AIServiceError):
    """AI가 요청을 거절했다. BE는 같은 상태 코드와 메시지를 돌려준다."""


class AIUnavailable(AIServiceError):
    """연결 실패, AI 장애 또는 설정 누락 (503)."""


class AIContractViolation(AIServiceError):
    """공유 형식이 다르거나 응답이 BE 재검사를 통과하지 못했다 (502)."""


def contract_violation(code: str = "AI_RESPONSE_INVALID") -> AIContractViolation:
    return AIContractViolation(502, "AI service response failed contract checks", code)


def _unavailable(detail: str = "AI service temporarily unavailable", code: str = "AI_SERVICE_UNAVAILABLE",
                 retryable: bool = True) -> AIUnavailable:
    return AIUnavailable(503, detail, code, retryable)


async def _take_corpus_slot(upstream: str) -> asyncio.Semaphore:
    """묶음 요청 자리 하나를 잡고, 끝나면 release()할 세마포어를 돌려준다. 기다려도 자리가 없으면 503 AI_SERVICE_BUSY."""
    limit = get_settings().ai_max_concurrency
    loop = asyncio.get_running_loop()
    entry = _corpus_slots.get(loop)
    if entry is None or entry[0] != limit:
        for known in [known for known in _corpus_slots if known.is_closed()]:
            del _corpus_slots[known]
        entry = _corpus_slots[loop] = (limit, asyncio.Semaphore(limit))
    slots = entry[1]
    if not slots.locked():
        await slots.acquire()  # 자리가 있으면 기다리지 않고 바로 잡는다
        return slots
    try:
        await asyncio.wait_for(slots.acquire(), timeout=AI_SLOT_WAIT_SECONDS)
    except asyncio.TimeoutError:
        error = _unavailable("AI service is busy. Try again later.", "AI_SERVICE_BUSY")
        error.headers = {"Retry-After": str(AI_BUSY_RETRY_AFTER_SECONDS)}
        error.upstream = upstream  # AI에는 아무것도 보내지 않았다 (upstream_status는 None으로 남는다)
        raise error from None
    return slots


def _enter_caller(caller: str, upstream: str) -> None:
    """사용자별 묶음 요청 한도를 본다: 동시 수(CORPUS_IN_FLIGHT_PER_USER)와 분·시간 몫. 통과하면 동시 수를 하나 올린다.

    넘으면 기다리지 않고 429 + Retry-After (DB의 묶음도 AI도 부르지 않는다). 감사 로그(vc-be.audit) 한 줄을 남긴다.
    동시 수로 거절한 요청은 몫을 가져가지 않는다.
    """
    with _corpus_in_flight_lock:
        if _corpus_in_flight.get(caller, 0) >= CORPUS_IN_FLIGHT_PER_USER:
            reason, retry_after = "in_flight", CORPUS_IN_FLIGHT_RETRY_AFTER_SECONDS
        else:
            try:
                corpus_request_budget.reserve(caller, 1)
            except VerifierBudgetExceeded as exc:
                reason, retry_after = "rate", exc.retry_after
            else:
                _corpus_in_flight[caller] = _corpus_in_flight.get(caller, 0) + 1
                return
    audit.info("event=corpus_budget_denied user=%s route=%s reason=%s retry_after=%d",
               caller, upstream.rsplit(" ", 1)[-1], reason, retry_after)
    raise HTTPException(status_code=429, detail=TOO_MANY_ANALYSIS_REQUESTS,
                        headers={"Retry-After": str(retry_after)})


def _leave_caller(caller: str) -> None:
    with _corpus_in_flight_lock:
        remaining = _corpus_in_flight.get(caller, 0) - 1
        if remaining > 0:
            _corpus_in_flight[caller] = remaining
        else:
            _corpus_in_flight.pop(caller, None)


def clear_corpus_caller_limits() -> None:
    """사용자별 묶음 요청 한도를 비운다 (테스트용: conftest가 테스트마다 부른다)."""
    corpus_request_budget.clear()
    with _corpus_in_flight_lock:
        _corpus_in_flight.clear()


def _settings() -> tuple[str, str]:
    settings = get_settings()
    url, token = settings.ai_service_url, settings.ai_service_token
    try:
        parsed = urlsplit(url)
        parsed.port  # 잘못된 포트는 요청 전에 거절한다.
    except ValueError:
        raise _unavailable("AI service is not configured", "AI_SERVICE_NOT_CONFIGURED", False) from None
    http_ok = parsed.hostname in LOOPBACK_HOSTS or settings.ai_service_allow_http
    if (
        parsed.scheme not in {"http", "https"}
        or (parsed.scheme == "http" and not http_ok)
        or not parsed.hostname
        or parsed.username or parsed.password
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
        or len(token.encode("utf-8")) < 32
        or any(ch.isspace() for ch in token)
    ):
        raise _unavailable("AI service is not configured", "AI_SERVICE_NOT_CONFIGURED", False)
    return url, token


def _decode(response: httpx.Response, model: type[BaseModel]):
    announced = response.headers.get(CONTRACT_HEADER)
    if response.status_code >= 500 and announced != CONTRACT_ID:
        raise _unavailable(code="AI_SERVICE_ERROR")  # 프록시·비정상 종료 등
    if announced != CONTRACT_ID:
        raise contract_violation("AI_CONTRACT_MISMATCH")
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.status_code == 200:
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise contract_violation() from exc
    code = payload.get("code") if isinstance(payload, dict) else None
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if response.status_code in (401, 403) or code == "AI_SERVICE_TOKEN_NOT_CONFIGURED":
        raise _unavailable("AI service authentication failed", "AI_SERVICE_AUTH_FAILED", False)
    if not isinstance(code, str) or not isinstance(detail, str) or code in {"REQUEST_SCHEMA_INVALID",
                                                                             "CONTRACT_MISMATCH"}:
        raise contract_violation(code if isinstance(code, str) else "AI_RESPONSE_INVALID")
    if response.status_code >= 500:
        raise _unavailable(detail, code, code not in NON_RETRYABLE)
    raise AIRejected(response.status_code, detail, code)


class AIServiceClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        # 테스트에서는 같은 프로세스의 AI 앱(ASGI)을 transport로 넣는다.
        self.transport = transport

    @asynccontextmanager
    async def caller_slot(self, upstream: str, caller: str):
        """사용자별 묶음 요청 한도(_enter_caller)를 corpus_slot보다 먼저 잡고 이 구간이 끝날 때 돌려준다.

        묶음 요청 앞에 비싼 일(직무 비교의 검증기 재검증)이 있는 API가 쓴다: 한도를 넘는 요청은 그 일을 하기 전에 429로
        끝난다. 이 구간 안의 corpus_slot에는 caller를 주지 않는다 (주면 같은 요청을 두 번 센다).
        """
        _enter_caller(caller, upstream)
        try:
            yield
        finally:
            _leave_caller(caller)

    @asynccontextmanager
    async def corpus_slot(self, upstream: str, caller: Optional[str] = None):
        """묶음 요청 자리 하나를 잡고 이 구간이 끝날 때(오류·취소 포함) 돌려준다. upstream은 로그용 호출 이름이다.

        묶음을 DB에서 읽기 전에 이 구간으로 감싸면, 자리를 얻지 못할 요청은 묶음을 읽지 않고 503 AI_SERVICE_BUSY로
        끝난다 (기다리는 시간·응답은 _take_corpus_slot과 같다). 이 구간 안의 묶음 호출(_call(corpus=True))과 겹쳐 쓴
        corpus_slot은 자리를 다시 잡지 않는다. 이 구간 안에서 검증기(DXWorks)처럼 오래 걸릴 수 있는 다른 외부 호출을
        기다리지 않는다 (자리는 모든 묶음 요청이 함께 쓴다).
        caller(사용자 id)를 주면 프로세스 전체 자리를 기다리기 전에 사용자별 한도(_enter_caller)를 먼저 본다: 넘으면 429.
        사용자 요청을 받는 API는 모두 caller를 준다.
        """
        if _holds_corpus_slot.get():
            yield
            return
        if caller is not None:
            _enter_caller(caller, upstream)
        try:
            slots = await _take_corpus_slot(upstream)
            held = _holds_corpus_slot.set(True)
            try:
                yield
            finally:
                _holds_corpus_slot.reset(held)
                slots.release()
        finally:
            if caller is not None:
                _leave_caller(caller)

    async def _call(self, method: str, path: str, model: type[BaseModel], body: BaseModel | None = None,
                    *, generation: bool = False, corpus: bool = False):
        """corpus=True: 공고·학습자료 묶음을 싣는 요청. 동시 요청 수 상한(AI_MAX_CONCURRENCY) 안에서만 보낸다."""
        if not corpus:
            return await self._send(method, path, model, body, generation=generation)
        async with self.corpus_slot(f"{method} {path}"):
            return await self._send(method, path, model, body, generation=generation)

    async def _send(self, method: str, path: str, model: type[BaseModel], body: BaseModel | None = None,
                    *, generation: bool = False):
        url, token = _settings()
        settings = get_settings()
        timeout = settings.ai_generation_timeout_seconds if generation else settings.ai_timeout_seconds
        try:
            async with httpx.AsyncClient(
                base_url=url, transport=self.transport, timeout=httpx.Timeout(timeout, connect=3.0),
                follow_redirects=False, trust_env=False,  # 검증된 개인 이력을 주변 프록시로 보내지 않는다.
            ) as client:
                response = await client.request(
                    method, path,
                    headers={"Authorization": f"Bearer {token}", CONTRACT_HEADER: CONTRACT_ID},
                    json=body.model_dump(mode="json") if body is not None else None,
                )
        except httpx.HTTPError as exc:
            error = _unavailable()
            error.upstream = f"{method} {path}"
            raise error from exc
        try:
            return _decode(response, model)
        except AIServiceError as exc:
            # 상태·detail·code·원인(__cause__)은 그대로 두고 어느 호출이 어떤 상태로 실패했는지만 붙인다.
            exc.upstream, exc.upstream_status = f"{method} {path}", response.status_code
            raise

    async def skill_catalog(self) -> SkillCatalog:
        return await self._call("GET", "/v1/skills", SkillCatalog)

    async def preview_requirements(self, raw_text: str) -> RequirementPreview:
        return await self._call("POST", "/v1/job-postings/preview", RequirementPreview,
                                PreviewRequest(raw_text=raw_text))

    async def validate_job(self, posting) -> None:
        await self._call("POST", "/v1/job-postings/validate", ValidationResult,
                         JobValidationRequest(posting=posting))

    async def search_jobs(self, request, candidates) -> JobSearchResult:
        return await self._call("POST", "/v1/job-postings/search", JobSearchResult,
                                JobSearchRequest(request=request, candidates=candidates), corpus=True)

    async def analyze(self, snapshot, request, candidates, target) -> CareerAnalysisOutput:
        body = CareerAnalysisInput(snapshot=snapshot, request=request, candidates=candidates, target=target)
        return await self._call("POST", "/v1/career-analyses", CareerAnalysisOutput, body, corpus=True)

    async def validate_resource(self, resource) -> None:
        await self._call("POST", "/v1/learning-resources/validate", ValidationResult,
                         ResourceValidationRequest(resource=resource))

    async def search_resources(self, resources, skill_id=None, budget_krw=None):
        body = ResourceSearchRequest(resources=resources, skill_id=skill_id, budget_krw=budget_krw)
        return (await self._call("POST", "/v1/learning-resources/search", ResourceSearchResult, body,
                                 corpus=True)).sources

    async def generator_info(self) -> dict:
        return (await self._call("GET", "/v1/curricula/generator", GeneratorInfo)).generator

    async def planning_context(self, analysis, resources, request) -> PlanningContext:
        body = PlanningInput(analysis=analysis, resources=resources, request=request)
        return await self._call("POST", "/v1/curricula/context", PlanningContext, body, corpus=True)

    async def market_snapshot(self, body: MarketSnapshotRequest) -> MarketSnapshotOutput:
        """직무 하나의 공고 묶음 → 통계. 공고 수백 건을 한 번에 보내므로 생성용 시간 제한을 쓴다."""
        return await self._call("POST", "/v1/market/snapshots", MarketSnapshotOutput, body, generation=True)

    async def generate_curriculum(self, body: GenerationInput) -> GenerationOutcome:
        return await self._call("POST", "/v1/curricula/generate", GenerationOutcome, body, generation=True)


ai_service = AIServiceClient()


def get_ai_service() -> AIServiceClient:
    """FastAPI 의존성. 테스트에서는 app.dependency_overrides 또는 transport 교체로 바꾼다."""
    return ai_service

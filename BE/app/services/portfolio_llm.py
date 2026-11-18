"""AI(LLM) 모드 포트폴리오: 허용 계정·동의·한도 확인, AI 서비스(Claude)에 소개 문장 요청, 실패 처리.

- 허용 계정은 학습 계획 LLM 모드와 같은 목록(CURRICULUM_LLM_USER_IDS, "*"면 모든 계정)을 쓴다. 아니면 403.
- 요청에 allow_external_processing=true가 있어야 한다 (FE 동의 체크). 없으면 422.
- AI로 보내는 것은 방금 재검증을 통과한 자격증명의 이름·발급 기관·발급일·내용·성취 기준과 희망 직무, 작성 방향 한 줄뿐이다.
  자격증명 ID는 짧은 ref(c1, c2, ...)로 바꿔 보내고 이름·이메일·DID·원본 토큰은 보내지 않는다.
- 한도 (넘으면 429 + Retry-After, AI를 부르지 않는다): 계정당 진행 중 1건, 계정당 24시간 PORTFOLIO_LLM_DAILY_LIMIT건(기본 20),
  모든 계정 합계 24시간 PORTFOLIO_LLM_GLOBAL_DAILY_LIMIT건(기본 200). 다시 쓰기는 같은 행을 고치므로 DB 행 수가 아니라
  프로세스 메모리의 시도 창(verifier_budget.llm_portfolio_*)으로 센다 (재시작하면 비워진다). 모델을 부르기 전에 끝난 시도는 돌려준다.
- AI가 쓴 문장이 검사를 두 번 통과하지 못하면(LLM_OUTPUT_REJECTED) 템플릿 본문으로 저장하고 generator="llm_fallback",
  경고 UNTRUSTED_MODEL_OUTPUT_DISCARDED를 남긴다. 공급자 장애는 503(+Retry-After) 또는 422로 끝나고 저장하지 않는다.
"""
import logging
import threading
from typing import Optional

from fastapi import HTTPException

from app.ai_contract.portfolio import PortfolioDraftRequest, PortfolioEvidence
from app.core.config import get_settings
from app.integrations.ai_service import AIRejected, AIServiceClient, AIUnavailable
from app.services.portfolio_writer import ordered_evidence
from app.services.verifier_budget import LLM_GLOBAL_KEY, llm_portfolio_attempts, llm_portfolio_global_attempts

audit = logging.getLogger("vc-be.audit")

LLM_NOT_ALLOWED = "External LLM access is not enabled for this account"
CONSENT_REQUIRED = "allow_external_processing must be true to write with the LLM"
DAILY_LIMIT_REACHED = "Daily LLM portfolio limit reached. Use the template mode or try again later."
SERVICE_LIMIT_REACHED = "Daily LLM portfolio service limit reached. Use the template mode or try again later."
IN_FLIGHT_REACHED = "An LLM portfolio is already being written for this account. Try again later."
IN_FLIGHT_RETRY_AFTER_SECONDS = 5
GENERATION_RETRY_AFTER_SECONDS = 5
# AI가 모델을 부르기 전에 끝난 실패: 이 요청의 시도 자리를 돌려준다.
NOT_SENT_CODES = frozenset({"LLM_BUSY", "LLM_CONTEXT_TOO_LARGE", "GENERATOR_CONFIGURATION_REQUIRED"})
FALLBACK_WARNING = "UNTRUSTED_MODEL_OUTPUT_DISCARDED"

_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()


def check_allowed(user_id: str, allow_external_processing: bool) -> None:
    if not get_settings().llm_allowed(user_id):
        raise HTTPException(status_code=403, detail=LLM_NOT_ALLOWED)
    if not allow_external_processing:
        raise HTTPException(status_code=422, detail=CONSENT_REQUIRED)


def _too_many(user_id: str, detail: str, retry_after: int, event: str) -> HTTPException:
    audit.info("event=%s user=%s route=portfolio_llm retry_after=%d", event, user_id, retry_after)
    return HTTPException(status_code=429, detail=detail, headers={"Retry-After": str(retry_after)})


def reserve(user_id: str) -> None:
    """진행 중 1건·계정 하루 몫·서비스 전체 하루 몫을 차례로 잡는다. 하나라도 모자라면 잡은 것을 돌려주고 429.
    통과하면 부른 쪽이 끝날 때 반드시 leave(user_id)를 부른다."""
    with _in_flight_lock:
        if user_id in _in_flight:
            busy = True
        else:
            _in_flight.add(user_id)
            busy = False
    if busy:
        audit.info("event=llm_in_flight_denied user=%s route=portfolio_llm", user_id)
        raise HTTPException(status_code=429, detail=IN_FLIGHT_REACHED,
                            headers={"Retry-After": str(IN_FLIGHT_RETRY_AFTER_SECONDS)})
    settings = get_settings()
    try:
        if not llm_portfolio_attempts.reserve(user_id, settings.portfolio_llm_daily_limit):
            raise _too_many(user_id, DAILY_LIMIT_REACHED, llm_portfolio_attempts.retry_after(user_id),
                            "daily_limit_denied")
        if not llm_portfolio_global_attempts.reserve(LLM_GLOBAL_KEY, settings.portfolio_llm_global_daily_limit):
            llm_portfolio_attempts.release(user_id)  # 서비스 전체 한도로 거절: 이 계정의 하루 몫은 쓰지 않는다
            raise _too_many(user_id, SERVICE_LIMIT_REACHED, llm_portfolio_global_attempts.retry_after(LLM_GLOBAL_KEY),
                            "llm_service_limit_denied")
    except BaseException:
        leave(user_id)
        raise


def release_attempt(user_id: str) -> None:
    """모델을 부르지 않고 끝난 시도를 돌려준다."""
    llm_portfolio_attempts.release(user_id)
    llm_portfolio_global_attempts.release(LLM_GLOBAL_KEY)


def leave(user_id: str) -> None:
    with _in_flight_lock:
        _in_flight.discard(user_id)


def clear_in_flight() -> None:
    """테스트용: conftest가 테스트마다 비운다."""
    with _in_flight_lock:
        _in_flight.clear()


def _date(value: Optional[str]) -> Optional[str]:
    return value[:10] if value else None


def draft_request(evidence: list[dict], target_role: Optional[str],
                  direction: Optional[str]) -> tuple[PortfolioDraftRequest, dict[str, str]]:
    """재검증 결과 → AI 요청과 ref → record_id 표. 본문과 같은 순서(최근 발급부터)로 c1, c2, ...를 붙인다."""
    refs, items = {}, []
    for index, e in enumerate(ordered_evidence(evidence), start=1):
        ref = f"c{index}"
        refs[ref] = e["record_id"]
        items.append(PortfolioEvidence(
            ref=ref,
            achievement_name=str(e["achievement_name"])[:300] or "-",
            issuer_name=str(e["issuer_name"])[:200] or "-",
            issued_on=_date(e.get("issued_at")),
            description=(str(e["achievement_description"])[:2000] if e.get("achievement_description") else None),
            criteria=(str(e["criteria"])[:2000] if e.get("criteria") else None),
        ))
    return PortfolioDraftRequest(target_role=target_role, direction=direction, evidence=items), refs


async def write(ai: AIServiceClient, user_id: str, evidence: list[dict], target_role: Optional[str],
                direction: Optional[str]) -> dict:
    """AI 초안을 받아 본문 작성기에 넘길 값으로 돌려준다. reserve()가 잡은 자리는 여기서 필요하면 돌려준다.

    성공: {"generator": "llm", "model", "summary", "highlights": {record_id: 문장}, "warnings": []}
    문장 검사 실패: {"generator": "llm_fallback", "model": None, "summary": None, "highlights": {}, "warnings": [...]}
    공급자 실패: AIUnavailable(503) / AIRejected(422)를 던진다 (저장하지 않는다).
    """
    body, refs = draft_request(evidence, target_role, direction)
    # 연결이 끊기는 등 결과를 모르면 시도로 센다 (학습 계획과 같은 기준). 응답을 받았으면 아래에서 판단한다.
    outcome = await ai.draft_portfolio(body)
    if outcome.state == "completed":
        draft = outcome.draft
        highlights = {refs[h.ref]: h.text for h in draft.highlights if h.ref in refs}
        return {"generator": "llm", "model": outcome.model, "summary": draft.summary,
                "highlights": highlights, "warnings": []}
    if outcome.error_code in NOT_SENT_CODES and not outcome.audit:
        release_attempt(user_id)
    if outcome.error_code == "LLM_OUTPUT_REJECTED":
        audit.info("event=llm_portfolio_output_discarded user=%s", user_id)
        return {"generator": "llm_fallback", "model": None, "summary": None, "highlights": {},
                "warnings": [FALLBACK_WARNING]}
    status = 503 if outcome.retryable or outcome.error_code.startswith("GENERATOR_") else 422
    error_class = AIUnavailable if status == 503 else AIRejected
    error = error_class(status, outcome.error_code, outcome.error_code, outcome.retryable)
    if outcome.retryable:
        error.headers = {"Retry-After": str(GENERATION_RETRY_AFTER_SECONDS)}
    raise error

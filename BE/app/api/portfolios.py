from typing import Annotated, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, require_user
from app.core.text import StorableText, ensure_single_line, ensure_storable
from app.integrations import get_verifier
from app.integrations.ai_service import AIServiceClient, get_ai_service
from app.integrations.ports import CredentialVerifier
from app.services.database import db_service
from app.services import portfolio_llm
from app.services.portfolio_writer import PortfolioWriter, get_portfolio_writer, rewrite_header
from app.services.reverify import raise_on_verifier_outage, reverify_rows

router = APIRouter()

# FE가 이 문구로 안내를 고른다 (FE/src/lib/apiClient.ts). 바꾸면 FE도 함께 바꾼다.
PORTFOLIO_LIMIT_REACHED = "Portfolio limit reached."

# 선택 입력용 문자열: StorableText와 같은 검사(NUL·짝 없는 서로게이트 → 500 대신 422)를 길이 제한 뒤에 붙인다.
# Optional[StorableText] + Field(max_length)로 쓰면 pydantic이 길이 검사를 검사기 뒤로 미뤄 422 메시지가 바뀐다.
OptionalTitle = Optional[Annotated[str, Field(min_length=1, max_length=255), AfterValidator(ensure_storable)]]
OptionalRole = Optional[Annotated[str, Field(max_length=100), AfterValidator(ensure_storable)]]
# AI(LLM) 모드의 작성 방향 한 줄 (말투·강조점). 본문에 그대로 넣지 않고 AI에 선호로만 전한다.
OptionalDirection = Optional[Annotated[str, Field(max_length=200), AfterValidator(ensure_storable)]]


def _clean_optional(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    return v or None


class PortfolioCreateRequest(BaseModel):
    # 정의되지 않은 필드(자유 텍스트 본문 등)는 422로 거절한다 (VC_ONLY, Security by Default).
    model_config = ConfigDict(extra="forbid")

    title: StorableText = Field(min_length=1, max_length=255)
    target_role: OptionalRole = None
    record_ids: list[UUID] = Field(min_length=1, max_length=50)
    source: Literal["VC_ONLY"]
    # template: 서버 틀만으로 본문 작성. llm: 같은 틀에 AI(Claude)가 쓴 요약·관련성 문장을 표시와 함께 넣는다
    # (허용 계정 + allow_external_processing=true 필요, app.services.portfolio_llm).
    mode: Literal["template", "llm"] = "template"
    allow_external_processing: bool = False
    direction: OptionalDirection = None

    @field_validator("direction")
    @classmethod
    def strip_direction(cls, v: Optional[str]) -> Optional[str]:
        return ensure_single_line(_clean_optional(v))

    # 제목·희망 직무는 본문 Markdown 머리말 한 줄에 그대로 들어간다. 앞뒤 공백은 지금처럼 잘라 내고, 안쪽의 줄바꿈·제어
    # 문자는 422로 거절한다 (줄을 나눠 '검증된 자격증명' 섹션을 흉내 내지 못하게: VC_ONLY).
    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return ensure_single_line(v)

    @field_validator("target_role")
    @classmethod
    def strip_role(cls, v: Optional[str]) -> Optional[str]:
        return ensure_single_line(_clean_optional(v))


class PortfolioRegenerateRequest(BaseModel):
    """다시 쓰기: 고른 자격증명을 지금 다시 검증해 본문·근거를 새로 만든다 (제목·희망 직무·상태는 그대로)."""
    model_config = ConfigDict(extra="forbid")

    record_ids: list[UUID] = Field(min_length=1, max_length=50)
    source: Literal["VC_ONLY"]
    mode: Literal["template", "llm"] = "template"
    allow_external_processing: bool = False
    direction: OptionalDirection = None

    @field_validator("direction")
    @classmethod
    def strip_direction(cls, v: Optional[str]) -> Optional[str]:
        return ensure_single_line(_clean_optional(v))


class PortfolioUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: OptionalTitle = None
    target_role: OptionalRole = None
    status: Optional[Literal["draft", "published"]] = None

    @field_validator("title", "target_role")
    @classmethod
    def check_single_line(cls, v: Optional[str]) -> Optional[str]:
        # 앞뒤 공백·빈 값·null 처리는 update_portfolio가 지금처럼 한다.
        # 여기서는 잘라 낸 값 안의 줄바꿈·제어 문자만 422로 거절하고 값은 그대로 돌려준다.
        if v is not None:
            ensure_single_line(v.strip())
        return v


def _summary(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "title": row["title"],
        "target_role": row["target_role"],
        "status": row["status"],
        "credential_count": len(row["referenced_record_ids"] or []),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _detail(row: dict) -> dict:
    sections = row["sections"] or {}
    return {
        **_summary(row),
        "source": sections.get("source", "VC_ONLY"),
        "generator": sections.get("generator", "template"),
        # AI(LLM) 모드: 실제로 쓴 모델과 경고(AI 문장을 버리고 템플릿으로 저장한 경우 UNTRUSTED_MODEL_OUTPUT_DISCARDED)
        "model": sections.get("model"),
        "warnings": sections.get("warnings", []),
        "content_markdown": row["content_markdown"],
        "sections": sections.get("evidence", []),
        "referenced_record_ids": [str(r) for r in row["referenced_record_ids"] or []],
        "excluded": sections.get("excluded", []),
    }


async def _write_body(*, principal: Principal, req, verifier: CredentialVerifier, writer: PortfolioWriter,
                      ai: AIServiceClient, title: str, target_role: Optional[str], route: str,
                      before_verify=None) -> dict:
    """고른 자격증명을 지금 다시 검증해 통과한 것만으로 본문을 만든다 (만들기·다시 쓰기 공용). 저장은 부른 쪽이 한다."""
    ids = sorted({str(r) for r in req.record_ids})
    rows = await run_in_threadpool(db_service.get_credential_rows, principal.user_id, ids)
    if len(rows) != len(ids):
        raise HTTPException(status_code=404, detail="Credential not found")
    llm = req.mode == "llm"
    if llm:
        portfolio_llm.check_allowed(principal.user_id, req.allow_external_processing)
    if before_verify is not None:
        await before_verify()
    if llm:
        portfolio_llm.reserve(principal.user_id)  # 한도면 429 (검증기·AI를 부르지 않는다)
    asked_ai = False
    try:
        # 생성 시점에 서명 원본을 다시 검증해 통과한 것만 근거로 쓴다.
        outcomes = await reverify_rows(rows, verifier, caller=principal.user_id, route=route)
        # 검증기 장애면 사용자 탓인 422 대신 502/503으로 끝내고 저장하지 않는다.
        raise_on_verifier_outage(outcomes)
        evidence = [o.evidence for o in outcomes if o.verified]
        excluded = [{"record_id": o.record_id, "status": o.status, "reason": o.reason}
                    for o in outcomes if not o.verified]
        if not evidence:
            raise HTTPException(status_code=422, detail="No verified credentials selected")
        ai_part = {"generator": writer.name, "model": None, "summary": None, "highlights": {}, "warnings": []}
        if llm:
            asked_ai = True
            ai_part = await portfolio_llm.write(ai, principal.user_id, evidence, target_role, req.direction)
        user = await run_in_threadpool(db_service.get_user_by_id, principal.user_id)
        content, bio = writer.write(holder_name=user["name"], title=title, target_role=target_role,
                                    evidence=evidence, ai_summary=ai_part["summary"],
                                    ai_highlights=ai_part["highlights"])
        sections = {"source": "VC_ONLY", "generator": ai_part["generator"], "evidence": evidence,
                    "excluded": excluded}
        if llm:
            sections.update(model=ai_part["model"], warnings=ai_part["warnings"])
        return {"content": content, "bio": bio, "sections": sections,
                "referenced": [e["record_id"] for e in evidence]}
    except BaseException:
        if llm and not asked_ai:
            portfolio_llm.release_attempt(principal.user_id)  # 모델을 부르기 전에 끝났다: 하루 몫을 쓰지 않는다
        raise
    finally:
        if llm:
            portfolio_llm.leave(principal.user_id)


@router.post("", status_code=201)
async def create_portfolio(
    req: PortfolioCreateRequest,
    principal: Principal = Depends(require_user),
    verifier: CredentialVerifier = Depends(get_verifier),
    writer: PortfolioWriter = Depends(get_portfolio_writer),
    ai: AIServiceClient = Depends(get_ai_service),
):
    async def limit_check():
        # 이미 상한까지 만든 사용자는 검증기(DXWorks)·AI를 부르기 전에 돌려보낸다. 동시 요청까지 막는 검사는 저장할 때 한다.
        if await run_in_threadpool(db_service.portfolio_limit_reached, principal.user_id):
            raise HTTPException(status_code=409, detail=PORTFOLIO_LIMIT_REACHED)

    body = await _write_body(principal=principal, req=req, verifier=verifier, writer=writer, ai=ai,
                             title=req.title, target_role=req.target_role, route="portfolio_create",
                             before_verify=limit_check)
    row = await run_in_threadpool(
        db_service.create_portfolio,
        user_id=principal.user_id, title=req.title, target_role=req.target_role, bio_summary=body["bio"],
        content_markdown=body["content"], sections=body["sections"], referenced_record_ids=body["referenced"],
    )
    if row is None:
        # 사용자별 상한(database.MAX_PORTFOLIOS_PER_USER)에 닿았다 (위 검사 뒤에 동시 요청이 먼저 채운 경우 포함).
        raise HTTPException(status_code=409, detail=PORTFOLIO_LIMIT_REACHED)
    return {"success": True, "message": "Portfolio created.", "data": _detail(row)}


@router.post("/{portfolio_id}/regenerate")
async def regenerate_portfolio(
    portfolio_id: UUID,
    req: PortfolioRegenerateRequest,
    principal: Principal = Depends(require_user),
    verifier: CredentialVerifier = Depends(get_verifier),
    writer: PortfolioWriter = Depends(get_portfolio_writer),
    ai: AIServiceClient = Depends(get_ai_service),
):
    """대화창의 "다시 써 줘": 같은 포트폴리오의 본문·근거를 새로 만든다 (제목·희망 직무·상태·공유는 그대로)."""
    current = await run_in_threadpool(db_service.get_portfolio, principal.user_id, str(portfolio_id))
    if not current:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    body = await _write_body(principal=principal, req=req, verifier=verifier, writer=writer, ai=ai,
                             title=current["title"], target_role=current["target_role"],
                             route="portfolio_regenerate")
    row = await run_in_threadpool(
        db_service.replace_portfolio_body, principal.user_id, str(portfolio_id), bio_summary=body["bio"],
        content_markdown=body["content"], sections=body["sections"], referenced_record_ids=body["referenced"],
    )
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"success": True, "message": "Portfolio rewritten.", "data": _detail(row)}


@router.get("")
def list_portfolios(principal: Principal = Depends(require_user)):
    return {"success": True, "data": [_summary(r) for r in db_service.list_portfolios(principal.user_id)]}


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: UUID, principal: Principal = Depends(require_user)):
    row = db_service.get_portfolio(principal.user_id, str(portfolio_id))
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"success": True, "data": _detail(row)}


@router.patch("/{portfolio_id}")
def update_portfolio(portfolio_id: UUID, req: PortfolioUpdateRequest, principal: Principal = Depends(require_user)):
    fields = req.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=422, detail="Nothing to update")
    if "title" in fields:
        if fields["title"] is None or not fields["title"].strip():
            raise HTTPException(status_code=422, detail="Title must not be blank")
        fields["title"] = fields["title"].strip()
    if "target_role" in fields:
        fields["target_role"] = _clean_optional(fields["target_role"])
    if "status" in fields and fields["status"] is None:
        raise HTTPException(status_code=422, detail="Status must not be null")
    if "title" in fields or "target_role" in fields:
        # 본문 첫 줄 제목·희망 직무 줄도 함께 맞춘다 (근거 섹션은 그대로).
        current = db_service.get_portfolio(principal.user_id, str(portfolio_id))
        if not current:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        fields["content_markdown"] = rewrite_header(
            current["content_markdown"],
            title=fields.get("title", current["title"]),
            target_role=fields.get("target_role", current["target_role"]),
        )
    row = db_service.update_portfolio(principal.user_id, str(portfolio_id), fields)
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"success": True, "message": "Portfolio updated.", "data": _detail(row)}


@router.delete("/{portfolio_id}")
def delete_portfolio(portfolio_id: UUID, principal: Principal = Depends(require_user)):
    if not db_service.delete_portfolio(principal.user_id, str(portfolio_id)):
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"success": True, "message": "Portfolio deleted."}

from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.core.auth import Principal, require_user
from app.integrations import get_verifier
from app.integrations.ports import CredentialVerifier
from app.services.database import db_service
from app.services.portfolio_writer import PortfolioWriter, get_portfolio_writer
from app.services.reverify import reverify_rows

router = APIRouter()


def _clean_optional(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    return v or None


class PortfolioCreateRequest(BaseModel):
    # 정의되지 않은 필드(자유 텍스트 본문 등)는 422로 거절한다 (VC_ONLY, Security by Default).
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    target_role: Optional[str] = Field(default=None, max_length=100)
    record_ids: list[UUID] = Field(min_length=1, max_length=50)
    source: Literal["VC_ONLY"]

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @field_validator("target_role")
    @classmethod
    def strip_role(cls, v: Optional[str]) -> Optional[str]:
        return _clean_optional(v)


class PortfolioUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    target_role: Optional[str] = Field(default=None, max_length=100)
    status: Optional[Literal["draft", "published"]] = None


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
        "content_markdown": row["content_markdown"],
        "sections": sections.get("evidence", []),
        "referenced_record_ids": [str(r) for r in row["referenced_record_ids"] or []],
        "excluded": sections.get("excluded", []),
    }


@router.post("", status_code=201)
async def create_portfolio(
    req: PortfolioCreateRequest,
    principal: Principal = Depends(require_user),
    verifier: CredentialVerifier = Depends(get_verifier),
    writer: PortfolioWriter = Depends(get_portfolio_writer),
):
    ids = sorted({str(r) for r in req.record_ids})
    rows = await run_in_threadpool(db_service.get_credential_rows, principal.user_id, ids)
    if len(rows) != len(ids):
        raise HTTPException(status_code=404, detail="Credential not found")

    # 생성 시점에 서명 원본을 다시 검증해 통과한 것만 근거로 쓴다.
    outcomes = await reverify_rows(rows, verifier)
    evidence = [o.evidence for o in outcomes if o.verified]
    excluded = [{"record_id": o.record_id, "status": o.status, "reason": o.reason}
                for o in outcomes if not o.verified]
    if not evidence:
        raise HTTPException(status_code=422, detail="No verified credentials selected")

    user = await run_in_threadpool(db_service.get_user_by_id, principal.user_id)
    content, bio = writer.write(holder_name=user["name"], title=req.title, target_role=req.target_role,
                                evidence=evidence)
    row = await run_in_threadpool(
        db_service.create_portfolio,
        user_id=principal.user_id, title=req.title, target_role=req.target_role, bio_summary=bio,
        content_markdown=content,
        sections={"source": "VC_ONLY", "generator": writer.name, "evidence": evidence, "excluded": excluded},
        referenced_record_ids=[e["record_id"] for e in evidence],
    )
    return {"success": True, "message": "Portfolio created.", "data": _detail(row)}


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
    row = db_service.update_portfolio(principal.user_id, str(portfolio_id), fields)
    if not row:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"success": True, "message": "Portfolio updated.", "data": _detail(row)}


@router.delete("/{portfolio_id}")
def delete_portfolio(portfolio_id: UUID, principal: Principal = Depends(require_user)):
    if not db_service.delete_portfolio(principal.user_id, str(portfolio_id)):
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return {"success": True, "message": "Portfolio deleted."}

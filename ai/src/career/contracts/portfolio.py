"""BE -> AI portfolio drafting (POST /v1/portfolios/draft).

BE sends only credentials it re-verified a moment ago, under short local refs (c1, c2, ...): no record
IDs, holder name, e-mail, DID or raw tokens. The model writes a short introduction and one sentence per
credential; BE renders the verified facts (names, issuers, dates, verification) itself and only places
these sentences next to them.
"""

from datetime import date
from typing import Any, Literal

from pydantic import Field, model_validator

from .verification import EvidenceModel

REF_PATTERN = r"^c[1-9][0-9]?$"
MAX_PORTFOLIO_EVIDENCE = 50


class PortfolioEvidence(EvidenceModel):
    ref: str = Field(pattern=REF_PATTERN)
    achievement_name: str = Field(min_length=1, max_length=300)
    issuer_name: str = Field(min_length=1, max_length=200)
    issued_on: date | None = None
    description: str | None = Field(default=None, max_length=2000)
    criteria: str | None = Field(default=None, max_length=2000)


class PortfolioDraftRequest(EvidenceModel):
    target_role: str | None = Field(default=None, max_length=100)
    # The holder's wish for tone and emphasis (single line). A preference only: never a source of facts.
    direction: str | None = Field(default=None, max_length=200)
    evidence: list[PortfolioEvidence] = Field(min_length=1, max_length=MAX_PORTFOLIO_EVIDENCE)

    @model_validator(mode="after")
    def unique_refs(self):
        refs = [item.ref for item in self.evidence]
        if len(refs) != len(set(refs)):
            raise ValueError("Evidence refs must be unique")
        return self


class PortfolioHighlight(EvidenceModel):
    ref: str = Field(pattern=REF_PATTERN)
    text: str = Field(min_length=1, max_length=200)


class PortfolioDraft(EvidenceModel):
    summary: str = Field(min_length=1, max_length=600)
    highlights: list[PortfolioHighlight] = Field(default_factory=list, max_length=MAX_PORTFOLIO_EVIDENCE)


class PortfolioDraftOutcome(EvidenceModel):
    state: Literal["completed", "failed"]
    draft: PortfolioDraft | None = None
    # Model that wrote the draft (may differ from the configured one after a server-side refusal fallback).
    model: str | None = None
    error_code: str | None = None
    retryable: bool = False
    # Per-call audit: usage, validation_errors, provider_error. Never the model's text or the request.
    audit: list[dict[str, Any]] = Field(default_factory=list, repr=False)

    @model_validator(mode="after")
    def consistent(self):
        if (self.state == "completed") != (self.draft is not None):
            raise ValueError("Completed outcomes carry a draft; failures do not")
        if self.state == "failed" and not self.error_code:
            raise ValueError("Failed outcomes carry an error code")
        return self

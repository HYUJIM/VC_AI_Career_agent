from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# BE re-verifies every signed original with its own DXWorks adapter at request time and
# sends only the outcome. The AI never parses VC originals or provider responses.
POLICY_VERSION = "be-runtime-reverify/1"


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NormalizedCredential(EvidenceModel):
    external_id: str
    issuer_id: str
    issuer_name: str
    subject_did: str
    achievement_name: str
    achievement: dict[str, Any]
    issued_at: datetime
    valid_from: datetime
    expires_at: datetime | None = None


class VerificationCheck(EvidenceModel):
    name: str
    result: Literal["pass", "fail", "warn", "skip"]
    code: str


class CredentialOutcome(EvidenceModel):
    record_id: UUID
    status: Literal["verified", "invalid", "revoked", "expired", "unverifiable"]
    accepted: bool = False
    checks: list[VerificationCheck]
    credential: NormalizedCredential | None = None


class VerificationSnapshot(EvidenceModel):
    verification_snapshot_id: UUID
    subject_did: str
    scope: Literal["all", "selected"] = "all"
    as_of: datetime
    valid_until: datetime
    policy_version: str = POLICY_VERSION
    accepted_credentials: list[CredentialOutcome]
    excluded_credentials: list[CredentialOutcome]

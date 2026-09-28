import { v5 as uuidv5 } from "uuid";
import { computeRawHash } from "../crypto/canonical.js";
import { mapAlignmentsToSkillClaims } from "./skillMapper.js";
import type { CredentialProvider, CredentialRecord, IssuerTrustLevel, ParsedCredential } from "../types/credential.js";

/** 프로젝트 고정 네임스페이스. 바꾸면 기존 fixture들의 record_id가 전부 달라지므로 변경 금지. */
const RECORD_ID_NAMESPACE = "6f6a9c1e-8b1a-4d9a-9b0a-4e2f9b6c0a11";

export type CredentialRecordDraft = Omit<CredentialRecord, "verification">;

/**
 * externalId로부터 결정적(deterministic) UUID를 만든다.
 * 서버를 재시작해도, 다른 팀원의 로컬 환경에서도 같은 fixture는 항상 같은 record_id를 가져야
 * AI/BE 팀이 record_id를 안정적인 키로 쓸 수 있다.
 */
export function deriveRecordId(externalId: string): string {
  return uuidv5(externalId, RECORD_ID_NAMESPACE);
}

export function normalizeCredential(
  parsed: ParsedCredential,
  provider: CredentialProvider,
  issuerTrustLevel: IssuerTrustLevel
): CredentialRecordDraft {
  const recordId = deriveRecordId(parsed.externalId);
  const skills = mapAlignmentsToSkillClaims(parsed.alignments, recordId);
  const now = new Date().toISOString();

  return {
    record_id: recordId,
    subject_did: parsed.subjectDid,
    schema_version: "0.1.0",
    normalized_at: now,
    source: {
      provider,
      external_id: parsed.externalId,
      fetched_at: now
    },
    format: parsed.format,
    issuer: {
      did: parsed.issuerDid,
      name: parsed.issuerName,
      url: parsed.issuerUrl,
      trust_level: issuerTrustLevel
    },
    achievement: parsed.achievement,
    evidence: parsed.evidence,
    issued_at: parsed.issuedAt,
    expires_at: parsed.expiresAt,
    awarded_date: parsed.awardedDate,
    skills,
    raw_hash: computeRawHash(parsed.rawDocument)
  };
}

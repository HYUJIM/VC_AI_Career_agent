import type { RawCredentialRef } from "../adapters/ports.js";
import { decodeCompactJws } from "../crypto/jws.js";
import type { Alignment, Evidence, ParsedCredential, ProofEnvelope } from "../types/credential.js";

interface Ob2AlignmentRaw {
  targetName?: string;
  targetUrl?: string;
  targetFramework?: string;
  targetCode?: string;
}

interface Ob2EvidenceRaw {
  id?: string;
  name?: string;
  description?: string;
}

interface Ob2Assertion {
  id: string;
  type: "Assertion";
  /**
   * 실제 OB2.0 스펙에서는 recipient.identity가 보통 해시된 이메일이라 DID로 바로 못 쓴다.
   * 이 프로젝트는 fixture 단계에서 recipient.identity에 곧바로 subject DID 문자열을 넣어
   * "신원 해석" 문제를 단순화했다. 실연동 시에는 이메일-DID 매핑 테이블이 별도로 필요하다.
   */
  recipient: { identity: string; type?: string };
  badge: {
    id: string;
    name: string;
    description?: string;
    criteria?: { narrative?: string };
    issuer: { id: string; name: string; url?: string };
    alignment?: Ob2AlignmentRaw[];
  };
  issuedOn: string;
  expires?: string | null;
  evidence?: Ob2EvidenceRaw[];
}

function toAlignments(raw?: Ob2AlignmentRaw[]): Alignment[] {
  if (!raw) return [];
  return raw.map((a) => ({ framework: a.targetFramework, code: a.targetCode, name: a.targetName, url: a.targetUrl }));
}

function toEvidence(raw?: Ob2EvidenceRaw[]): Evidence[] {
  if (!raw) return [];
  return raw.map((e) => ({ name: e.name, description: e.description, url: e.id }));
}

export function parseOb2(ref: RawCredentialRef): ParsedCredential {
  let assertion: Ob2Assertion;
  let proofEnvelope: ProofEnvelope;
  let rawDocument: Record<string, unknown>;

  if (ref.compactJws) {
    const decoded = decodeCompactJws<Ob2Assertion>(ref.compactJws);
    assertion = decoded.payload;
    proofEnvelope = { kind: "compact-jws", jws: ref.compactJws };
    rawDocument = decoded.payload as unknown as Record<string, unknown>;
  } else if (ref.document) {
    assertion = ref.document as unknown as Ob2Assertion;
    proofEnvelope = { kind: "hosted", document: ref.document };
    rawDocument = ref.document;
  } else {
    throw new Error("OB2.0 파서: document/compactJws가 모두 없습니다");
  }

  const alignments = toAlignments(assertion.badge.alignment);

  return {
    format: "OB2.0",
    externalId: ref.externalId,
    subjectDid: assertion.recipient.identity,
    issuerDid: assertion.badge.issuer.id,
    issuerName: assertion.badge.issuer.name,
    issuerUrl: assertion.badge.issuer.url,
    achievement: {
      id: assertion.badge.id,
      name: assertion.badge.name,
      description: assertion.badge.description,
      criteria_narrative: assertion.badge.criteria?.narrative,
      achievement_type: "Badge",
      alignments
    },
    evidence: toEvidence(assertion.evidence),
    issuedAt: assertion.issuedOn,
    expiresAt: assertion.expires ?? null,
    awardedDate: assertion.issuedOn,
    alignments,
    proofEnvelope,
    rawDocument
  };
}

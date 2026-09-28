import type { RawCredentialRef } from "../adapters/ports.js";
import { decodeCompactJws } from "../crypto/jws.js";
import type { Alignment, Evidence, ParsedCredential, ProofEnvelope } from "../types/credential.js";

interface Ob3AlignmentRaw {
  targetName?: string;
  targetUrl?: string;
  targetFramework?: string;
  targetCode?: string;
}

interface Ob3EvidenceRaw {
  id?: string;
  name?: string;
  description?: string;
}

interface Ob3Credential {
  "@context": unknown;
  id: string;
  type: string[];
  issuer: { id: string; name: string; url?: string; type?: string };
  issuanceDate: string;
  expirationDate?: string | null;
  credentialSubject: {
    id: string;
    type?: string;
    achievement: {
      id: string;
      name: string;
      description?: string;
      criteria?: { narrative?: string };
      alignment?: Ob3AlignmentRaw[];
    };
  };
  evidence?: Ob3EvidenceRaw[];
  proof?: unknown;
}

function toAlignments(raw?: Ob3AlignmentRaw[]): Alignment[] {
  if (!raw) return [];
  return raw.map((a) => ({ framework: a.targetFramework, code: a.targetCode, name: a.targetName, url: a.targetUrl }));
}

function toEvidence(raw?: Ob3EvidenceRaw[]): Evidence[] {
  if (!raw) return [];
  return raw.map((e) => ({ name: e.name, description: e.description, url: e.id }));
}

export function parseOb3(ref: RawCredentialRef): ParsedCredential {
  let credential: Ob3Credential;
  let proofEnvelope: ProofEnvelope;
  let rawDocument: Record<string, unknown>;

  if (ref.compactJws) {
    const decoded = decodeCompactJws<Ob3Credential>(ref.compactJws);
    credential = decoded.payload;
    proofEnvelope = { kind: "compact-jws", jws: ref.compactJws };
    rawDocument = decoded.payload as unknown as Record<string, unknown>;
  } else if (ref.document) {
    credential = ref.document as unknown as Ob3Credential;
    proofEnvelope = { kind: "data-integrity", document: ref.document };
    // raw_hash는 proof를 제외한 내용 기준으로 계산한다 (동일 내용이면 재서명해도 raw_hash가 유지되도록).
    const { proof: _proof, ...withoutProof } = ref.document;
    rawDocument = withoutProof;
  } else {
    throw new Error("OB3.0 파서: document/compactJws가 모두 없습니다");
  }

  const alignments = toAlignments(credential.credentialSubject.achievement.alignment);

  return {
    format: "OB3.0",
    externalId: ref.externalId,
    subjectDid: credential.credentialSubject.id,
    issuerDid: credential.issuer.id,
    issuerName: credential.issuer.name,
    issuerUrl: credential.issuer.url,
    achievement: {
      id: credential.credentialSubject.achievement.id,
      name: credential.credentialSubject.achievement.name,
      description: credential.credentialSubject.achievement.description,
      criteria_narrative: credential.credentialSubject.achievement.criteria?.narrative,
      achievement_type: "Achievement",
      alignments
    },
    evidence: toEvidence(credential.evidence),
    issuedAt: credential.issuanceDate,
    expiresAt: credential.expirationDate ?? null,
    awardedDate: credential.issuanceDate,
    alignments,
    proofEnvelope,
    rawDocument
  };
}

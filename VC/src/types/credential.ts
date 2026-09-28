/**
 * 지갑 코어 전체가 공유하는 정규화 도메인 타입.
 * /src/schemas/*.schema.json 과 1:1로 대응한다. 필드를 바꿀 때는 반드시 두 곳을 함께 수정할 것.
 */

export type CredentialFormat = "OB2.0" | "OB3.0" | "VC1.1" | "VC2.0";

export type CredentialProvider = "dx_ledger_wallet" | "dx_ledger_badge" | "hosted_url" | "upload";

export type IssuerTrustLevel = "accredited" | "known" | "unknown";

export type VerificationStatus = "verified" | "invalid" | "revoked" | "expired" | "unverifiable" | "pending";

export type VerificationCheckName =
  | "schema"
  | "did_resolution"
  | "signature"
  | "issuer_trust"
  | "revocation"
  | "anchor_match"
  | "expiration";

export type VerificationCheckResult = "pass" | "fail" | "warn" | "skip";

export type ProofType = "VC-JWT" | "DataIntegrityProof" | "HostedVerification";

export type SkillMappingMethod = "exact_alignment" | "alias_dict" | "embedding_knn" | "manual";

export type SkillLevelBasis = "assessed" | "completion" | "participation" | "declared";

export interface Source {
  provider: CredentialProvider;
  external_id: string;
  fetched_at: string;
}

export interface IssuerProfile {
  did: string;
  name: string;
  url?: string;
  trust_level: IssuerTrustLevel;
}

export interface Alignment {
  framework?: string;
  code?: string;
  name?: string;
  url?: string;
}

export interface Achievement {
  id: string;
  name: string;
  description?: string;
  criteria_narrative?: string;
  achievement_type?: string;
  image_url?: string;
  tags?: string[];
  alignments?: Alignment[];
}

export interface Evidence {
  name?: string;
  description?: string;
  url?: string;
}

export interface SkillTaxonomyRef {
  source: "NCS" | "ESCO" | "internal";
  code: string;
  version: string;
}

export interface SkillClaim {
  skill_id: string;
  label_ko: string;
  label_en?: string;
  taxonomy: SkillTaxonomyRef;
  level: number | null;
  level_basis?: SkillLevelBasis;
  confidence: number;
  mapping_method: SkillMappingMethod;
  source_record_ids: string[];
}

export interface VerificationCheck {
  check: VerificationCheckName;
  result: VerificationCheckResult;
  detail?: string;
  checked_at: string;
}

export interface Proof {
  type: ProofType;
  alg?: string;
  verification_method?: string;
}

export interface Anchor {
  chain?: string | null;
  tx_id?: string | null;
  block_no?: number | null;
  anchored_hash?: string | null;
  computed_hash?: string | null;
  match?: boolean | null;
}

export interface VerificationResult {
  status: VerificationStatus;
  checks: VerificationCheck[];
  proof?: Proof;
  anchor?: Anchor;
  verified_at: string;
  verifier_version: string;
}

export interface CredentialRecord {
  record_id: string;
  subject_did: string;
  schema_version: string;
  normalized_at: string;
  source: Source;
  format: CredentialFormat;
  issuer: IssuerProfile;
  achievement: Achievement;
  evidence: Evidence[];
  issued_at: string;
  expires_at: string | null;
  awarded_date: string | null;
  skills: SkillClaim[];
  verification: VerificationResult;
  raw_hash: string;
}

/** 파서가 원본 자격증명에서 뽑아내는, 아직 검증되지 않은 중간 산출물 */
export interface ParsedCredential {
  format: CredentialFormat;
  externalId: string;
  subjectDid: string;
  issuerDid: string;
  issuerName: string;
  issuerUrl?: string;
  achievement: Achievement;
  evidence: Evidence[];
  issuedAt: string;
  expiresAt: string | null;
  awardedDate: string | null;
  alignments: Alignment[];
  /** 서명 검증에 필요한 원문 표현 (compact JWS 문자열 또는 Data Integrity 첨부 JSON-LD 문서) */
  proofEnvelope: ProofEnvelope;
  /** raw_hash 계산 대상이 되는, 파서가 그대로 보존한 원본 JSON */
  rawDocument: Record<string, unknown>;
}

export type ProofEnvelope =
  | { kind: "hosted"; document: Record<string, unknown> }
  | { kind: "compact-jws"; jws: string }
  | { kind: "data-integrity"; document: Record<string, unknown> };

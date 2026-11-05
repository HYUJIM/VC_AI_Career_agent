import type { AnchorVerifierPort, IssuerTrustPort, RawCredentialRef, RevocationCheckerPort } from "../adapters/ports.js";
import { normalizeCredential } from "../normalize/normalizeCredential.js";
import { parseCredential } from "../parsers/parseCredential.js";
import type {
  CredentialProvider,
  CredentialRecord,
  ParsedCredential,
  Proof,
  VerificationCheck,
  VerificationCheckName,
  VerificationResult,
  VerificationStatus
} from "../types/credential.js";
import { checkAnchor, checkDidResolution, checkExpiration, checkIssuerTrust, checkRevocation, checkSchema, checkSignature } from "./checks.js";
import { decodeCompactJws } from "../crypto/jws.js";

const VERIFIER_VERSION = "vc-core-mock/0.1.0";

export interface VerifyPipelineDeps {
  issuerTrust: IssuerTrustPort;
  revocationChecker: RevocationCheckerPort;
  anchorVerifier: AnchorVerifierPort;
}

function findCheck(checks: VerificationCheck[], name: VerificationCheckName): VerificationCheck | undefined {
  return checks.find((c) => c.check === name);
}

/**
 * 7개 체크 결과로부터 최종 status를 도출한다.
 * 우선순위: invalid > revoked > expired > unverifiable > verified.
 * (모든 체크는 항상 전부 실행되어 checks[]에 기록되며, 이 함수는 그중 "가장 심각한" 실패 하나를 고른다.)
 */
export function deriveStatus(checks: VerificationCheck[]): VerificationStatus {
  if (findCheck(checks, "schema")?.result === "fail") return "invalid";
  if (findCheck(checks, "signature")?.result === "fail") return "invalid";
  if (findCheck(checks, "revocation")?.result === "fail") return "revoked";
  if (findCheck(checks, "expiration")?.result === "fail") return "expired";
  if (findCheck(checks, "did_resolution")?.result === "fail") return "unverifiable";
  if (findCheck(checks, "issuer_trust")?.result === "fail") return "unverifiable";
  return "verified";
}

function buildProofMeta(parsed: ParsedCredential): Proof {
  const envelope = parsed.proofEnvelope;
  if (envelope.kind === "hosted") {
    return { type: "HostedVerification" };
  }
  if (envelope.kind === "compact-jws") {
    const { header } = decodeCompactJws(envelope.jws);
    return { type: "VC-JWT", alg: header.alg, verification_method: header.kid };
  }
  const document = envelope.document as { proof?: { cryptosuite?: string; verificationMethod?: string } };
  return {
    type: "DataIntegrityProof",
    alg: document.proof?.cryptosuite,
    verification_method: document.proof?.verificationMethod
  };
}

export async function verifyAndNormalize(
  ref: RawCredentialRef,
  provider: CredentialProvider,
  deps: VerifyPipelineDeps
): Promise<CredentialRecord> {
  const parsed = parseCredential(ref);

  const schemaCheckResult = checkSchema(parsed.format, parsed.rawDocument);
  const { check: didCheckResult, didDocument } = await checkDidResolution(parsed.issuerDid);
  const signatureCheckResult = await checkSignature(parsed, didDocument);
  const trustLevel = await deps.issuerTrust.getTrustLevel(parsed.issuerDid);
  const issuerTrustCheckResult = checkIssuerTrust(trustLevel);
  const revocationCheckResult = await checkRevocation(parsed.externalId, deps.revocationChecker);
  const expirationCheckResult = checkExpiration(parsed.expiresAt);

  const draft = normalizeCredential(parsed, provider, trustLevel);
  const anchorCheckResult = await checkAnchor(draft.raw_hash, deps.anchorVerifier);

  const checks: VerificationCheck[] = [
    schemaCheckResult,
    didCheckResult,
    signatureCheckResult,
    issuerTrustCheckResult,
    revocationCheckResult,
    anchorCheckResult,
    expirationCheckResult
  ];

  const verification: VerificationResult = {
    status: deriveStatus(checks),
    checks,
    proof: buildProofMeta(parsed),
    anchor: {
      chain: null,
      tx_id: null,
      block_no: null,
      anchored_hash: null,
      computed_hash: draft.raw_hash,
      match: null
    },
    verified_at: new Date().toISOString(),
    verifier_version: VERIFIER_VERSION
  };

  return { ...draft, verification };
}

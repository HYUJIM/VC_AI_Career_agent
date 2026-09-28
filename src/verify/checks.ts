import type { DIDDocument } from "did-resolver";
import type { AnchorVerifierPort, RevocationCheckerPort } from "../adapters/ports.js";
import { verifyDataIntegrityProof } from "../crypto/dataIntegrity.js";
import { decodeCompactJws, verifyCompactJwsSignature } from "../crypto/jws.js";
import { didResolver } from "../resolver/resolver.js";
import type { CredentialFormat, IssuerTrustLevel, ParsedCredential, VerificationCheck } from "../types/credential.js";
import { formatAjvErrors, validateOb2, validateOb3 } from "./ajvSchemas.js";
import { findVerificationMethod, publicKeyFromVerificationMethod } from "./didKeyLookup.js";

function nowIso(): string {
  return new Date().toISOString();
}

/** 1. schema: 원본 문서가 OB2.0/OB3.0 최소 스키마를 만족하는지 */
export function checkSchema(format: CredentialFormat, rawDocument: Record<string, unknown>): VerificationCheck {
  const validator = format === "OB2.0" ? validateOb2 : validateOb3;
  const valid = validator(rawDocument);
  return {
    check: "schema",
    result: valid ? "pass" : "fail",
    detail: valid ? undefined : formatAjvErrors(validator.errors),
    checked_at: nowIso()
  };
}

/** 2. did_resolution: 발급자 DID Document를 조회할 수 있는지 (did-resolver 사용) */
export async function checkDidResolution(
  issuerDid: string
): Promise<{ check: VerificationCheck; didDocument: DIDDocument | null }> {
  try {
    const result = await didResolver.resolve(issuerDid);
    if (!result.didDocument) {
      return {
        check: {
          check: "did_resolution",
          result: "fail",
          detail: result.didResolutionMetadata.message ?? `DID Document를 찾을 수 없음: ${issuerDid}`,
          checked_at: nowIso()
        },
        didDocument: null
      };
    }
    return {
      check: { check: "did_resolution", result: "pass", checked_at: nowIso() },
      didDocument: result.didDocument
    };
  } catch (err) {
    return {
      check: {
        check: "did_resolution",
        result: "fail",
        detail: `DID 조회 중 오류: ${(err as Error).message}`,
        checked_at: nowIso()
      },
      didDocument: null
    };
  }
}

/** 3. signature: proofEnvelope 종류에 따라 compact JWS 또는 Data Integrity 서명을 검증 */
export async function checkSignature(parsed: ParsedCredential, didDocument: DIDDocument | null): Promise<VerificationCheck> {
  const envelope = parsed.proofEnvelope;

  if (envelope.kind === "hosted") {
    return {
      check: "signature",
      result: "skip",
      detail: "hosted assertion: 내장 서명이 없고 호스팅 URL 신뢰에 의존함",
      checked_at: nowIso()
    };
  }

  if (!didDocument) {
    return {
      check: "signature",
      result: "fail",
      detail: "발급자 DID Document를 확인할 수 없어 서명 검증 불가",
      checked_at: nowIso()
    };
  }

  try {
    if (envelope.kind === "compact-jws") {
      const { header } = decodeCompactJws(envelope.jws);
      const vm = findVerificationMethod(didDocument, header.kid);
      if (!vm) {
        return {
          check: "signature",
          result: "fail",
          detail: `kid(${header.kid})에 해당하는 verificationMethod를 찾을 수 없음`,
          checked_at: nowIso()
        };
      }
      const publicKey = publicKeyFromVerificationMethod(vm);
      const valid = verifyCompactJwsSignature(envelope.jws, publicKey);
      return {
        check: "signature",
        result: valid ? "pass" : "fail",
        detail: valid ? undefined : "서명이 일치하지 않음 (위조·변조 의심)",
        checked_at: nowIso()
      };
    }

    // data-integrity
    const document = envelope.document as { proof?: { verificationMethod?: string } };
    const verificationMethodId = document.proof?.verificationMethod;
    if (!verificationMethodId) {
      return { check: "signature", result: "fail", detail: "proof.verificationMethod가 없음", checked_at: nowIso() };
    }
    const vm = findVerificationMethod(didDocument, verificationMethodId);
    if (!vm) {
      return {
        check: "signature",
        result: "fail",
        detail: `verificationMethod(${verificationMethodId})를 DID Document에서 찾을 수 없음`,
        checked_at: nowIso()
      };
    }
    const publicKey = publicKeyFromVerificationMethod(vm);
    const valid = await verifyDataIntegrityProof(envelope.document, publicKey);
    return {
      check: "signature",
      result: valid ? "pass" : "fail",
      detail: valid ? undefined : "Data Integrity 서명이 일치하지 않음 (위조·변조 의심)",
      checked_at: nowIso()
    };
  } catch (err) {
    return {
      check: "signature",
      result: "fail",
      detail: `서명 검증 중 오류: ${(err as Error).message}`,
      checked_at: nowIso()
    };
  }
}

/** 4. issuer_trust: 발급자가 신뢰 레지스트리에 등록되어 있는지 (accredited/known만 통과) */
export function checkIssuerTrust(trustLevel: IssuerTrustLevel): VerificationCheck {
  const pass = trustLevel === "accredited" || trustLevel === "known";
  return {
    check: "issuer_trust",
    result: pass ? "pass" : "fail",
    detail: pass ? undefined : `발급자가 신뢰 레지스트리에 없음 (trust_level=${trustLevel})`,
    checked_at: nowIso()
  };
}

/** 5. revocation: 폐기 목록에 올라 있는지 */
export async function checkRevocation(
  externalId: string,
  revocationChecker: RevocationCheckerPort
): Promise<VerificationCheck> {
  const revoked = await revocationChecker.isRevoked(externalId);
  return {
    check: "revocation",
    result: revoked ? "fail" : "pass",
    detail: revoked ? "폐기 목록에 등록된 자격증명" : undefined,
    checked_at: nowIso()
  };
}

/** 6. anchor_match: 원장 앵커 해시 대조 (DX Ledger Builder 미연동 상태에서는 항상 skip) */
export async function checkAnchor(rawHash: string, anchorVerifier: AnchorVerifierPort): Promise<VerificationCheck> {
  const result = await anchorVerifier.checkAnchor(rawHash);
  return {
    check: "anchor_match",
    result: result.checked ? (result.match ? "pass" : "fail") : "skip",
    detail: result.detail,
    checked_at: nowIso()
  };
}

/** 7. expiration: 만료일이 지났는지 */
export function checkExpiration(expiresAt: string | null): VerificationCheck {
  if (!expiresAt) {
    return { check: "expiration", result: "pass", detail: "만료일 없음", checked_at: nowIso() };
  }
  const expired = new Date(expiresAt).getTime() < Date.now();
  return {
    check: "expiration",
    result: expired ? "fail" : "pass",
    detail: expired ? `만료일(${expiresAt})이 지남` : undefined,
    checked_at: nowIso()
  };
}

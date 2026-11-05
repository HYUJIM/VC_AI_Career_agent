import jsonld from "jsonld";
import { createHash, sign, verify, type KeyObject } from "node:crypto";
import { base64urlDecode, base64urlEncode } from "./base64url.js";
import { offlineDocumentLoader } from "./contexts.js";

/**
 * W3C Data Integrity(eddsa-rdfc-2022류) 서명 방식의 간소화 구현.
 * 실제 스펙처럼 "proof 옵션 정본화 해시 + 문서 정본화 해시를 이어붙여 서명"하는 구조를 그대로 따르되,
 * cryptosuite 이름은 `eddsa-fixture-2024`로 명시해 실스펙 인증 구현이 아님을 분명히 한다.
 * DX Ledger가 실제로 이 계열 proof를 발급한다면, 이 함수의 해시 조합 규칙을 실스펙에 맞춰 교체하면 된다.
 */

export interface DataIntegrityProof {
  type: "DataIntegrityProof";
  cryptosuite: string;
  created: string;
  verificationMethod: string;
  proofPurpose: "assertionMethod";
  proofValue: string;
}

async function canonize(document: unknown): Promise<string> {
  return jsonld.canonize(document, {
    algorithm: "URDNA2015",
    format: "application/n-quads",
    documentLoader: offlineDocumentLoader,
    // jsonld.js의 canonize()는 기본값이 safe:true라 미매핑 속성(@vocab 없는 용어)이 있으면 경고가
    // 아니라 에러로 승격된다. 암호학적 서명 용도에서는 safe:false로 끈는 것이 jsonld.js 공식 권장사항이다.
    safe: false
  });
}

async function computeCombinedHash(documentWithoutProof: object, proofOptions: object): Promise<Buffer> {
  const contextValue = (documentWithoutProof as { "@context"?: unknown })["@context"];
  const canonicalDoc = await canonize(documentWithoutProof);
  const canonicalProofOptions = await canonize({ "@context": contextValue, ...proofOptions });
  const docHash = createHash("sha256").update(canonicalDoc, "utf8").digest();
  const proofHash = createHash("sha256").update(canonicalProofOptions, "utf8").digest();
  return Buffer.concat([proofHash, docHash]);
}

export async function addDataIntegrityProof(
  documentWithoutProof: Record<string, unknown>,
  privateKey: KeyObject,
  verificationMethod: string
): Promise<Record<string, unknown> & { proof: DataIntegrityProof }> {
  const proofOptions = {
    type: "DataIntegrityProof" as const,
    cryptosuite: "eddsa-fixture-2024",
    created: new Date().toISOString(),
    verificationMethod,
    proofPurpose: "assertionMethod" as const
  };
  const combined = await computeCombinedHash(documentWithoutProof, proofOptions);
  const signature = sign(null, combined, privateKey);
  return {
    ...documentWithoutProof,
    proof: { ...proofOptions, proofValue: base64urlEncode(signature) }
  };
}

export async function verifyDataIntegrityProof(
  documentWithProof: Record<string, unknown>,
  publicKey: KeyObject
): Promise<boolean> {
  const { proof, ...documentWithoutProof } = documentWithProof as { proof?: DataIntegrityProof };
  if (!proof || !proof.proofValue) {
    return false;
  }
  const { proofValue, ...proofOptions } = proof;
  try {
    const combined = await computeCombinedHash(documentWithoutProof, proofOptions);
    return verify(null, combined, publicKey, base64urlDecode(proofValue));
  } catch {
    return false;
  }
}

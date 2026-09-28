import canonicalize from "canonicalize";
import { createHash } from "node:crypto";

/**
 * RFC 8785 (JSON Canonicalization Scheme)로 객체를 정본화한 뒤 SHA-256 해시를 16진수로 반환한다.
 * CredentialRecord.raw_hash 및 (추후) 원장 앵커 해시 대조의 기준값으로 쓰인다.
 * OB2.0(JSON) / OB3.0(JSON-LD as JSON) 모두 유효한 JSON이므로 이 방식으로 충분하며,
 * JSON-LD의 의미론적 동치(@context 재배열 등)까지 다루려면 verify/dataIntegrity.ts의
 * URDNA2015 canonize를 대신 사용해야 한다.
 */
export function computeRawHash(document: unknown): string {
  const canonical = canonicalize(document);
  if (canonical === undefined) {
    throw new Error("canonicalize()가 undefined를 반환했습니다 (document가 JSON 직렬화 불가능한 값을 포함)");
  }
  return createHash("sha256").update(canonical, "utf8").digest("hex");
}

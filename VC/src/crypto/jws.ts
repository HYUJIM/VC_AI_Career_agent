import { sign, verify, type KeyObject } from "node:crypto";
import { base64urlDecode, base64urlEncode } from "./base64url.js";

/**
 * RFC 7515 Compact JWS의 EdDSA 전용 최소 구현.
 * OB2.0 Signed Assertion과 OB3.0 VC-JWT 둘 다 "header.payload.signature" 구조를 그대로 쓰므로
 * 같은 sign/verify 로직을 공유한다. 실제 DX Ledger가 발급하는 JWT도 같은 방식(decode -> kid로 DID
 * 조회 -> 공개키로 서명 검증)으로 검증하면 되므로, 이 모듈은 나중에 그대로 재사용 가능하다.
 */

export interface CompactJwsHeader {
  alg: "EdDSA";
  typ: "JWT";
  kid: string;
}

export interface DecodedCompactJws<T = Record<string, unknown>> {
  header: CompactJwsHeader;
  payload: T;
  signingInput: string;
  signature: Buffer;
}

export function signCompactJws(payload: unknown, privateKey: KeyObject, kid: string): string {
  const header: CompactJwsHeader = { alg: "EdDSA", typ: "JWT", kid };
  const headerB64 = base64urlEncode(Buffer.from(JSON.stringify(header), "utf8"));
  const payloadB64 = base64urlEncode(Buffer.from(JSON.stringify(payload), "utf8"));
  const signingInput = `${headerB64}.${payloadB64}`;
  const signature = sign(null, Buffer.from(signingInput, "utf8"), privateKey);
  return `${signingInput}.${base64urlEncode(signature)}`;
}

export function decodeCompactJws<T = Record<string, unknown>>(jws: string): DecodedCompactJws<T> {
  const parts = jws.split(".");
  if (parts.length !== 3) {
    throw new Error("올바르지 않은 compact JWS 형식입니다 (header.payload.signature 3파트 필요)");
  }
  const [headerB64, payloadB64, signatureB64] = parts as [string, string, string];
  const header = JSON.parse(base64urlDecode(headerB64).toString("utf8")) as CompactJwsHeader;
  const payload = JSON.parse(base64urlDecode(payloadB64).toString("utf8")) as T;
  return {
    header,
    payload,
    signingInput: `${headerB64}.${payloadB64}`,
    signature: base64urlDecode(signatureB64)
  };
}

export function verifyCompactJwsSignature(jws: string, publicKey: KeyObject): boolean {
  const { signingInput, signature } = decodeCompactJws(jws);
  try {
    return verify(null, Buffer.from(signingInput, "utf8"), publicKey, signature);
  } catch {
    return false;
  }
}

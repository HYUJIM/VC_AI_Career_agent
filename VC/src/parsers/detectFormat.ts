import type { RawCredentialRef } from "../adapters/ports.js";
import type { CredentialFormat } from "../types/credential.js";
import { decodeCompactJws } from "../crypto/jws.js";

/**
 * RawCredentialRef(document 또는 compactJws)의 내용을 보고 OB2.0/OB3.0을 판별한다.
 * 실제 DX Ledger 연동 시에는 API의 Content-Type이나 별도 필드로 더 쉽게 판별할 수도 있지만,
 * 이 함수는 "내용만 보고도" 판별 가능하도록 만들어 어떤 소스 어댑터가 와도 재사용할 수 있게 한다.
 */
export function detectFormat(ref: RawCredentialRef): CredentialFormat {
  if (ref.compactJws) {
    const { payload } = decodeCompactJws<Record<string, unknown>>(ref.compactJws);
    if (payload && "@context" in payload) {
      return "OB3.0";
    }
    if (payload && "badge" in payload && "recipient" in payload) {
      return "OB2.0";
    }
    throw new Error("compactJws payload 형식을 인식할 수 없습니다 (OB2.0/OB3.0 스키마와 불일치)");
  }

  if (ref.document) {
    if ("@context" in ref.document) {
      return "OB3.0";
    }
    if ("badge" in ref.document && "recipient" in ref.document) {
      return "OB2.0";
    }
    throw new Error("document 형식을 인식할 수 없습니다 (OB2.0/OB3.0 스키마와 불일치)");
  }

  throw new Error("RawCredentialRef에 document와 compactJws가 모두 없습니다");
}

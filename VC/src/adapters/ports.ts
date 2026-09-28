import type { CredentialProvider, IssuerTrustLevel } from "../types/credential.js";

/**
 * 이 파일의 인터페이스들이 "벤더 격리" 경계다.
 * 검증 파이프라인/파서/정규화 로직은 이 포트에만 의존하고, 구체 구현(FixtureXxx / 나중의 DxLedgerXxx)은
 * 절대 직접 import하지 않는다. 실제 DX Ledger 연동 시 이 포트를 구현하는 새 클래스만 추가하면 된다.
 */

export interface RawCredentialRef {
  externalId: string;
  provider: CredentialProvider;
  /** JSON/JSON-LD 형태의 원본 (hosted OB2.0, Data Integrity OB3.0). document 또는 compactJws 중 하나만 채운다. */
  document?: Record<string, unknown>;
  /** Compact JWS 문자열 형태의 원본 (signed OB2.0, VC-JWT OB3.0) */
  compactJws?: string;
}

/** DX Ledger Wallet/Badge 등에서 원본 자격증명을 가져오는 포트 */
export interface CredentialSourcePort {
  listBySubject(subjectDid: string): Promise<RawCredentialRef[]>;
  getByExternalId(externalId: string): Promise<RawCredentialRef | undefined>;
  listAll(): Promise<RawCredentialRef[]>;
}

/** 발급자 신뢰 판정 포트 (신뢰 레지스트리 조회) */
export interface IssuerTrustPort {
  getTrustLevel(issuerDid: string): Promise<IssuerTrustLevel>;
}

/** 폐기 여부 확인 포트 */
export interface RevocationCheckerPort {
  isRevoked(credentialExternalId: string): Promise<boolean>;
}

/** 원장 앵커링 대조 포트 (논문 기간에는 FixtureAnchorVerifier가 항상 skip을 반환) */
export interface AnchorVerifierPort {
  checkAnchor(rawHash: string): Promise<{ checked: boolean; match: boolean | null; detail: string }>;
}

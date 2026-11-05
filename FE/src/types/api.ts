/**
 * BE(FastAPI) 요청/응답 타입 정의
 * 출처: BE/openapi.yaml (BE 브랜치, v0.4.0 — JWT 인증, 검증 결과 status/record_id 추가)
 */

export interface UserProfile {
  id: string;
  did: string;
  email: string;
  name: string;
}

/**
 * 회원가입/로그인 공통 응답 (openapi `AuthResponse`).
 * v0.4.0부터 `data`(UserProfile)는 그대로 두고 토큰 필드가 최상위에 추가되었다.
 */
export interface AuthResponse {
  success: boolean;
  message: string;
  data: UserProfile;
  /** JWT 액세스 토큰. 이후 요청에 `Authorization: Bearer <access_token>` 으로 보낸다. */
  access_token: string;
  token_type: 'bearer';
  /** 토큰 만료까지 남은 초 (BE `JWT_EXPIRES_MINUTES`, 기본 1일) */
  expires_in: number;
}

export type RegisterResponse = AuthResponse;
export type LoginResponse = AuthResponse;

export interface IssueMockBadgeResponse {
  success: boolean;
  message: string;
  vcJwt: string;
}

export type VerifyType = 'identity' | 'badge';

export interface VerifyRequest {
  type: VerifyType;
  token: string;
}

/**
 * 검증 실패 시에도 HTTP 200 + success:false 로 응답한다 (BE openapi.yaml).
 * data 는 DXWorks 검증 API 응답 원문이며, BE가 사용하는 필드만 아래에 명시한다.
 *
 * v0.4.0 (`badge`만):
 * - status: BE가 판정한 상태. success 는 `status === 'verified'` 일 때만 true.
 * - record_id: 저장된 자격증명 ID. 폐기·만료 배지도 해당 status로 저장되므로
 *   success:false 여도 record_id 가 있으면 지갑에 저장된 것이다. 원본을 해석하지 못하면 null.
 */
export interface VerifyResponse<T = BadgeVerifyData | IdentityVerifyData> {
  success: boolean;
  message: string;
  data: T;
  status?: CredentialStatus;
  record_id?: string | null;
}

export interface BadgeVerifyData {
  valid?: boolean;
  badge?: { name?: string; issuedAt?: string };
  checks?: {
    issuer?: { issuerName?: string };
    issuerTrust?: { trusted?: boolean };
    credentialId?: { id?: string; credentialStatus?: string };
    recipient?: { subjectId?: string };
  };
  [key: string]: unknown;
}

export interface IdentityVerifyData {
  verified?: boolean;
  holderDid?: string;
  claims?: { name?: string; email?: string };
  [key: string]: unknown;
}

/**
 * 자격증명 상태값. BE DB `verification_status` ENUM 과 동일한 값 집합.
 * careers API는 상태와 무관하게 모든 자격증명을 반환하므로 화면에서 status로 구분한다.
 */
export type CredentialStatus =
  | 'verified'
  | 'invalid'
  | 'revoked'
  | 'expired'
  | 'unverifiable'
  | 'pending'
  // BE 데모 데이터에만 존재하는 비표준 상태(방어적으로 허용)
  | (string & {});

export interface VerifiedCredential {
  record_id: string;
  issuer: string;
  achievement_name: string;
  status: CredentialStatus;
  issued_at: string;
  raw_data: Record<string, unknown>;
}

export interface CareersResponse {
  user_profile: UserProfile;
  verified_credentials: VerifiedCredential[];
}

/** FastAPI 요청 본문 검증 실패(422) 시 detail 배열의 원소 */
export interface ValidationErrorItem {
  loc?: (string | number)[];
  msg?: string;
  type?: string;
}

/**
 * BE 에러 응답. 일반 에러는 `{ detail: string }`,
 * 요청 본문 검증 실패(422)는 `{ detail: ValidationErrorItem[] }` 이다.
 */
export interface ApiErrorBody {
  detail?: string | ValidationErrorItem[];
  error?: string;
  message?: string;
}

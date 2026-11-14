/**
 * BE(FastAPI) 요청/응답 타입 정의
 * 출처: BE/openapi.yaml (BE 브랜치, v0.3.0 — FE 연동 기준으로 작성된 명세)
 */

export interface UserProfile {
  id: string;
  did: string;
  email: string;
  name: string;
}

/** 회원가입·로그인 응답. access_token은 이후 요청의 `Authorization: Bearer` 헤더에 쓴다. */
export interface AuthTokenFields {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
}

export interface RegisterResponse extends AuthTokenFields {
  success: boolean;
  message: string;
  data: UserProfile;
}

export interface LoginResponse extends AuthTokenFields {
  success: boolean;
  message: string;
  data: UserProfile;
}

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
 * 검증 실패 시에도 HTTP 200 + success:false 로 응답한다 (BE openapi.yaml v0.4.0).
 * data 는 BE 공통 형식이다 (외부 검증기 응답 원문이 아님).
 */
export interface VerifyResponse<T = BadgeVerifyData | IdentityVerifyData> {
  success: boolean;
  message: string;
  data: T;
}

export interface VerificationCheck {
  name: string;
  result: 'pass' | 'fail' | 'warn' | 'skip';
  detail: string | null;
}

export interface BadgeVerifyData {
  /** 저장된 자격증명 ID. 원본을 해석할 수 없어 저장하지 않았으면 null */
  record_id: string | null;
  status: CredentialStatus;
  valid: boolean;
  achievement_name: string;
  issuer_name: string;
  issuer_trusted: boolean | null;
  recipient_did: string | null;
  issued_at: string;
  expires_at: string | null;
  checks: VerificationCheck[];
}

export interface IdentityVerifyData {
  verified: boolean;
  holder_did: string | null;
  claims: Record<string, unknown>;
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

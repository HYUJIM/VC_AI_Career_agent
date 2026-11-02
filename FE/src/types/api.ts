/**
 * BE(FastAPI) 응답 타입 정의
 * 출처: BE/README.md, BE/app/api/*.py (main 브랜치 기준)
 */

export interface UserProfile {
  id: string;
  did: string;
  email: string;
  name: string;
}

export interface RegisterResponse {
  success: boolean;
  message: string;
  data: UserProfile;
}

export interface LoginResponse {
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

export interface VerifyResponse<T = Record<string, unknown>> {
  success: boolean;
  message: string;
  data: T;
}

/**
 * 자격증명 상태값.
 * VC 파트 openapi.yaml VerificationStatus 와 동일한 값 집합을 사용.
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

export interface ApiErrorBody {
  detail?: string;
  error?: string;
  message?: string;
}

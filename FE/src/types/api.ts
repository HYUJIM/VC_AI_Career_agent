/**
 * BE(FastAPI) 요청/응답 타입 정의
 * 출처: BE/openapi.yaml (v0.6.0 — JWT 인증, 관리형 지갑, 실행 시점 재검증, 포트폴리오(VC_ONLY), 선택 공개)
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

/**
 * GET /api/v1/users/me (v0.5.0). 토큰의 사용자 프로필 + 지갑(홀더 키) 상태.
 * - managed: BE가 Ed25519 홀더 키를 암호화 보관 (진짜 did:key)
 * - none: 홀더 키 없는 기존 사용자(시드 포함) 또는 서버에 마스터 키가 없을 때 가입한 사용자
 */
export interface MeResponse {
  success: boolean;
  message: string;
  data: UserProfile;
  wallet: {
    custody: 'managed' | 'none';
    key_type: string | null;
  };
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
 * 검증 실패 시에도 HTTP 200 + success:false 로 응답한다.
 * data 는 BE 공통 형식이다 (외부 검증기 응답 원문이 아님).
 *
 * `badge` 일 때:
 * - success 는 `data.status === 'verified'` 일 때만 true.
 * - data.record_id: 폐기·만료 배지도 해당 status로 저장되므로 success:false 여도
 *   record_id 가 있으면 지갑에 저장된 것이다. 원본을 해석하지 못하면 null(저장 안 됨).
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

/* ── 실행 시점 재검증 (v0.6.0) ─────────────────────────────── */

/** 서명 원본에서 뽑은 근거 + 요청 시점 검증 결과 (openapi `Evidence`) */
export interface Evidence {
  record_id: string;
  status: CredentialStatus;
  /** 지금 검증을 통과했는지 */
  verified: boolean;
  achievement_name: string;
  achievement_description: string | null;
  /** 성취 기준 설명 */
  criteria: string | null;
  issuer_name: string;
  issued_at: string;
  expires_at: string | null;
  /** 이번에 검증한 시각. 서명 원본이 없어 검증하지 못했으면 null */
  verified_at: string | null;
  checks: VerificationCheck[];
}

/** 근거에서 빠진 자격증명과 사유 (예: no_signed_original, revoked, expired, invalid, verifier_error) */
export interface ExcludedEvidence {
  record_id: string;
  status: CredentialStatus;
  reason: string;
}

/** POST /api/v1/credentials/{record_id}/reverify. success 는 `data.status === 'verified'` */
export interface ReverifyResponse {
  success: boolean;
  message: string;
  data: Evidence;
}

/* ── 포트폴리오 (VC_ONLY, v0.6.0) ──────────────────────────── */

export type PortfolioStatus = 'draft' | 'published';

/** 자유 텍스트 본문은 보낼 수 없다. 정의되지 않은 필드가 있으면 BE가 422로 거절한다. */
export interface PortfolioCreateRequest {
  title: string;
  target_role?: string | null;
  record_ids: string[];
  source: 'VC_ONLY';
}

/** 본문은 수정할 수 없다 (VC_ONLY). */
export interface PortfolioUpdateRequest {
  title?: string;
  target_role?: string | null;
  status?: PortfolioStatus;
}

export interface PortfolioSummary {
  id: string;
  title: string;
  target_role: string | null;
  status: PortfolioStatus;
  credential_count: number;
  created_at: string;
  updated_at: string;
}

export interface Portfolio extends PortfolioSummary {
  source: 'VC_ONLY';
  /** 본문을 만든 방식. 현재 template (이후 ai) */
  generator: string;
  content_markdown: string;
  /** 본문에 들어간 근거 (생성 시점 재검증 결과) */
  sections: Evidence[];
  referenced_record_ids: string[];
  /** 골랐지만 검증을 통과하지 못해 빠진 자격증명 */
  excluded: ExcludedEvidence[];
}

export interface PortfolioResponse {
  success: boolean;
  message?: string;
  data: Portfolio;
}

export interface PortfolioListResponse {
  success: boolean;
  data: PortfolioSummary[];
}

export interface SimpleSuccess {
  success: boolean;
  message: string;
}

/* ── 선택 공개 (v0.6.0) ─────────────────────────────────────── */

export interface ShareCreateRequest {
  record_ids: string[];
  /** 학생이 알아보기 위한 이름 (예: 지원 회사명). 기업용 화면에는 보이지 않는다 */
  label?: string | null;
  /** 1~90, 기본 14 */
  expires_in_days?: number;
}

export interface ShareSummary {
  id: string;
  label: string | null;
  record_ids: string[];
  expires_at: string;
  revoked: boolean;
  view_count: number;
  last_viewed_at: string | null;
  created_at: string;
}

export interface ShareCreated extends ShareSummary {
  /** 링크 토큰. 생성 응답에서만 볼 수 있다 (서버에는 해시만 저장) */
  token: string;
  /** FE 기업용 조회 화면 경로 (예: /share/{token}) */
  share_path: string;
}

export interface ShareCreateResponse {
  success: boolean;
  message: string;
  data: ShareCreated;
}

export interface ShareListResponse {
  success: boolean;
  data: ShareSummary[];
}

export interface SharedCredential extends Evidence {
  /** 서명 원본. 기업이 직접 검증할 때 사용 (원본이 없으면 null) */
  vc_jwt: string | null;
}

/** GET /api/v1/public/shares/{token} — 로그인 불필요, 봉투 없이 본문 반환 */
export interface PublicShare {
  holder: { name: string; did: string };
  credentials: SharedCredential[];
  /** 이번 열람 시 재검증한 시각 */
  verified_at: string;
  expires_at: string;
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

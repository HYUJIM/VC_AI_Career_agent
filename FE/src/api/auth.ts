import { apiClient } from '@/lib/apiClient';
import { isPreviewSession, previewMe } from '@/lib/preview';
import type {
  AuthResponse,
  LoginResponse,
  MeResponse,
  OAuthAuthorizeResponse,
  OAuthCallbackRequest,
  OAuthProviderId,
  RegisterResponse,
} from '@/types/api';

export interface RegisterPayload {
  name: string;
  email: string;
  password: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

/** POST /api/v1/users/register */
export async function registerUser(payload: RegisterPayload): Promise<RegisterResponse> {
  const { data } = await apiClient.post<RegisterResponse>('/v1/users/register', payload);
  return data;
}

/** POST /api/v1/users/login */
export async function loginUser(payload: LoginPayload): Promise<LoginResponse> {
  const { data } = await apiClient.post<LoginResponse>('/v1/users/login', payload);
  return data;
}

/** GET /api/v1/users/me (v0.5.0) — 토큰의 사용자 프로필 + 지갑(홀더 키) 상태 + 연동된 소셜 로그인 */
export async function getMe(): Promise<MeResponse> {
  if (isPreviewSession()) return previewMe();
  const { data } = await apiClient.get<MeResponse>('/v1/users/me');
  return data;
}

/**
 * POST /api/v1/users/logout (204): 이 계정에 발급된 액세스 토큰을 서버에서 모두 무효로 만든다 (다른 기기 포함).
 * 화면 로그아웃이 스토어의 토큰을 먼저 지우므로 토큰을 직접 받아 헤더에 싣는다.
 */
export async function logoutUser(accessToken: string): Promise<void> {
  await apiClient.post('/v1/users/logout', undefined, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
}

export interface OAuthAuthorizeParams {
  /** 제공자가 돌려보낼 FE 콜백 주소. BE 허용 목록(GOOGLE_REDIRECT_URI / GOOGLE_ALLOWED_REDIRECT_URIS)에 있어야 한다. */
  redirectUri: string;
  /** PKCE S256 code_challenge (code_verifier 의 SHA-256, base64url) */
  codeChallenge: string;
}

function oauthPath(provider: OAuthProviderId, action: 'authorize' | 'callback'): string {
  return `/v1/users/oauth/${encodeURIComponent(provider)}/${action}`;
}

/** GET /api/v1/users/oauth/{provider}/authorize — 제공자 로그인 화면 주소와 서명된 state를 받는다. */
export async function getOAuthAuthorization(
  provider: OAuthProviderId,
  { redirectUri, codeChallenge }: OAuthAuthorizeParams,
): Promise<OAuthAuthorizeResponse> {
  const { data } = await apiClient.get<OAuthAuthorizeResponse>(oauthPath(provider, 'authorize'), {
    params: { redirect_uri: redirectUri, code_challenge: codeChallenge, code_challenge_method: 'S256' },
  });
  return data;
}

/**
 * POST /api/v1/users/oauth/{provider}/callback — 제공자가 돌려준 code 를 BE가 토큰으로 교환해 로그인·가입을 마친다.
 * code·state 는 URL 쿼리(GET)가 아니라 본문으로 보내 서버·프록시 접근 로그에 남지 않게 한다.
 */
export async function completeOAuthLogin(
  provider: OAuthProviderId,
  body: OAuthCallbackRequest,
): Promise<AuthResponse> {
  const { data } = await apiClient.post<AuthResponse>(oauthPath(provider, 'callback'), body);
  return data;
}

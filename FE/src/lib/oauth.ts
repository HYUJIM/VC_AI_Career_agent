import { completeOAuthLogin, getOAuthAuthorization } from '@/api/auth';
import type { AuthResponse, OAuthProviderId } from '@/types/api';

/** OAuth 인가 요청을 시작한 브라우저 탭에만 잠깐 보관하는 키 (탭을 닫으면 사라지는 sessionStorage 사용) */
const OAUTH_SESSION_KEY = 'vc-ai-career-agent-oauth-tx';

/** Google 콜백 경로 (BE 기본값 GOOGLE_REDIRECT_URI = http://localhost:5173/auth/google/callback 과 맞춤) */
export const GOOGLE_CALLBACK_PATH = '/auth/google/callback';

/** BE state 기본 유효기간(10분)과 맞춘 로컬 트랜잭션 최대 보관 시간 */
const DEFAULT_EXPIRES_IN_SECONDS = 600;

/** PKCE code_verifier 바이트 길이 (32바이트 → base64url 43자, RFC 7636 43~128자 충족) */
const PKCE_VERIFIER_BYTES = 32;

/** 허용된 Google 인가 엔드포인트 호스트 (open redirect·javascript: 스킴 방지) */
const ALLOWED_GOOGLE_AUTH_HOSTS: ReadonlySet<string> = new Set(['accounts.google.com']);

interface StoredOAuthTransaction {
  provider: OAuthProviderId;
  state: string;
  codeVerifier: string;
  redirectUri: string;
  expiresAt: number;
}

export type OAuthCallbackClientErrorCode =
  | 'provider_denied'
  | 'missing_params'
  | 'state_mismatch'
  | 'state_expired';

/** 서버를 부르기 전에 콜백 화면에서 잡는 클라이언트 측 OAuth 검증 오류 */
export class OAuthCallbackClientError extends Error {
  readonly code: OAuthCallbackClientErrorCode;

  constructor(code: OAuthCallbackClientErrorCode, message: string) {
    super(message);
    this.name = 'OAuthCallbackClientError';
    this.code = code;
  }
}

function toBase64Url(bytes: Uint8Array): string {
  let binary = '';
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/** RFC 7636 PKCE S256 페어(code_verifier, code_challenge)를 Web Crypto API로 만든다. */
async function createPkcePair(): Promise<{ codeVerifier: string; codeChallenge: string }> {
  if (!globalThis.crypto?.getRandomValues || !globalThis.crypto?.subtle) {
    throw new Error('이 브라우저는 보안 로그인(Web Crypto API)을 지원하지 않습니다. HTTPS 환경인지 확인해주세요.');
  }
  const randomBytes = new Uint8Array(PKCE_VERIFIER_BYTES);
  globalThis.crypto.getRandomValues(randomBytes);
  const codeVerifier = toBase64Url(randomBytes);
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(codeVerifier));
  const codeChallenge = toBase64Url(new Uint8Array(digest));
  return { codeVerifier, codeChallenge };
}

/** 현재 접속 출처(origin) 기준 Google OAuth 콜백 전체 주소를 돌려준다. */
export function getGoogleRedirectUri(): string {
  return `${window.location.origin}${GOOGLE_CALLBACK_PATH}`;
}

/**
 * BE가 돌려준 authorization_url 이 안전한 Google https 주소인지 확인한다.
 * javascript:/data: 스킴이나 예상치 못한 호스트로의 오픈 리다이렉트를 막는다.
 */
function assertSafeAuthorizationUrl(rawUrl: string): string {
  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    throw new Error('소셜 로그인 이동 주소가 올바르지 않습니다.');
  }
  if (parsed.protocol !== 'https:' || !ALLOWED_GOOGLE_AUTH_HOSTS.has(parsed.hostname)) {
    throw new Error('허용되지 않은 소셜 로그인 이동 주소입니다.');
  }
  return parsed.toString();
}

function saveTransaction(tx: StoredOAuthTransaction): void {
  try {
    window.sessionStorage.setItem(OAUTH_SESSION_KEY, JSON.stringify(tx));
  } catch {
    throw new Error('브라우저 세션 저장소(sessionStorage)를 사용할 수 없어 소셜 로그인을 시작할 수 없습니다.');
  }
}

function consumeTransaction(): StoredOAuthTransaction | null {
  try {
    const raw = window.sessionStorage.getItem(OAUTH_SESSION_KEY);
    window.sessionStorage.removeItem(OAUTH_SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredOAuthTransaction>;
    if (
      parsed.provider !== 'google' ||
      typeof parsed.state !== 'string' ||
      !parsed.state ||
      typeof parsed.codeVerifier !== 'string' ||
      !parsed.codeVerifier ||
      typeof parsed.redirectUri !== 'string' ||
      !parsed.redirectUri ||
      typeof parsed.expiresAt !== 'number'
    ) {
      return null;
    }
    return parsed as StoredOAuthTransaction;
  } catch {
    return null;
  }
}

/**
 * Google OAuth 2.0 로그인을 시작한다.
 * 1) PKCE(S256) verifier/challenge 생성
 * 2) BE `/v1/users/oauth/google/authorize`에서 서명된 state + authorization_url 수령
 * 3) sessionStorage에 state·codeVerifier·redirectUri 보관 (콜백에서 로그인 CSRF 검증에 사용)
 * 4) Google 인증 화면으로 이동
 */
export async function startGoogleOAuth(): Promise<void> {
  const { codeVerifier, codeChallenge } = await createPkcePair();
  const redirectUri = getGoogleRedirectUri();
  const res = await getOAuthAuthorization('google', { redirectUri, codeChallenge });
  const targetUrl = assertSafeAuthorizationUrl(res.authorization_url);
  const ttlSeconds =
    typeof res.expires_in === 'number' && Number.isFinite(res.expires_in) && res.expires_in > 0
      ? res.expires_in
      : DEFAULT_EXPIRES_IN_SECONDS;

  saveTransaction({
    provider: 'google',
    state: res.state,
    codeVerifier,
    redirectUri,
    expiresAt: Date.now() + ttlSeconds * 1000,
  });

  window.location.assign(targetUrl);
}

/**
 * React 19 StrictMode 개발 환경에서는 마운트 직후 이펙트가 두 번 실행된다.
 * 일회성 OAuth 인가 코드(code)와 sessionStorage 트랜잭션이 두 번째 실행에서 실패하지 않도록
 * 같은 콜백(code + state)에 대한 진행 중/완료된 요청 Promise를 메모리에 캐시한다.
 */
const inflightCallbacks = new Map<string, Promise<AuthResponse>>();

/**
 * Google이 리다이렉트한 콜백 쿼리(`code`, `state`, `error`)를 검증하고 BE와 코드를 교환한다.
 */
export function finishGoogleOAuthCallback(searchParams: URLSearchParams): Promise<AuthResponse> {
  const errorParam = searchParams.get('error')?.trim();
  if (errorParam) {
    consumeTransaction();
    if (errorParam === 'access_denied') {
      return Promise.reject(
        new OAuthCallbackClientError('provider_denied', 'Google 로그인이 취소되었습니다. 다시 시도해주세요.'),
      );
    }
    return Promise.reject(
      new OAuthCallbackClientError('provider_denied', 'Google 인증을 완료하지 못했습니다. 다시 시도해주세요.'),
    );
  }

  const code = searchParams.get('code')?.trim() ?? '';
  const state = searchParams.get('state')?.trim() ?? '';
  if (!code || !state) {
    consumeTransaction();
    return Promise.reject(
      new OAuthCallbackClientError(
        'missing_params',
        '로그인 응답 정보가 없습니다. 로그인 화면에서 다시 시도해주세요.',
      ),
    );
  }

  const cacheKey = `google:${state}:${code}`;
  const existing = inflightCallbacks.get(cacheKey);
  if (existing) return existing;

  const tx = consumeTransaction();
  if (!tx || tx.provider !== 'google' || tx.state !== state) {
    return Promise.reject(
      new OAuthCallbackClientError(
        'state_mismatch',
        '이 브라우저에서 시작한 로그인 요청과 일치하지 않습니다. 보안을 위해 로그인을 중단했습니다. 로그인 화면에서 다시 시도해주세요.',
      ),
    );
  }

  if (tx.expiresAt <= Date.now()) {
    return Promise.reject(
      new OAuthCallbackClientError(
        'state_expired',
        '로그인 유효 시간(10분)이 지났습니다. 로그인 화면에서 다시 시도해주세요.',
      ),
    );
  }

  const promise = completeOAuthLogin('google', {
    code,
    state,
    redirect_uri: tx.redirectUri,
    code_verifier: tx.codeVerifier,
  }).finally(() => {
    // 잠시 뒤 캐시를 비워 메모리에 남지 않게 한다 (StrictMode 이중 실행만 넘기면 충분).
    setTimeout(() => inflightCallbacks.delete(cacheKey), 5_000);
  });

  inflightCallbacks.set(cacheKey, promise);
  return promise;
}

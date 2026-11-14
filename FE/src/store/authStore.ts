import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { AuthResponse, UserProfile } from '@/types/api';

/**
 * 로그인 화면에 안내 배너를 띄우기 위한 로그아웃 사유
 * - loggedOut: 사용자가 직접 로그아웃 (같은 브라우저의 다른 탭에서 로그아웃한 경우 포함)
 * - sessionExpired: 화면의 "다시 로그인" 버튼 (저장된 DID가 서버에 없음(404) 등)
 * - tokenExpired: BE가 토큰을 거절함(401) 또는 토큰 만료 시각이 지남. apiClient 응답 인터셉터와 만료 타이머가 자동으로 로그아웃한다
 */
export type LogoutReason = 'loggedOut' | 'sessionExpired' | 'tokenExpired';

/** 로그인 상태를 담는 localStorage 키. 같은 브라우저의 모든 탭이 이 값 하나를 함께 쓴다. */
const AUTH_STORAGE_KEY = 'vc-ai-career-agent-auth';
/** setTimeout 최대 지연(약 24.8일). 더 먼 만료는 이 간격마다 다시 확인한다. */
const MAX_TIMER_MS = 2_147_483_647;

interface AuthState {
  user: UserProfile | null;
  /** BE가 로그인·가입(이메일 또는 소셜 로그인) 때 준 JWT 액세스 토큰. 개발용 미리보기 세션은 null */
  accessToken: string | null;
  /** 토큰 만료 시각(이 기기 시계 기준 ms). 응답의 expires_in으로 계산한다. 모르면 null (BE의 401로만 판단) */
  expiresAt: number | null;
  isAuthenticated: boolean;
  logoutReason: LogoutReason | null;
  /** 로그인·회원가입·소셜 로그인 성공 시 프로필과 토큰(및 만료까지 남은 초)을 함께 저장한다. */
  setSession: (user: UserProfile, accessToken: string | null, expiresIn?: number | null) => void;
  logout: (reason?: LogoutReason) => void;
  clearLogoutReason: () => void;
}

type PersistedAuthState = Pick<AuthState, 'user' | 'accessToken' | 'expiresAt' | 'isAuthenticated'>;

const EMPTY_SESSION: PersistedAuthState = { user: null, accessToken: null, expiresAt: null, isAuthenticated: false };

/** 저장 형식 버전. v0 = 토큰 없이 프로필만 저장(BE v0.3.0) 또는 만료 시각 없이 토큰만 저장하던 시절 */
const AUTH_STORAGE_VERSION = 1;

function toExpiresAt(expiresIn: number | null | undefined): number | null {
  return typeof expiresIn === 'number' && Number.isFinite(expiresIn) && expiresIn > 0
    ? Date.now() + expiresIn * 1000
    : null;
}

/**
 * 예전 형식의 저장값을 지금 형식으로 바꾼다.
 * 토큰이 없는 세션은 BE(AUTH_REQUIRED=true)가 모든 요청을 401로 거절하므로 버리고 다시 로그인하게 한다.
 * 토큰은 있지만 만료 시각이 없으면 BE의 401로만 만료를 판단한다(expiresAt: null).
 */
function migrateStoredSession(persisted: unknown): PersistedAuthState {
  const old = (persisted ?? {}) as Partial<PersistedAuthState>;
  if (!old.isAuthenticated || !old.user || typeof old.accessToken !== 'string' || !old.accessToken) {
    return EMPTY_SESSION;
  }
  return { user: old.user, accessToken: old.accessToken, expiresAt: null, isAuthenticated: true };
}

/**
 * 로그인·가입 응답의 프로필(did 포함)과 액세스 토큰을 로컬에 보관해 로그인 상태를 유지한다.
 * 토큰은 `lib/apiClient.ts` 인터셉터가 공개 API를 뺀 모든 요청의 Authorization 헤더에 붙인다.
 *
 * logoutReason 은 라우터 state 대신 스토어에 둔다. 로그아웃 직후 ProtectedRoute 가 즉시
 * /login 으로 리다이렉트하면서 navigate(..., { state }) 의 state 가 유실될 수 있기 때문이다.
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      ...EMPTY_SESSION,
      logoutReason: null,
      setSession: (user, accessToken, expiresIn = null) =>
        set({ user, accessToken, expiresAt: toExpiresAt(expiresIn), isAuthenticated: true, logoutReason: null }),
      logout: (reason) => set({ ...EMPTY_SESSION, logoutReason: reason ?? null }),
      clearLogoutReason: () => set({ logoutReason: null }),
    }),
    {
      name: AUTH_STORAGE_KEY,
      version: AUTH_STORAGE_VERSION,
      migrate: migrateStoredSession,
      // 일회성 안내인 logoutReason 은 새로고침 후까지 남기지 않는다.
      partialize: (state): PersistedAuthState => ({
        user: state.user,
        accessToken: state.accessToken,
        expiresAt: state.expiresAt,
        isAuthenticated: state.isAuthenticated,
      }),
      // 저장된 세션이 세션 필드를 모두 정한다 (expiresAt 이 없으면 null).
      merge: (persisted, current) => ({ ...current, expiresAt: null, ...(persisted as Partial<AuthState>) }),
    },
  ),
);

/** BE 인증 응답(`AuthResponse`)으로 세션을 시작한다. 이메일 로그인·회원가입·소셜 로그인이 같은 경로를 쓴다. */
export function startSession(res: AuthResponse): void {
  useAuthStore.getState().setSession(res.data, res.access_token, res.expires_in);
}

let expiryTimer: ReturnType<typeof setTimeout> | undefined;

/** 토큰 만료 시각이 지났으면 로그아웃하고("로그인 시간이 만료되었습니다" 배너), 아니면 그 시각에 다시 확인한다. */
function checkExpiry() {
  clearTimeout(expiryTimer);
  expiryTimer = undefined;
  const { isAuthenticated, expiresAt, logout } = useAuthStore.getState();
  // 만료 시각을 모르는 세션(이전 버전 로그인·미리보기)은 BE의 401로만 판단한다.
  if (!isAuthenticated || typeof expiresAt !== 'number' || !Number.isFinite(expiresAt)) return;
  const remaining = expiresAt - Date.now();
  if (remaining <= 0) logout('tokenExpired');
  else expiryTimer = setTimeout(checkExpiry, Math.min(remaining, MAX_TIMER_MS));
}

/**
 * React Router 가 라우트와 맞춰 보기 전에 경로를 푸는 방식(decodePath)을 그대로 따른다:
 * '/'로 나눈 조각마다 %인코딩을 풀고, 조각 안에서 '/'가 된 %2F 는 다시 %2F 로 둔다.
 * 잘못된 %인코딩이 하나라도 있으면 라우터처럼 원래 경로를 그대로 쓴다.
 */
function decodePathLikeRouter(pathname: string): string {
  try {
    return pathname.split('/').map((segment) => decodeURIComponent(segment).replace(/\//g, '%2F')).join('/');
  } catch {
    return pathname;
  }
}

/**
 * 다른 탭에서 로그인·로그아웃해 저장소가 바뀌면 이 탭에도 반영한다.
 * persist 는 set() 마다 이 탭의 메모리 상태 전체를 저장소에 쓰므로, 반영하지 않으면 오래된 탭이
 * 로그아웃된 세션(또는 다른 사용자의 세션)을 저장소에 다시 써 넣는다.
 */
function adoptStoredSession() {
  const before = useAuthStore.getState();
  let stored: string | null;
  try {
    stored = window.localStorage.getItem(AUTH_STORAGE_KEY);
  } catch {
    return;
  }
  if (stored === null) {
    // 저장소가 지워졌다 (사이트 데이터 삭제 등): 이 탭도 로그아웃한다.
    if (before.isAuthenticated) before.logout('loggedOut');
    return;
  }
  // localStorage 는 동기 저장소라 여기서 바로 반영된다. rehydrate 는 저장소에 다시 쓰지 않는다.
  void useAuthStore.persist.rehydrate();
  const after = useAuthStore.getState();
  // 로그아웃돼 로그인·회원가입·OAuth 콜백 화면에 있던 탭: 다른 탭에서 로그인했으면 그 계정의 대시보드로 새로 연다
  // (그대로 두면 로그인 화면과 "로그아웃되었습니다" 배너 위에 새 계정의 메뉴가 함께 보인다).
  // 라우터처럼 경로의 %인코딩을 풀고 대소문자와 끝의 '/'는 무시한다 (/login/, /LOGIN, /%6Cogin 도 로그인 화면을 보여 준다).
  const path = decodePathLikeRouter(window.location.pathname).replace(/\/+$/, '').toLowerCase();
  if (
    !before.isAuthenticated &&
    after.isAuthenticated &&
    (path === '/login' || path === '/register' || path === '/auth/google/callback')
  ) {
    window.location.replace('/dashboard');
    return;
  }
  if (!before.isAuthenticated || after.user?.id === before.user?.id) return;
  if (after.isAuthenticated) {
    // 다른 사용자로 바뀌었다: 이전 사용자의 화면 상태(작성 중인 내용·조회 캐시)를 버리고 새로 연다.
    window.location.reload();
  } else {
    const expired = typeof before.expiresAt === 'number' && before.expiresAt <= Date.now();
    useAuthStore.setState({ logoutReason: expired ? 'tokenExpired' : 'loggedOut' });
  }
}

useAuthStore.subscribe((state, prev) => {
  if (state.expiresAt !== prev.expiresAt || state.isAuthenticated !== prev.isAuthenticated) checkExpiry();
});
// 만료된 세션으로 앱을 열면 첫 화면을 그리기 전에 로그아웃한다 (저장소에서도 지운다).
checkExpiry();
window.addEventListener('storage', (event) => {
  if (event.key === null || event.key === AUTH_STORAGE_KEY) adoptStoredSession();
});
// 뒤로 가기 캐시(bfcache)에서 돌아온 페이지는 그동안의 storage 이벤트를 받지 못했고 타이머도 멈춰 있었다.
window.addEventListener('pageshow', (event) => {
  if (!event.persisted) return;
  adoptStoredSession();
  checkExpiry();
});
// 절전·백그라운드 탭에서는 타이머가 늦게 울릴 수 있으므로 화면이 다시 보일 때 만료를 한 번 더 확인한다.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') checkExpiry();
});

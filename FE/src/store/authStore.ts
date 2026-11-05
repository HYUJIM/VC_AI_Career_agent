import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { UserProfile } from '@/types/api';

/** 로그인 화면에 안내 배너를 띄우기 위한 로그아웃 사유 */
export type LogoutReason = 'loggedOut' | 'sessionExpired';

interface AuthState {
  user: UserProfile | null;
  /** BE v0.4.0 JWT 액세스 토큰. 개발용 미리보기 세션은 토큰이 없다(null). */
  accessToken: string | null;
  isAuthenticated: boolean;
  logoutReason: LogoutReason | null;
  /** 로그인/회원가입 성공 시 프로필과 토큰을 함께 저장한다. */
  setSession: (user: UserProfile, accessToken: string | null) => void;
  logout: (reason?: LogoutReason) => void;
  clearLogoutReason: () => void;
}

type PersistedAuthState = Pick<AuthState, 'user' | 'accessToken' | 'isAuthenticated'>;

const EMPTY_SESSION: PersistedAuthState = { user: null, accessToken: null, isAuthenticated: false };

/**
 * BE는 로그인/회원가입 응답으로 사용자 정보(did 포함)와 JWT(`access_token`)를 내려준다.
 * FE는 둘을 로컬에 보관하고, `lib/apiClient.ts` 인터셉터가 모든 요청에 Bearer 헤더로 붙인다.
 *
 * logoutReason 은 라우터 state 대신 스토어에 둔다. 로그아웃 직후 ProtectedRoute 가 즉시
 * /login 으로 리다이렉트하면서 navigate(..., { state }) 의 state 가 유실될 수 있기 때문이다.
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      ...EMPTY_SESSION,
      logoutReason: null,
      setSession: (user, accessToken) =>
        set({ user, accessToken, isAuthenticated: true, logoutReason: null }),
      logout: (reason) => set({ ...EMPTY_SESSION, logoutReason: reason ?? null }),
      clearLogoutReason: () => set({ logoutReason: null }),
    }),
    {
      name: 'vc-ai-career-agent-auth',
      // v0 = 토큰 없이 프로필만 저장하던 시절(BE v0.3.0). 토큰이 없으면 BE가 AUTH_REQUIRED=true일 때
      // 모든 요청을 401로 거절하므로, 이전 저장값은 버리고 다시 로그인하게 한다.
      version: 1,
      migrate: () => EMPTY_SESSION,
      // 일회성 안내인 logoutReason 은 새로고침 후까지 남기지 않는다.
      partialize: (state): PersistedAuthState => ({
        user: state.user,
        accessToken: state.accessToken,
        isAuthenticated: state.isAuthenticated,
      }),
    },
  ),
);

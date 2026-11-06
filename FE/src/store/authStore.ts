import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { UserProfile } from '@/types/api';

/** 로그인 화면에 안내 배너를 띄우기 위한 로그아웃 사유 */
export type LogoutReason = 'loggedOut' | 'sessionExpired';

interface AuthState {
  user: UserProfile | null;
  /** BE가 로그인·가입 때 준 액세스 토큰. 미리보기 세션이나 이전 버전 로그인은 null */
  accessToken: string | null;
  isAuthenticated: boolean;
  logoutReason: LogoutReason | null;
  setUser: (user: UserProfile, accessToken?: string | null) => void;
  logout: (reason?: LogoutReason) => void;
  clearLogoutReason: () => void;
}

/**
 * 로그인·가입 응답의 프로필(did 포함)과 액세스 토큰을 로컬에 보관해 로그인 상태를 유지한다.
 * 토큰은 apiClient가 모든 요청의 Authorization 헤더에 붙인다 (직무 비교·커리큘럼 API는 토큰 필수).
 *
 * logoutReason 은 라우터 state 대신 스토어에 둔다. 로그아웃 직후 ProtectedRoute 가 즉시
 * /login 으로 리다이렉트하면서 navigate(..., { state }) 의 state 가 유실될 수 있기 때문이다.
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      accessToken: null,
      isAuthenticated: false,
      logoutReason: null,
      setUser: (user, accessToken = null) =>
        set({ user, accessToken, isAuthenticated: true, logoutReason: null }),
      logout: (reason) =>
        set({ user: null, accessToken: null, isAuthenticated: false, logoutReason: reason ?? null }),
      clearLogoutReason: () => set({ logoutReason: null }),
    }),
    {
      name: 'vc-ai-career-agent-auth',
      // 일회성 안내인 logoutReason 은 새로고침 후까지 남기지 않는다.
      partialize: (state) => ({
        user: state.user,
        accessToken: state.accessToken,
        isAuthenticated: state.isAuthenticated,
      }),
    },
  ),
);

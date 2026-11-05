import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { UserProfile } from '@/types/api';

/** 로그인 화면에 안내 배너를 띄우기 위한 로그아웃 사유 */
export type LogoutReason = 'loggedOut' | 'sessionExpired';

interface AuthState {
  user: UserProfile | null;
  isAuthenticated: boolean;
  logoutReason: LogoutReason | null;
  setUser: (user: UserProfile) => void;
  logout: (reason?: LogoutReason) => void;
  clearLogoutReason: () => void;
}

/**
 * BE는 로그인 이후 토큰/세션 없이 사용자 정보(did 포함)만 내려준다.
 * 따라서 FE에서는 로그인 성공 시 받은 프로필을 로컬에 보관해 로그인 상태를 유지한다.
 *
 * logoutReason 은 라우터 state 대신 스토어에 둔다. 로그아웃 직후 ProtectedRoute 가 즉시
 * /login 으로 리다이렉트하면서 navigate(..., { state }) 의 state 가 유실될 수 있기 때문이다.
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      isAuthenticated: false,
      logoutReason: null,
      setUser: (user) => set({ user, isAuthenticated: true, logoutReason: null }),
      logout: (reason) => set({ user: null, isAuthenticated: false, logoutReason: reason ?? null }),
      clearLogoutReason: () => set({ logoutReason: null }),
    }),
    {
      name: 'vc-ai-career-agent-auth',
      // 일회성 안내인 logoutReason 은 새로고침 후까지 남기지 않는다.
      partialize: (state) => ({ user: state.user, isAuthenticated: state.isAuthenticated }),
    },
  ),
);

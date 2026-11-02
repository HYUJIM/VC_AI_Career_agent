import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { UserProfile } from '@/types/api';

interface AuthState {
  user: UserProfile | null;
  isAuthenticated: boolean;
  setUser: (user: UserProfile) => void;
  logout: () => void;
}

/**
 * BE에는 세션/토큰 개념이 없고(DID 기반 간이 로그인) 응답으로 user_profile만 내려준다.
 * 따라서 FE에서는 로그인 성공 시 받은 프로필을 로컬에 보관해 로그인 상태를 유지한다.
 */
export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      isAuthenticated: false,
      setUser: (user) => set({ user, isAuthenticated: true }),
      logout: () => set({ user: null, isAuthenticated: false }),
    }),
    {
      name: 'vc-ai-career-agent-auth',
    },
  ),
);

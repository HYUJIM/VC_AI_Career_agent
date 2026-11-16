import { type ReactNode, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return children;
}

/**
 * 이미 로그인된 상태에서 `/login`·`/register`에 직접 접근하면 `/dashboard`로 돌려보낸다.
 * 진입 시점의 로그인 여부만 확인해, 화면 안에서 방금 로그인에 성공했을 때 전달하는
 * `navigate('/dashboard', { state: { welcome, newUser } })` 가 덮어써지지 않게 한다.
 */
export function GuestRoute({ children }: { children: ReactNode }) {
  const [isAuthenticatedOnMount] = useState(() => useAuthStore.getState().isAuthenticated);

  if (isAuthenticatedOnMount) {
    return <Navigate to="/dashboard" replace />;
  }

  return children;
}

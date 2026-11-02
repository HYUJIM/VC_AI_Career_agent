import { Link, Outlet, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/Button';
import { useAuthStore } from '@/store/authStore';

export function Layout() {
  const { user, isAuthenticated, logout } = useAuthStore();
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate('/login');
  }

  return (
    <div className="min-h-screen bg-[var(--color-surface)]">
      <header className="border-b border-[var(--color-border)] bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <Link to="/" className="text-lg font-bold text-[var(--color-text)]">
            VC AI Career Agent
          </Link>
          {isAuthenticated && user && (
            <div className="flex items-center gap-4">
              <span className="text-sm text-[var(--color-text-muted)]">
                {user.name} ({user.email})
              </span>
              <Button variant="secondary" onClick={handleLogout}>
                로그아웃
              </Button>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}

import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/Button';
import { useAuthStore } from '@/store/authStore';

const NAV_ITEMS = [
  { to: '/issue', label: '자격증명 발급' },
  { to: '/dashboard', label: '자격증명 지갑' },
  { to: '/portfolio', label: 'AI 포트폴리오' },
];

export function Layout() {
  const { user, isAuthenticated, logout } = useAuthStore();
  const navigate = useNavigate();

  function handleLogout() {
    logout('loggedOut');
    navigate('/login');
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-white">
        <div className="mx-auto flex h-16 max-w-5xl items-center justify-between gap-6 px-5">
          <div className="flex items-center gap-8">
            <Link to="/" className="flex items-center gap-2 text-[17px] font-bold tracking-tight">
              <span
                aria-hidden="true"
                className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--color-primary)] text-[13px] font-extrabold text-white"
              >
                V
              </span>
              커리어 지갑
            </Link>
            {isAuthenticated && (
              <nav className="hidden items-center gap-1 sm:flex">
                {NAV_ITEMS.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) =>
                      `rounded-lg px-3 py-2 text-[15px] transition-colors ${
                        isActive
                          ? 'font-semibold text-[var(--color-text)]'
                          : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text-sub)]'
                      }`
                    }
                  >
                    {item.label}
                  </NavLink>
                ))}
              </nav>
            )}
          </div>
          {isAuthenticated && user && (
            <div className="flex items-center gap-3">
              <span className="hidden text-[15px] font-medium text-[var(--color-text-sub)] md:inline">
                {user.name} 님
              </span>
              <Button variant="tertiary" size="sm" onClick={handleLogout}>
                로그아웃
              </Button>
            </div>
          )}
        </div>
        {isAuthenticated && (
          <nav className="flex gap-1 overflow-x-auto px-3 pb-2 sm:hidden">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `shrink-0 rounded-full px-3 py-1.5 text-sm ${
                    isActive
                      ? 'bg-[var(--color-text)] font-semibold text-white'
                      : 'text-[var(--color-text-muted)]'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        )}
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-5 py-8">
        <Outlet />
      </main>
      <footer className="mx-auto w-full max-w-5xl px-5 py-8 text-[13px] text-[var(--color-text-muted)]">
        커리어 지갑 · 검증된 자격증명으로 만드는 AI 포트폴리오
      </footer>
    </div>
  );
}

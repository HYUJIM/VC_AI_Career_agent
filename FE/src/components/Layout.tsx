import { Link, NavLink, Outlet, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/Button';
import { useAuthStore } from '@/store/authStore';

const NAV_ITEMS = [
  { to: '/issue', label: '자격증명 발급' },
  { to: '/dashboard', label: '자격증명 지갑' },
  { to: '/portfolio', label: 'AI 포트폴리오' },
];

function shortDid(did: string) {
  return did.length > 24 ? `${did.slice(0, 14)}...${did.slice(-6)}` : did;
}

export function Layout() {
  const { user, isAuthenticated, logout } = useAuthStore();
  const navigate = useNavigate();

  function handleLogout() {
    logout('loggedOut');
    navigate('/login');
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-[var(--color-bg)]/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-6 px-6 py-3">
          <div className="flex items-center gap-8">
            <Link to="/" className="flex items-center gap-2 text-lg font-bold">
              <span className="text-[var(--color-primary)]">VC</span> AI Career Agent
              <span className="rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] font-semibold text-[var(--color-text-muted)]">
                DID VERIFIED
              </span>
            </Link>
            {isAuthenticated && (
              <nav className="flex items-center gap-5 text-sm">
                {NAV_ITEMS.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    className={({ isActive }) =>
                      isActive
                        ? 'font-semibold text-[var(--color-primary)]'
                        : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
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
              <span className="font-mono-did hidden rounded-full border border-[var(--color-border)] bg-[var(--color-card)] px-3 py-1 text-xs text-emerald-300 sm:inline">
                {shortDid(user.did)}
              </span>
              <span className="hidden text-sm text-[var(--color-text-muted)] md:inline">
                {user.name}
              </span>
              <Button variant="secondary" onClick={handleLogout}>
                로그아웃
              </Button>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <Outlet />
      </main>
      <footer className="mx-auto max-w-6xl border-t border-[var(--color-border)] px-6 py-6 text-xs text-[var(--color-text-muted)]">
        VC AI Career Agent · Decentralized Identity & Verifiable Credentials
      </footer>
    </div>
  );
}

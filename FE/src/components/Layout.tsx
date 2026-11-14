import { motion } from 'motion/react';
import { useEffect } from 'react';
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { logoutUser } from '@/api/auth';
import { ProfileMenu } from '@/components/ProfileMenu';
import { Button } from '@/components/ui/Button';
import { useAuthStore } from '@/store/authStore';

const GITHUB_URL = 'https://github.com/HYUJIM/VC_AI_Career_agent';
const CURRENT_YEAR = new Date().getFullYear();

const NAV_ITEMS = [
  { to: '/issue', label: '자격증명 발급' },
  { to: '/dashboard', label: '자격증명 지갑' },
  { to: '/career', label: '직무 비교·커리큘럼' },
  { to: '/portfolio', label: 'AI 포트폴리오' },
];

export function Layout() {
  const { user, isAuthenticated, logout } = useAuthStore();
  const navigate = useNavigate();
  const { pathname } = useLocation();

  // 페이지(탭)를 옮기면 항상 맨 위에서 시작한다. BrowserRouter는 스크롤 위치를 초기화하지 않는다.
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);

  function handleLogout() {
    // 서버에서도 이 계정의 토큰을 무효로 만든다. 응답을 기다리지 않고, 실패해도 이 브라우저에서는 로그아웃한다.
    const token = useAuthStore.getState().accessToken;
    if (token) logoutUser(token).catch(() => undefined);
    logout('loggedOut');
    navigate('/login');
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-10 border-b border-[var(--color-border)] bg-white">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-6 px-6">
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
                      `relative rounded-lg px-3 py-2 text-[15px] transition-colors ${
                        isActive
                          ? 'font-semibold text-[var(--color-text)]'
                          : 'text-[var(--color-text-muted)] hover:bg-[var(--color-surface)] hover:text-[var(--color-text-sub)]'
                      }`
                    }
                  >
                    {({ isActive }) => (
                      <>
                        {item.label}
                        {/* Motion layoutId: 활성 탭 밑줄이 탭 사이를 미끄러지듯 이동한다 */}
                        {isActive && (
                          <motion.span
                            layoutId="nav-underline"
                            aria-hidden="true"
                            className="absolute inset-x-3 -bottom-[13px] h-[2px] rounded-full bg-[var(--color-text)]"
                            transition={{ type: 'spring', stiffness: 500, damping: 40 }}
                          />
                        )}
                      </>
                    )}
                  </NavLink>
                ))}
              </nav>
            )}
          </div>
          {isAuthenticated && user && (
            <div className="flex items-center gap-2">
              <Link to="/issue" className="hidden sm:block">
                <Button size="sm">+ 발급받기</Button>
              </Link>
              <ProfileMenu user={user} onLogout={handleLogout} />
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
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        {/* 경로가 바뀔 때마다 새로 마운트되며 살짝 떠오르듯 나타난다 */}
        <motion.div
          key={pathname}
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25, ease: 'easeOut' }}
        >
          <Outlet />
        </motion.div>
      </main>
      <footer className="mt-8 border-t border-[var(--color-border)] bg-white">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-6 py-8 sm:flex-row sm:justify-between">
          <div className="flex flex-col gap-2">
            <span className="text-[15px] font-bold">커리어 지갑</span>
            <p className="max-w-xs text-[13px] leading-relaxed text-[var(--color-text-muted)]">
              DID와 검증 가능한 자격증명(VC)으로 신뢰할 수 있는 AI 포트폴리오를 만듭니다.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-8 text-[13px]">
            <div className="flex flex-col gap-2">
              <span className="font-semibold text-[var(--color-text-sub)]">서비스</span>
              {NAV_ITEMS.map((item) => (
                <Link key={item.to} to={item.to} className="text-[var(--color-text-muted)] hover:underline">
                  {item.label}
                </Link>
              ))}
            </div>
            <div className="flex flex-col gap-2">
              <span className="font-semibold text-[var(--color-text-sub)]">프로젝트</span>
              <a
                href={GITHUB_URL}
                target="_blank"
                rel="noreferrer"
                className="text-[var(--color-text-muted)] hover:underline"
              >
                GitHub
              </a>
              <a
                href={`${GITHUB_URL}#readme`}
                target="_blank"
                rel="noreferrer"
                className="text-[var(--color-text-muted)] hover:underline"
              >
                사용 가이드
              </a>
              <a
                href={`${GITHUB_URL}/issues`}
                target="_blank"
                rel="noreferrer"
                className="text-[var(--color-text-muted)] hover:underline"
              >
                문의 · 버그 제보
              </a>
            </div>
          </div>
        </div>
        <p className="mx-auto w-full max-w-6xl px-6 pb-6 text-[12px] text-[var(--color-text-muted)]">
          © {CURRENT_YEAR} 커리어 지갑 · 검증된 자격증명으로 만드는 AI 포트폴리오
        </p>
      </footer>
    </div>
  );
}

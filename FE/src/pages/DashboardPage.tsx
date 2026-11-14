import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { getCareersByDid } from '@/api/credentials';
import { CareersErrorAlert } from '@/components/CareersErrorAlert';
import { CredentialDetailModal } from '@/components/CredentialDetailModal';
import { CredentialList } from '@/components/dashboard/CredentialList';
import { OnboardingChecklist } from '@/components/dashboard/OnboardingChecklist';
import {
  IssuerStatsCard,
  ProfileCard,
  QuickActionsCard,
  RecentActivityCard,
} from '@/components/dashboard/SidePanel';
import { StatsCard } from '@/components/dashboard/StatsCard';
import { StatusRingCard } from '@/components/dashboard/StatusRingCard';
import { Alert } from '@/components/ui/Alert';
import { type StatusFilter, getStatusGroup } from '@/lib/credentialStatus';
import { hasUsedPortfolio } from '@/lib/onboarding';
import { useAuthStore } from '@/store/authStore';
import type { VerifiedCredential } from '@/types/api';

/** 로그인/회원가입/발급 화면에서 대시보드로 이동할 때 전달하는 상태 */
export interface DashboardLocationState {
  welcome?: string;
  newUser?: boolean;
  oauthLinked?: boolean;
  issued?: string;
}

export function DashboardPage() {
  const user = useAuthStore((state) => state.user);
  const location = useLocation();
  const [banner, setBanner] = useState<DashboardLocationState | null>(
    (location.state as DashboardLocationState | null) ?? null,
  );
  const [detail, setDetail] = useState<VerifiedCredential | null>(null);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['careers', user?.did],
    queryFn: () => getCareersByDid(user!.did),
    enabled: Boolean(user?.did),
  });

  // BE careers API는 status와 무관하게 모든 자격증명을 반환한다 → 화면에서 status로 구분
  const credentials = useMemo(() => data?.verified_credentials ?? [], [data]);

  const counts = useMemo(() => {
    const c: Record<StatusFilter, number> = { all: credentials.length, verified: 0, attention: 0, pending: 0 };
    credentials.forEach((cred) => {
      c[getStatusGroup(cred.status)] += 1;
    });
    return c;
  }, [credentials]);

  const newestFirst = useMemo(
    () => [...credentials].sort((a, b) => +new Date(b.issued_at) - +new Date(a.issued_at)),
    [credentials],
  );

  const issuerStats = useMemo(() => {
    const map = new Map<string, number>();
    credentials
      .filter((c) => c.status === 'verified')
      .forEach((c) => map.set(c.issuer, (map.get(c.issuer) ?? 0) + 1));
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [credentials]);

  if (!user) return null;

  const latest = newestFirst[0];

  return (
    <div className="flex flex-col gap-4">
      {banner?.issued && (
        <Alert variant="success" title="자격증명이 지갑에 저장되었습니다." onClose={() => setBanner(null)}>
          {banner.issued} 배지가 검증을 통과해 목록에 추가되었습니다.
        </Alert>
      )}
      {!banner?.issued && banner?.welcome && (
        <Alert
          variant="success"
          title={banner.newUser ? '회원가입이 완료되었습니다.' : `${banner.welcome} 님, 환영합니다.`}
          onClose={() => setBanner(null)}
        >
          {banner.newUser
            ? `${banner.welcome} 님의 DID가 발급되었습니다. "새 자격증명 발급받기"로 첫 배지를 받아보세요.`
            : banner.oauthLinked
              ? '기존 계정에 Google 계정이 연동되어 로그인되었습니다.'
              : '로그인되었습니다.'}
        </Alert>
      )}

      <section className="flex flex-col gap-1 px-1">
        <h1 className="text-[26px] font-bold leading-snug tracking-tight">{user.name} 님의 자격증명 지갑</h1>
        <p className="text-[15px] text-[var(--color-text-sub)]">
          블록체인으로 검증된 자격증명을 한곳에서 관리하고 포트폴리오에 활용하세요.
        </p>
      </section>

      <div className="grid items-start gap-5 lg:grid-cols-[1fr_340px]">
        <div className="flex min-w-0 flex-col gap-5">
          <StatsCard
            total={counts.all}
            verified={counts.verified}
            attention={counts.attention}
            latestDate={latest ? new Date(latest.issued_at).toLocaleDateString('ko-KR') : null}
          />

          {data && (
            <OnboardingChecklist
              steps={[
                { label: '회원가입하고 DID 발급받기', done: true },
                { label: '첫 자격증명 발급받기', done: credentials.length > 0, to: '/issue' },
                { label: 'AI 포트폴리오 만들어보기', done: hasUsedPortfolio(user.did), to: '/portfolio' },
              ]}
            />
          )}

          {isError && <CareersErrorAlert error={error} onRetry={() => refetch()} />}

          <CredentialList
            credentials={credentials}
            counts={counts}
            isLoading={isLoading}
            loaded={Boolean(data)}
            onSelect={setDetail}
          />
        </div>

        <aside className="flex flex-col gap-4">
          <ProfileCard user={user} />
          <StatusRingCard
            total={counts.all}
            verified={counts.verified}
            attention={counts.attention}
            pending={counts.pending}
          />
          <QuickActionsCard />
          <IssuerStatsCard stats={issuerStats} />
          <RecentActivityCard newestFirst={newestFirst} onSelect={setDetail} />
        </aside>
      </div>

      {/* key: 다른 자격증명을 열면 이전 재검증 결과를 초기화한다 */}
      <CredentialDetailModal key={detail?.record_id} credential={detail} onClose={() => setDetail(null)} />
    </div>
  );
}

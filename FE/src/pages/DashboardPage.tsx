import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { getCareersByDid } from '@/api/credentials';
import { CareersErrorAlert } from '@/components/CareersErrorAlert';
import { StatusBadge } from '@/components/StatusBadge';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { useAuthStore } from '@/store/authStore';

/** 로그인/회원가입/발급 화면에서 대시보드로 이동할 때 전달하는 상태 */
export interface DashboardLocationState {
  welcome?: string;
  newUser?: boolean;
  issued?: string;
}

function Stat({ label, value, unit }: { label: string; value: number | string; unit: string }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[13px] text-[var(--color-text-muted)]">{label}</span>
      <span className="text-[22px] font-bold tracking-tight">
        {value}
        <span className="ml-0.5 text-[15px] font-medium text-[var(--color-text-sub)]">{unit}</span>
      </span>
    </div>
  );
}

/** 발급 기관 이름의 첫 글자로 만드는 아바타 (아이콘 리소스 없이 목록 구분용) */
function IssuerAvatar({ issuer }: { issuer: string }) {
  const initial = issuer.replace(/[^\p{L}\p{N}]/gu, '').charAt(0).toUpperCase() || '?';
  return (
    <span
      aria-hidden="true"
      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[var(--color-surface)] text-[15px] font-bold text-[var(--color-text-sub)]"
    >
      {initial}
    </span>
  );
}

export function DashboardPage() {
  const user = useAuthStore((state) => state.user);
  const location = useLocation();
  const [banner, setBanner] = useState<DashboardLocationState | null>(
    (location.state as DashboardLocationState | null) ?? null,
  );
  const [copied, setCopied] = useState(false);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['careers', user?.did],
    queryFn: () => getCareersByDid(user!.did),
    enabled: Boolean(user?.did),
  });

  if (!user) return null;

  // BE careers API는 status와 무관하게 모든 자격증명을 반환한다 → 화면에서 status로 구분
  const credentials = data?.verified_credentials ?? [];
  const verifiedCredentials = credentials.filter((c) => c.status === 'verified');
  const verifiedCount = verifiedCredentials.length;
  const issuerCount = new Set(verifiedCredentials.map((c) => c.issuer)).size;

  async function copyDid() {
    await navigator.clipboard.writeText(user!.did);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
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
            : '로그인되었습니다.'}
        </Alert>
      )}
      <section className="flex flex-col gap-1 px-1">
        <h1 className="text-[26px] font-bold leading-snug tracking-tight">
          {user.name} 님의 자격증명
        </h1>
        <button
          type="button"
          onClick={copyDid}
          className="group flex w-fit max-w-full items-center gap-2 text-left text-[13px] text-[var(--color-text-muted)]"
          title="클릭하여 DID 복사"
        >
          <span className="font-mono-did truncate">{user.did}</span>
          <span className="shrink-0 font-medium text-[var(--color-primary)] group-hover:underline">
            {copied ? '복사됨' : '복사'}
          </span>
        </button>
      </section>

      <Card className="flex flex-col gap-5">
        <div className="grid grid-cols-2 gap-4">
          <Stat label="검증된 자격증명" value={verifiedCount} unit="개" />
          <Stat label="발급 기관" value={issuerCount} unit="곳" />
        </div>
        <Link to="/issue" className="block">
          <Button size="lg" className="w-full">
            새 자격증명 발급받기
          </Button>
        </Link>
      </Card>

      {isError && <CareersErrorAlert error={error} onRetry={() => refetch()} />}

      <Card className="px-0 py-4">
        <div className="flex items-baseline justify-between px-6 pb-2">
          <h2 className="text-[17px] font-bold">전체</h2>
          {credentials.length > 0 && (
            <span className="text-[13px] text-[var(--color-text-muted)]">{credentials.length}개</span>
          )}
        </div>

        {isLoading && (
          <p className="px-6 py-8 text-center text-[15px] text-[var(--color-text-muted)]">불러오는 중...</p>
        )}

        {data && credentials.length === 0 && (
          <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
            <p className="text-[15px] font-semibold">아직 저장된 자격증명이 없어요</p>
            <p className="text-[13px] text-[var(--color-text-muted)]">
              모의 발급 서버에서 첫 배지를 받아보세요.
            </p>
            <Link to="/issue" className="mt-1">
              <Button variant="secondary" size="sm">
                첫 자격증명 발급받기
              </Button>
            </Link>
          </div>
        )}

        {credentials.length > 0 && (
          <ul>
            {credentials.map((credential) => (
              <li
                key={credential.record_id}
                className="flex items-center gap-4 px-6 py-3.5 transition-colors hover:bg-[var(--color-card-hover)]"
              >
                <IssuerAvatar issuer={credential.issuer} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[16px] font-semibold">{credential.achievement_name}</p>
                  <p className="truncate text-[13px] text-[var(--color-text-muted)]">
                    {credential.issuer} · {new Date(credential.issued_at).toLocaleDateString('ko-KR')}
                  </p>
                </div>
                <StatusBadge status={credential.status} />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

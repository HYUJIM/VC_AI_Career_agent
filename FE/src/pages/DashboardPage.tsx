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

function StatCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card className="flex flex-col gap-2 p-5">
      <span className="text-xs text-[var(--color-text-muted)]">{label}</span>
      <span className="text-3xl font-bold">{value}</span>
      {hint && <span className="text-xs text-[var(--color-text-muted)]">{hint}</span>}
    </Card>
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
  const validity =
    credentials.length > 0 ? Math.round((verifiedCount / credentials.length) * 100) : 0;

  async function copyDid() {
    await navigator.clipboard.writeText(user!.did);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="flex flex-col gap-6">
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
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="text-xs font-semibold tracking-widest text-[var(--color-primary)]">
            DECENTRALIZED VAULT
          </span>
          <h1 className="mt-1 text-3xl font-bold">나의 DID 검증 자격 및 인증서</h1>
          <button
            type="button"
            onClick={copyDid}
            className="font-mono-did mt-2 break-all text-left text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            title="클릭하여 DID 복사"
          >
            {user.did} <span className="text-[var(--color-primary)]">{copied ? '✓ 복사됨' : '복사'}</span>
          </button>
        </div>
        <Link to="/issue">
          <Button>+ 새 자격증명 발급받기</Button>
        </Link>
      </div>

      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard label="검증 완료 자격" value={`${verifiedCount}`} hint={`/ ${credentials.length} Verified VC`} />
        <StatCard label="인가 발급 기관" value={`${issuerCount}`} hint="신뢰된 발급처" />
        <StatCard label="위변조 검증 무결성" value={`${validity}%`} hint="검증 상태 기준" />
      </div>

      {isLoading && <Card>불러오는 중...</Card>}

      {isError && <CareersErrorAlert error={error} onRetry={() => refetch()} />}

      {data && credentials.length === 0 && (
        <Card className="flex flex-col items-start gap-3">
          <p className="text-sm text-[var(--color-text-muted)]">아직 지갑에 저장된 자격증명이 없습니다.</p>
          <Link to="/issue">
            <Button variant="secondary">첫 자격증명 발급받기</Button>
          </Link>
        </Card>
      )}

      {credentials.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {credentials.map((credential) => (
            <Card
              key={credential.record_id}
              accent
              className="flex flex-col gap-3 transition-colors hover:bg-[var(--color-card-hover)]"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-xs text-[var(--color-text-muted)]">{credential.issuer}</p>
                <StatusBadge status={credential.status} />
              </div>
              <h2 className="text-lg font-semibold">{credential.achievement_name}</h2>
              <dl className="mt-1 flex flex-col gap-1 border-t border-[var(--color-border)] pt-3 text-xs">
                <div className="flex justify-between gap-2">
                  <dt className="text-[var(--color-text-muted)]">발급일</dt>
                  <dd>{new Date(credential.issued_at).toLocaleDateString('ko-KR')}</dd>
                </div>
                <div className="flex justify-between gap-2">
                  <dt className="text-[var(--color-text-muted)]">레코드 ID</dt>
                  <dd className="font-mono-did truncate">{credential.record_id}</dd>
                </div>
              </dl>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

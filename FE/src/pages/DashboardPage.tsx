import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { getCareersByDid } from '@/api/credentials';
import { StatusBadge } from '@/components/StatusBadge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { getApiErrorMessage, getApiErrorStatus } from '@/lib/apiClient';
import { useAuthStore } from '@/store/authStore';

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
  const logout = useAuthStore((state) => state.logout);
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
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
  const userNotFound = isError && getApiErrorStatus(error) === 404;
  const validity =
    credentials.length > 0 ? Math.round((verifiedCount / credentials.length) * 100) : 0;

  function handleRelogin() {
    logout();
    navigate('/login');
  }

  async function copyDid() {
    await navigator.clipboard.writeText(user!.did);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="flex flex-col gap-6">
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

      {isError && (
        <Card className="border-[var(--color-danger)]">
          <p className="text-sm text-[var(--color-danger)]">{getApiErrorMessage(error)}</p>
          {userNotFound ? (
            <div className="mt-3 flex flex-col items-start gap-2">
              <p className="text-xs text-[var(--color-text-muted)]">
                저장된 로그인 정보의 DID가 서버에 없습니다. (DB가 초기화되었을 수 있습니다)
              </p>
              <Button variant="secondary" onClick={handleRelogin}>
                다시 로그인
              </Button>
            </div>
          ) : (
            <p className="mt-2 text-xs text-[var(--color-text-muted)]">
              BE 서버(http://localhost:3000)가 실행 중인지 확인해주세요.
            </p>
          )}
        </Card>
      )}

      {data && credentials.length === 0 && (
        <Card>
          <p className="text-sm text-[var(--color-text-muted)]">아직 검증된 자격증명이 없습니다.</p>
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

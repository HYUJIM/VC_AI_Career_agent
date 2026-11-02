import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getCareersByDid } from '@/api/credentials';
import { StatusBadge } from '@/components/StatusBadge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { getApiErrorMessage } from '@/lib/apiClient';
import { useAuthStore } from '@/store/authStore';

export function DashboardPage() {
  const user = useAuthStore((state) => state.user);

  const {
    data,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['careers', user?.did],
    queryFn: () => getCareersByDid(user!.did),
    enabled: Boolean(user?.did),
  });

  if (!user) return null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">내 자격증명 지갑</h1>
          <p className="mt-1 break-all text-sm text-[var(--color-text-muted)]">DID: {user.did}</p>
        </div>
        <Link to="/issue">
          <Button>자격증명 발급받기</Button>
        </Link>
      </div>

      {isLoading && <Card>불러오는 중...</Card>}

      {isError && (
        <Card className="border-[var(--color-danger)]">
          <p className="text-sm text-[var(--color-danger)]">{getApiErrorMessage(error)}</p>
          <p className="mt-2 text-xs text-[var(--color-text-muted)]">
            아직 검증된 자격증명이 없다면 먼저 "자격증명 발급받기"를 통해 배지를 발급받고
            검증하세요.
          </p>
        </Card>
      )}

      {data && data.verified_credentials.length === 0 && (
        <Card>
          <p className="text-sm text-[var(--color-text-muted)]">
            아직 검증된 자격증명이 없습니다.
          </p>
        </Card>
      )}

      {data && data.verified_credentials.length > 0 && (
        <div className="flex flex-col gap-4">
          {data.verified_credentials.map((credential) => (
            <Card key={credential.record_id} className="flex flex-col gap-2">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="font-semibold">{credential.achievement_name}</h2>
                  <p className="text-sm text-[var(--color-text-muted)]">{credential.issuer}</p>
                </div>
                <StatusBadge status={credential.status} />
              </div>
              <p className="text-xs text-[var(--color-text-muted)]">
                발급일: {new Date(credential.issued_at).toLocaleString('ko-KR')}
              </p>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}

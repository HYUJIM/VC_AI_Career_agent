import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { getMe } from '@/api/auth';
import { IssuerAvatar } from '@/components/IssuerAvatar';
import { StatusBadge } from '@/components/StatusBadge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { useCopy } from '@/lib/useCopy';
import type { UserProfile, VerifiedCredential } from '@/types/api';

const formatDate = (iso: string) => new Date(iso).toLocaleDateString('ko-KR');

export function ProfileCard({ user }: { user: UserProfile }) {
  const { copy, isCopied } = useCopy();
  // BE v0.5.0: 관리형 지갑(홀더 키 보관) 여부. 실패해도 프로필 표시에는 영향 없음.
  const { data: me } = useQuery({ queryKey: ['me', user.did], queryFn: getMe, retry: false });
  const custody = me?.wallet.custody;
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <IssuerAvatar issuer={user.name} size="lg" />
        <div className="min-w-0">
          <p className="truncate text-[17px] font-bold">{user.name}</p>
          <p className="truncate text-[13px] text-[var(--color-text-muted)]">{user.email}</p>
        </div>
      </div>
      <div className="rounded-2xl bg-[var(--color-surface)] px-4 py-3">
        <div className="flex items-center justify-between">
          <span className="text-[13px] text-[var(--color-text-muted)]">내 DID</span>
          <button
            type="button"
            onClick={() => copy(user.did)}
            className="text-[13px] font-medium text-[var(--color-primary)] hover:underline"
          >
            {isCopied() ? '복사됨' : '복사'}
          </button>
        </div>
        <p className="font-mono-did mt-1 break-all text-[12px] text-[var(--color-text-sub)]">{user.did}</p>
        {custody && (
          <p className="mt-2 text-[12px] text-[var(--color-text-muted)]">
            {custody === 'managed'
              ? `관리형 지갑 · 서버가 ${me?.wallet.key_type ?? ''} 키를 암호화 보관해요`
              : '홀더 키 없는 계정 (이전 방식 DID)'}
          </p>
        )}
      </div>
    </Card>
  );
}

export function QuickActionsCard() {
  return (
    <Card className="flex flex-col gap-2 p-4">
      <h2 className="px-2 pb-1 text-[15px] font-bold">바로가기</h2>
      <Link to="/issue">
        <Button size="lg" className="w-full">
          새 자격증명 발급받기
        </Button>
      </Link>
      <Link to="/portfolio">
        <Button variant="secondary" size="lg" className="w-full">
          AI 포트폴리오 만들기
        </Button>
      </Link>
    </Card>
  );
}

/** 검증된 자격증명 기준 발급 기관별 개수 막대 */
export function IssuerStatsCard({ stats }: { stats: [issuer: string, count: number][] }) {
  const max = stats[0]?.[1] ?? 1;
  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[15px] font-bold">발급 기관</h2>
        <span className="text-[13px] text-[var(--color-text-muted)]">검증됨 기준 {stats.length}곳</span>
      </div>
      {stats.length === 0 ? (
        <p className="text-[13px] text-[var(--color-text-muted)]">검증된 자격증명이 생기면 표시돼요.</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {stats.map(([issuer, count]) => (
            <li key={issuer} className="flex flex-col gap-1">
              <div className="flex justify-between gap-2 text-[13px]">
                <span className="truncate text-[var(--color-text-sub)]">{issuer}</span>
                <span className="shrink-0 font-semibold">{count}</span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-surface)]">
                <div
                  className="h-full rounded-full bg-[var(--color-primary)]"
                  style={{ width: `${(count / max) * 100}%` }}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** 발급일 기준 최근 5건 타임라인 (newestFirst는 이미 최신순 정렬된 목록) */
export function RecentActivityCard({
  newestFirst,
  onSelect,
}: {
  newestFirst: VerifiedCredential[];
  onSelect: (c: VerifiedCredential) => void;
}) {
  return (
    <Card className="flex flex-col gap-3">
      <h2 className="text-[15px] font-bold">최근 활동</h2>
      {newestFirst.length === 0 ? (
        <p className="text-[13px] text-[var(--color-text-muted)]">아직 활동 내역이 없어요.</p>
      ) : (
        <ol className="flex flex-col gap-4 border-l-2 border-[var(--color-border)] pl-4">
          {newestFirst.slice(0, 5).map((c) => (
            <li key={c.record_id} className="relative">
              <span
                aria-hidden="true"
                className={`absolute top-1.5 -left-[23px] h-3 w-3 rounded-full border-2 border-white ${
                  c.status === 'verified' ? 'bg-[var(--color-success)]' : 'bg-[var(--color-text-muted)]'
                }`}
              />
              <button type="button" onClick={() => onSelect(c)} className="flex w-full flex-col gap-1 text-left">
                <span className="w-full truncate text-[14px] font-semibold hover:underline">
                  {c.achievement_name}
                </span>
                <span className="flex items-center gap-1.5 text-[12px] text-[var(--color-text-muted)]">
                  {formatDate(c.issued_at)} 발급
                  <StatusBadge status={c.status} />
                </span>
              </button>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

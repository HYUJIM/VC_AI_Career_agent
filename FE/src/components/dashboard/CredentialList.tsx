import { AnimatePresence, motion } from 'motion/react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { IssuerAvatar } from '@/components/IssuerAvatar';
import { StatusBadge } from '@/components/StatusBadge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { STATUS_FILTER_TABS, type StatusFilter, getStatusGroup } from '@/lib/credentialStatus';
import type { VerifiedCredential } from '@/types/api';

type SortKey = 'newest' | 'oldest' | 'issuer';

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: 'newest', label: '최신순' },
  { key: 'oldest', label: '오래된순' },
  { key: 'issuer', label: '기관명순' },
];

interface Props {
  credentials: VerifiedCredential[];
  counts: Record<StatusFilter, number>;
  isLoading: boolean;
  /** 데이터 요청이 성공했는지 (빈 목록 안내 표시 여부) */
  loaded: boolean;
  onSelect: (c: VerifiedCredential) => void;
}

export function CredentialList({ credentials, counts, isLoading, loaded, onSelect }: Props) {
  const [filter, setFilter] = useState<StatusFilter>('all');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortKey>('newest');

  const visible = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = credentials.filter(
      (c) =>
        (filter === 'all' || getStatusGroup(c.status) === filter) &&
        (!q || c.achievement_name.toLowerCase().includes(q) || c.issuer.toLowerCase().includes(q)),
    );
    return list.sort((a, b) => {
      if (sort === 'issuer') return a.issuer.localeCompare(b.issuer, 'ko');
      const diff = +new Date(b.issued_at) - +new Date(a.issued_at);
      return sort === 'newest' ? diff : -diff;
    });
  }, [credentials, filter, search, sort]);

  return (
    <Card className="flex flex-col gap-3 px-0 py-4">
      <div className="flex gap-1 overflow-x-auto px-4" role="tablist" aria-label="상태 필터">
        {STATUS_FILTER_TABS.map((tab) => {
          const active = filter === tab.key;
          return (
            <button
              key={tab.key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setFilter(tab.key)}
              className={`relative flex shrink-0 items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[14px] transition-colors ${
                active
                  ? 'font-semibold text-white'
                  : 'text-[var(--color-text-sub)] hover:bg-[var(--color-surface)]'
              }`}
            >
              {/* Motion layoutId: 선택된 필터 배경이 탭 사이를 이동한다 */}
              {active && (
                <motion.span
                  layoutId="status-filter-pill"
                  aria-hidden="true"
                  className="absolute inset-0 rounded-full bg-[var(--color-text)]"
                  transition={{ type: 'spring', stiffness: 500, damping: 38 }}
                />
              )}
              <span className="relative">{tab.label}</span>
              <span className={`relative text-[12px] ${active ? 'text-white/70' : 'text-[var(--color-text-muted)]'}`}>
                {counts[tab.key]}
              </span>
            </button>
          );
        })}
      </div>

      <div className="flex gap-2 px-4">
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="배지 이름 또는 발급 기관 검색"
          aria-label="자격증명 검색"
          className="h-10 min-w-0 flex-1 rounded-xl border-2 border-transparent bg-[var(--color-surface)] px-3.5 text-[14px] outline-none placeholder:text-[#b0b8c1] focus:border-[var(--color-primary)] focus:bg-white"
        />
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          aria-label="정렬"
          className="h-10 shrink-0 rounded-xl bg-[var(--color-surface)] px-3 text-[14px] text-[var(--color-text-sub)] outline-none"
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      {isLoading && (
        <p className="px-6 py-8 text-center text-[15px] text-[var(--color-text-muted)]">불러오는 중...</p>
      )}

      {loaded && credentials.length === 0 && (
        <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
          <p className="text-[15px] font-semibold">아직 저장된 자격증명이 없어요</p>
          <p className="text-[13px] text-[var(--color-text-muted)]">모의 발급 서버에서 첫 배지를 받아보세요.</p>
          <Link to="/issue" className="mt-1">
            <Button variant="secondary" size="sm">
              첫 자격증명 발급받기
            </Button>
          </Link>
        </div>
      )}

      {credentials.length > 0 && visible.length === 0 && (
        <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
          <p className="text-[15px] font-semibold">조건에 맞는 자격증명이 없어요</p>
          <button
            type="button"
            onClick={() => {
              setFilter('all');
              setSearch('');
            }}
            className="text-[13px] font-medium text-[var(--color-primary)] hover:underline"
          >
            필터 초기화
          </button>
        </div>
      )}

      {visible.length > 0 && (
        <ul>
          {/* Motion: 필터·정렬이 바뀌면 항목이 자리를 옮기고, 새 항목은 순서대로 떠오른다 */}
          <AnimatePresence mode="popLayout">
          {visible.map((credential, i) => (
            <motion.li
              key={credential.record_id}
              layout
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2, delay: Math.min(i, 10) * 0.03 }}
            >
              <button
                type="button"
                onClick={() => onSelect(credential)}
                className="flex w-full items-center gap-4 px-6 py-3.5 text-left transition-colors hover:bg-[var(--color-card-hover)]"
              >
                <IssuerAvatar issuer={credential.issuer} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[16px] font-semibold">{credential.achievement_name}</p>
                  <p className="truncate text-[13px] text-[var(--color-text-muted)]">
                    {credential.issuer} · {new Date(credential.issued_at).toLocaleDateString('ko-KR')}
                  </p>
                </div>
                <StatusBadge status={credential.status} />
                <span aria-hidden="true" className="text-[18px] text-[var(--color-text-muted)]">
                  ›
                </span>
              </button>
            </motion.li>
          ))}
          </AnimatePresence>
        </ul>
      )}
    </Card>
  );
}

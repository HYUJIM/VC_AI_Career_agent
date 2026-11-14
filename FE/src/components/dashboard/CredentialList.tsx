import { AnimatePresence, motion } from 'motion/react';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { IssuerAvatar } from '@/components/IssuerAvatar';
import { StatusBadge } from '@/components/StatusBadge';
import { BatchReverifyModal } from '@/components/dashboard/BatchReverifyModal';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { STATUS_FILTER_TABS, type StatusFilter, getStatusGroup } from '@/lib/credentialStatus';
import type { VerifiedCredential } from '@/types/api';

type SortKey = 'newest' | 'oldest' | 'issuer';
const PAGE_SIZE = 10;

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
  const [selectMode, setSelectMode] = useState(false);
  const [checkedIds, setCheckedIds] = useState<Set<string>>(new Set());
  const [batchItems, setBatchItems] = useState<VerifiedCredential[]>([]);
  const [batchOpen, setBatchOpen] = useState(false);
  const [page, setPage] = useState(1);

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

  const totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pagedVisible = useMemo(
    () => visible.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE),
    [visible, currentPage],
  );

  function toggleCheck(id: string) {
    setCheckedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function startBatchReverify() {
    const targets = credentials.filter((c) => checkedIds.has(c.record_id));
    if (targets.length === 0) return;
    setBatchItems(targets);
    setBatchOpen(true);
  }

  return (
    <Card className="flex flex-col gap-3 px-0 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2 px-4">
        <div className="flex gap-1 overflow-x-auto" role="tablist" aria-label="상태 필터">
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

        {credentials.length > 0 && (
          <Button
            variant={selectMode ? 'secondary' : 'tertiary'}
            size="sm"
            onClick={() => {
              setSelectMode((prev) => !prev);
              setCheckedIds(new Set());
            }}
          >
            {selectMode ? '취소' : '선택'}
          </Button>
        )}
      </div>

      {selectMode && credentials.length > 0 && (
        <div className="mx-4 flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-[var(--color-surface)] px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-semibold text-[var(--color-text)]">
              {checkedIds.size}개 선택됨
            </span>
            <Button
              variant="tertiary"
              size="sm"
              onClick={() => setCheckedIds(new Set(visible.map((c) => c.record_id)))}
            >
              현재 목록 전체 선택
            </Button>
            {checkedIds.size > 0 && (
              <Button variant="tertiary" size="sm" onClick={() => setCheckedIds(new Set())}>
                전체 해제
              </Button>
            )}
          </div>
          <Button size="sm" disabled={checkedIds.size === 0} onClick={startBatchReverify}>
            선택한 {checkedIds.size}개 동시 재검증
          </Button>
        </div>
      )}

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

      <div className="flex min-h-[710px] flex-col">
        {isLoading && (
          <div className="flex flex-1 items-center justify-center px-6 py-8 text-center">
            <p className="text-[15px] text-[var(--color-text-muted)]">불러오는 중...</p>
          </div>
        )}

        {loaded && credentials.length === 0 && (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 py-10 text-center">
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
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 py-10 text-center">
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
          <AnimatePresence mode="wait" initial={false}>
            <motion.ul
              key={`${filter}-${sort}-${currentPage}`}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -4 }}
              transition={{ duration: 0.16, ease: 'easeOut' }}
              className="flex-1"
            >
              {pagedVisible.map((credential) => (
                <li key={credential.record_id}>
                  <button
                    type="button"
                    onClick={() => (selectMode ? toggleCheck(credential.record_id) : onSelect(credential))}
                    className="flex w-full items-center gap-4 px-6 py-3.5 text-left transition-colors hover:bg-[var(--color-card-hover)]"
                  >
                    {selectMode && (
                      <input
                        type="checkbox"
                        checked={checkedIds.has(credential.record_id)}
                        onChange={() => toggleCheck(credential.record_id)}
                        onClick={(e) => e.stopPropagation()}
                        className="h-5 w-5 shrink-0 accent-[var(--color-primary)]"
                      />
                    )}
                    <IssuerAvatar issuer={credential.issuer} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[16px] font-semibold">{credential.achievement_name}</p>
                      <p className="truncate text-[13px] text-[var(--color-text-muted)]">
                        {credential.issuer} · {new Date(credential.issued_at).toLocaleDateString('ko-KR')}
                      </p>
                    </div>
                    <StatusBadge status={credential.status} />
                    {!selectMode && (
                      <span aria-hidden="true" className="text-[18px] text-[var(--color-text-muted)]">
                        ›
                      </span>
                    )}
                  </button>
                </li>
              ))}
            </motion.ul>
          </AnimatePresence>
        )}
      </div>

      <div className="mt-1 flex flex-wrap items-center justify-between gap-3 border-t border-[var(--color-border)] px-6 pt-4">
        <Button
          variant="secondary"
          size="md"
          disabled={currentPage <= 1}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
        >
          ‹ 이전
        </Button>

        <div className="flex items-center gap-1.5">
          {Array.from({ length: totalPages }, (_, idx) => idx + 1).map((pageNum) => {
            const active = pageNum === currentPage;
            return (
              <button
                key={pageNum}
                type="button"
                onClick={() => setPage(pageNum)}
                className={`flex h-10 min-w-10 items-center justify-center rounded-xl px-3 text-[14px] font-bold transition-colors ${
                  active
                    ? 'bg-[var(--color-primary)] text-white shadow-sm'
                    : 'bg-[var(--color-surface)] text-[var(--color-text-sub)] hover:bg-[var(--color-primary-weak)] hover:text-[var(--color-primary)]'
                }`}
              >
                {pageNum}
              </button>
            );
          })}
        </div>

        <Button
          variant="secondary"
          size="md"
          disabled={currentPage >= totalPages}
          onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
        >
          다음 ›
        </Button>
      </div>

      {batchOpen && (
        <BatchReverifyModal
          open={batchOpen}
          items={batchItems}
          onClose={() => {
            setBatchOpen(false);
            setSelectMode(false);
            setCheckedIds(new Set());
          }}
        />
      )}
    </Card>
  );
}

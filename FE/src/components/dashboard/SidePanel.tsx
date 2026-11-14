import { useQuery } from '@tanstack/react-query';
import { animate, stagger } from 'animejs';
import { AnimatePresence, motion } from 'motion/react';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getMe } from '@/api/auth';
import { IssuerAvatar } from '@/components/IssuerAvatar';
import { StatusBadge } from '@/components/StatusBadge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Modal } from '@/components/ui/Modal';
import { prefersReducedMotion } from '@/lib/motion';
import { useCopy } from '@/lib/useCopy';
import type { UserProfile, VerifiedCredential } from '@/types/api';

const formatDate = (iso: string) => new Date(iso).toLocaleDateString('ko-KR');

export function ProfileCard({ user }: { user: UserProfile }) {
  const { copy, isCopied } = useCopy();
  // BE v0.5.0+: 관리형 지갑(홀더 키 보관) 여부 및 연동된 소셜 로그인(oauth_providers). 실패해도 프로필 표시에는 영향 없음.
  const { data: me } = useQuery({ queryKey: ['me', user.did], queryFn: getMe, retry: false });
  const custody = me?.wallet.custody;
  const googleLinked = Boolean(me?.oauth_providers?.includes('google'));
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <IssuerAvatar issuer={user.name} size="lg" />
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <p className="truncate text-[17px] font-bold">{user.name}</p>
            {googleLinked && (
              <span className="rounded-md bg-[var(--color-primary-weak)] px-1.5 py-0.5 text-[11px] font-semibold text-[var(--color-primary)]">
                Google 연동됨
              </span>
            )}
          </div>
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
    <Card className="flex flex-col gap-2">
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

const COLLAPSED_COUNT = 3;

/** 검증된 자격증명 기준 발급 기관별 개수 막대 (접기/펼치기 애니메이션 + 전체 모달 지원) */
export function IssuerStatsCard({ stats }: { stats: [issuer: string, count: number][] }) {
  const max = stats[0]?.[1] ?? 1;
  const listRef = useRef<HTMLUListElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const hasMore = stats.length > COLLAPSED_COUNT;
  // 통계가 바뀔 때만 다시 그린다
  const statsKey = stats.map(([issuer, count]) => `${issuer}:${count}`).join('|');

  // Anime.js: 막대가 왼쪽에서 차례로 차오른다
  useEffect(() => {
    const bars = listRef.current?.querySelectorAll<HTMLElement>('[data-bar]');
    if (!bars?.length || prefersReducedMotion()) return;
    const anim = animate(bars, {
      scaleX: [0, 1],
      duration: 600,
      delay: stagger(60),
      ease: 'outCubic',
    });
    return () => {
      anim.revert();
    };
  }, [statsKey]);

  return (
    <Card className="flex flex-col gap-3">
      <button
        type="button"
        disabled={!hasMore}
        onClick={() => setExpanded((prev) => !prev)}
        className={`flex w-full items-center justify-between text-left ${hasMore ? 'cursor-pointer' : 'cursor-default'}`}
      >
        <div className="flex items-baseline gap-2">
          <h2 className="text-[15px] font-bold">발급 기관</h2>
          <span className="text-[12px] text-[var(--color-text-muted)]">검증됨 기준 {stats.length}곳</span>
        </div>
        {hasMore && (
          <motion.span
            animate={{ rotate: expanded ? 90 : 0 }}
            transition={{ duration: 0.2 }}
            className="inline-block text-[18px] font-semibold leading-none text-[var(--color-text-muted)]"
          >
            ›
          </motion.span>
        )}
      </button>

      {stats.length === 0 ? (
        <p className="text-[13px] text-[var(--color-text-muted)]">검증된 자격증명이 생기면 표시돼요.</p>
      ) : (
        <>
          <ul ref={listRef} className="flex flex-col gap-2.5">
            {stats.slice(0, COLLAPSED_COUNT).map(([issuer, count]) => (
              <li key={issuer} className="flex flex-col gap-1">
                <div className="flex justify-between gap-2 text-[13px]">
                  <span className="truncate text-[var(--color-text-sub)]">{issuer}</span>
                  <span className="shrink-0 font-semibold">{count}</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-surface)]">
                  <div
                    data-bar
                    className="h-full origin-left rounded-full bg-[var(--color-primary)]"
                    style={{ width: `${(count / max) * 100}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>

          <AnimatePresence initial={false}>
            {expanded && hasMore && (
              <motion.ul
                key="issuer-more"
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
                className="flex flex-col gap-2.5 overflow-hidden"
              >
                {stats.slice(COLLAPSED_COUNT).map(([issuer, count]) => (
                  <li key={issuer} className="flex flex-col gap-1">
                    <div className="flex justify-between gap-2 text-[13px]">
                      <span className="truncate text-[var(--color-text-sub)]">{issuer}</span>
                      <span className="shrink-0 font-semibold">{count}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-surface)]">
                      <div
                        className="h-full origin-left rounded-full bg-[var(--color-primary)]"
                        style={{ width: `${(count / max) * 100}%` }}
                      />
                    </div>
                  </li>
                ))}
              </motion.ul>
            )}
          </AnimatePresence>

          {hasMore && (
            <div className="flex items-center justify-between border-t border-[var(--color-border)] pt-2.5 text-[12px]">
              <button
                type="button"
                onClick={() => setExpanded((prev) => !prev)}
                className="font-semibold text-[var(--color-text-sub)] hover:text-[var(--color-text)]"
              >
                {expanded ? '간략히 접기' : `외 ${stats.length - COLLAPSED_COUNT}곳 더 펼치기`}
              </button>
              <button
                type="button"
                onClick={() => setModalOpen(true)}
                className="font-semibold text-[var(--color-primary)] hover:underline"
              >
                팝업으로 보기
              </button>
            </div>
          )}
        </>
      )}

      {modalOpen && (
        <Modal
          open={modalOpen}
          title={`발급 기관 현황 (총 ${stats.length}곳)`}
          onClose={() => setModalOpen(false)}
          footer={
            <Button className="w-full" onClick={() => setModalOpen(false)}>
              닫기
            </Button>
          }
        >
          <ul className="flex flex-col gap-3">
            {stats.map(([issuer, count]) => (
              <li key={issuer} className="flex flex-col gap-1.5">
                <div className="flex justify-between gap-2 text-[14px]">
                  <span className="font-medium text-[var(--color-text)]">{issuer}</span>
                  <span className="shrink-0 font-bold text-[var(--color-primary)]">{count}개</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-[var(--color-surface)]">
                  <div
                    className="h-full rounded-full bg-[var(--color-primary)]"
                    style={{ width: `${(count / max) * 100}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        </Modal>
      )}
    </Card>
  );
}

/** 발급일 기준 최근 활동 타임라인 (평상시에는 제목만 접혀 있고 클릭 시 부드럽게 펼쳐짐 + 전체 모달 지원) */
export function RecentActivityCard({
  newestFirst,
  onSelect,
}: {
  newestFirst: VerifiedCredential[];
  onSelect: (c: VerifiedCredential) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const hasItems = newestFirst.length > 0;
  const visibleItems = newestFirst.slice(0, 8);

  return (
    <Card className="flex flex-col">
      <button
        type="button"
        disabled={!hasItems}
        onClick={() => setExpanded((prev) => !prev)}
        className={`flex w-full items-center justify-between text-left ${hasItems ? 'cursor-pointer' : 'cursor-default'}`}
      >
        <div className="flex items-baseline gap-2">
          <h2 className="text-[15px] font-bold">최근 활동</h2>
          <span className="text-[12px] text-[var(--color-text-muted)]">
            {hasItems ? `총 ${newestFirst.length}건 · ${expanded ? '접기' : '클릭해 펼치기'}` : '내역 없음'}
          </span>
        </div>
        {hasItems && (
          <motion.span
            animate={{ rotate: expanded ? 90 : 0 }}
            transition={{ duration: 0.2 }}
            className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--color-surface)] text-[18px] font-semibold leading-none text-[var(--color-text-sub)]"
          >
            ›
          </motion.span>
        )}
      </button>

      <AnimatePresence initial={false}>
        {expanded && hasItems && (
          <motion.div
            key="recent-activity-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
            className="overflow-hidden"
          >
            <div className="pt-4">
              <ol className="flex flex-col gap-3.5 border-l-2 border-[var(--color-border)] pl-4">
                {visibleItems.map((c) => (
                  <li key={c.record_id} className="relative">
                    <span
                      aria-hidden="true"
                      className={`absolute top-1.5 -left-[23px] h-3 w-3 rounded-full border-2 border-white ${
                        c.status === 'verified' ? 'bg-[var(--color-success)]' : 'bg-[var(--color-warning)]'
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

              <div className="mt-3 flex items-center justify-between border-t border-[var(--color-border)] pt-2.5 text-[12px]">
                <button
                  type="button"
                  onClick={() => setExpanded(false)}
                  className="font-semibold text-[var(--color-text-sub)] hover:text-[var(--color-text)]"
                >
                  접기
                </button>
                <button
                  type="button"
                  onClick={() => setModalOpen(true)}
                  className="font-semibold text-[var(--color-primary)] hover:underline"
                >
                  전체 팝업 보기 ({newestFirst.length}건)
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {modalOpen && (
        <Modal
          open={modalOpen}
          title={`전체 활동 타임라인 (${newestFirst.length}건)`}
          onClose={() => setModalOpen(false)}
          footer={
            <Button className="w-full" onClick={() => setModalOpen(false)}>
              닫기
            </Button>
          }
        >
          <ol className="flex flex-col gap-4 border-l-2 border-[var(--color-border)] py-1 pl-5">
            {newestFirst.map((c) => (
              <li key={c.record_id} className="relative">
                <span
                  aria-hidden="true"
                  className={`absolute top-1.5 -left-[27px] h-3.5 w-3.5 rounded-full border-2 border-white ${
                    c.status === 'verified' ? 'bg-[var(--color-success)]' : 'bg-[var(--color-warning)]'
                  }`}
                />
                <button
                  type="button"
                  onClick={() => {
                    setModalOpen(false);
                    onSelect(c);
                  }}
                  className="flex w-full items-center justify-between gap-3 rounded-xl px-2 py-1.5 text-left hover:bg-[var(--color-surface)]"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[15px] font-semibold">{c.achievement_name}</p>
                    <p className="truncate text-[12px] text-[var(--color-text-muted)]">
                      {c.issuer} · {formatDate(c.issued_at)} 발급
                    </p>
                  </div>
                  <StatusBadge status={c.status} />
                </button>
              </li>
            ))}
          </ol>
        </Modal>
      )}
    </Card>
  );
}

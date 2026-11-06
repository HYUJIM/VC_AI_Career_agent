import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { reverifyCredential } from '@/api/credentials';
import { StatusBadge } from '@/components/StatusBadge';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { getApiErrorMessage } from '@/lib/apiClient';
import type { CredentialStatus, VerifiedCredential } from '@/types/api';

type ItemState =
  | { phase: 'waiting' }
  | { phase: 'running' }
  | { phase: 'done'; verified: boolean; status: CredentialStatus; reason: string | null }
  | { phase: 'error'; message: string };

const REASON_TEXT: Record<string, string> = {
  no_signed_original: '서명 원본이 없어 다시 검증할 수 없음',
  revoked: '발급 기관에서 취소됨',
  expired: '유효기간 만료',
  invalid: '서명 또는 형식 불일치',
  unverifiable: '검증 불가',
};

function failReason(status: string, verifiedAt: string | null): string {
  if (!verifiedAt) return REASON_TEXT.no_signed_original;
  return REASON_TEXT[status] ?? `검증 실패 (${status})`;
}

interface Props {
  open: boolean;
  items: VerifiedCredential[];
  onClose: () => void;
}

export function BatchReverifyModal({ open, items, onClose }: Props) {
  const queryClient = useQueryClient();
  const [states, setStates] = useState<Record<string, ItemState>>(() =>
    Object.fromEntries(items.map((item) => [item.record_id, { phase: 'waiting' as const }])),
  );
  const [running, setRunning] = useState(() => items.length > 0);

  useEffect(() => {
    if (!open || items.length === 0) return;
    let cancelled = false;

    (async () => {
      for (const item of items) {
        if (cancelled) break;
        setStates((prev) => ({ ...prev, [item.record_id]: { phase: 'running' } }));
        try {
          const res = await reverifyCredential(item.record_id);
          if (cancelled) break;
          const ev = res.data;
          setStates((prev) => ({
            ...prev,
            [item.record_id]: {
              phase: 'done',
              verified: ev.verified,
              status: ev.status,
              reason: ev.verified ? null : failReason(ev.status, ev.verified_at),
            },
          }));
        } catch (err) {
          if (cancelled) break;
          setStates((prev) => ({
            ...prev,
            [item.record_id]: { phase: 'error', message: getApiErrorMessage(err) },
          }));
        }
      }
      if (!cancelled) {
        setRunning(false);
        queryClient.invalidateQueries({ queryKey: ['careers'] });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [open, items, queryClient]);

  const values = Object.values(states);
  const completedCount = values.filter((s) => s.phase === 'done' || s.phase === 'error').length;
  const passCount = values.filter((s) => s.phase === 'done' && s.verified).length;
  const failCount = values.filter((s) => (s.phase === 'done' && !s.verified) || s.phase === 'error').length;
  const progressPct = items.length > 0 ? Math.round((completedCount / items.length) * 100) : 0;

  return (
    <Modal
      open={open}
      title={running ? `실시간 자격증명 재검증 중 (${completedCount}/${items.length})` : '자격증명 일괄 재검증 완료'}
      onClose={running ? () => {} : onClose}
      footer={
        <Button className="w-full" disabled={running} onClick={onClose}>
          {running ? '검증 진행 중...' : '확인 (지갑 상태 반영됨)'}
        </Button>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between text-[13px]">
            <span className="font-medium text-[var(--color-text-sub)]">
              {running ? '서명 원본을 순서대로 대조하고 있어요...' : `총 ${items.length}개 재검증이 완료되었습니다.`}
            </span>
            <span className="font-semibold text-[var(--color-primary)]">{progressPct}%</span>
          </div>
          <div className="h-2.5 w-full overflow-hidden rounded-full bg-[var(--color-surface)]">
            <div
              className="h-full rounded-full bg-[var(--color-primary)] transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <div className="flex gap-3 pt-1 text-[13px]">
            <span className="font-semibold text-[#029359]">통과 {passCount}건</span>
            <span className="font-semibold text-[#d22030]">실패·확인 필요 {failCount}건</span>
            <span className="text-[var(--color-text-muted)]">
              대기 {Math.max(0, items.length - completedCount)}건
            </span>
          </div>
        </div>

        {!running && failCount > 0 && (
          <Alert variant="warning" title={`${failCount}건의 자격증명이 '확인 필요' 탭으로 이동했습니다.`}>
            서명 원본이 없거나 폐기·만료된 자격증명은 지갑 상태가 즉시 갱신되어 포트폴리오 근거에서 제외됩니다.
          </Alert>
        )}
        {!running && failCount === 0 && items.length > 0 && (
          <Alert variant="success" title="선택한 모든 자격증명이 지금도 유효합니다.">
            모든 서명 원본 검증을 통과했습니다.
          </Alert>
        )}

        <ul className="flex max-h-80 flex-col divide-y divide-[var(--color-border)] overflow-y-auto rounded-2xl border border-[var(--color-border)]">
          {items.map((item) => {
            const st = states[item.record_id] ?? { phase: 'waiting' };
            return (
              <li key={item.record_id} className="flex items-center justify-between gap-3 px-4 py-3 text-[14px]">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-semibold">{item.achievement_name}</p>
                  <p className="truncate text-[12px] text-[var(--color-text-muted)]">
                    {st.phase === 'waiting' && '검증 대기 중'}
                    {st.phase === 'running' && '서명 원본 검증 중...'}
                    {st.phase === 'done' && (st.verified ? '서명·발급자·폐기 여부 확인 완료' : st.reason)}
                    {st.phase === 'error' && st.message}
                  </p>
                </div>
                <div className="shrink-0">
                  {st.phase === 'waiting' && (
                    <span className="inline-flex items-center rounded-md bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium text-[var(--color-text-muted)]">
                      대기
                    </span>
                  )}
                  {st.phase === 'running' && (
                    <span className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-primary-weak)] px-2.5 py-0.5 text-xs font-semibold text-[var(--color-primary)]">
                      <span className="h-2 w-2 animate-ping rounded-full bg-[var(--color-primary)]" />
                      검증 중
                    </span>
                  )}
                  {st.phase === 'done' && (
                    <div className="flex items-center gap-1.5">
                      <span
                        className={`text-xs font-bold ${st.verified ? 'text-[#029359]' : 'text-[#d22030]'}`}
                      >
                        {st.verified ? '성공' : '실패'}
                      </span>
                      <StatusBadge status={st.status} />
                    </div>
                  )}
                  {st.phase === 'error' && (
                    <span className="inline-flex items-center rounded-md bg-[#ffeeee] px-2 py-0.5 text-xs font-semibold text-[#d22030]">
                      오류
                    </span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    </Modal>
  );
}

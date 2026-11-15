import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { isVerifierErrorResult, reverifyCredential } from '@/api/credentials';
import { StatusBadge } from '@/components/StatusBadge';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { getApiErrorKind, getApiErrorMessage, getApiErrorStatus } from '@/lib/apiClient';
import type { CredentialStatus, VerifiedCredential } from '@/types/api';

type ItemState =
  | { phase: 'waiting' }
  | { phase: 'running' }
  /** unchanged: 검증 서버가 이 자격증명만 확인하지 못해 지갑에 저장된 상태가 그대로인 결과 (BE reason verifier_error) */
  | { phase: 'done'; verified: boolean; status: CredentialStatus; reason: string | null; unchanged: boolean }
  | { phase: 'error'; message: string }
  /** 앞 항목이 서버 장애·요청 한도로 실패해 보내지 않은 항목 */
  | { phase: 'skipped' };

const REASON_TEXT: Record<string, string> = {
  no_signed_original: '서명 원본이 없어 다시 검증할 수 없음',
  revoked: '발급 기관에서 취소됨',
  expired: '유효기간 만료',
  invalid: '서명 또는 형식 불일치',
  unverifiable: '검증 불가',
  verifier_error: '검증 서버가 확인하지 못함 (지갑 상태는 그대로)',
};

function failReason(status: string, verifiedAt: string | null): string {
  if (!verifiedAt) return REASON_TEXT.no_signed_original;
  return Object.hasOwn(REASON_TEXT, status) ? REASON_TEXT[status] : `검증 실패 (${status})`;
}

/**
 * 남은 항목을 보내도 같은 이유로 실패할 오류인지: 연결 실패(응답 없음·본문 없는 5xx), BE·검증 서버 오류(5xx), 요청 한도 초과(429).
 * 이런 오류 뒤에도 계속 보내면 장애 중인 검증 서버에 선택한 개수만큼 요청이 더 쌓인다.
 */
function shouldStopBatch(err: unknown): boolean {
  const kind = getApiErrorKind(err);
  return kind === 'network' || kind === 'rateLimited' || (getApiErrorStatus(err) ?? 0) >= 500;
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
  /** 서버 장애·요청 한도로 실패한 항목에서 멈췄는지. 마지막(또는 하나뿐인) 항목에서 멈추면 '미실행' 항목이 없으므로 따로 기억한다. */
  const [stopped, setStopped] = useState(false);

  useEffect(() => {
    if (!open || items.length === 0) return;
    let cancelled = false;

    (async () => {
      for (const [index, item] of items.entries()) {
        if (cancelled) break;
        setStates((prev) => ({ ...prev, [item.record_id]: { phase: 'running' } }));
        try {
          const res = await reverifyCredential(item.record_id);
          if (cancelled) break;
          const ev = res?.data;
          // 200 이지만 재검증 응답이 아닌 본문(예: /api 요청이 SPA 의 index.html 로 넘어가는 배포 설정, 빈 객체)은 이 항목의 오류로
          // 처리한다. 그대로 읽으면 아래 상태 갱신(렌더링 중에 실행됨)에서 예외가 나 지갑 화면 전체가 오류 화면으로 바뀐다.
          if (typeof ev?.status !== 'string' || typeof ev.verified !== 'boolean') {
            throw new Error('서버 응답이 올바르지 않습니다.');
          }
          // 검증 서버가 이 자격증명 하나만 확인하지 못한 응답(200 + success:false): 실패 항목으로 표시하고 다음 항목을 계속 보낸다.
          const unchanged = isVerifierErrorResult(res);
          const failText = unchanged ? REASON_TEXT.verifier_error : failReason(ev.status, ev.verified_at);
          setStates((prev) => ({
            ...prev,
            [item.record_id]: {
              phase: 'done',
              verified: ev.verified,
              status: ev.status,
              reason: ev.verified ? null : failText,
              unchanged,
            },
          }));
        } catch (err) {
          if (cancelled) break;
          setStates((prev) => ({
            ...prev,
            [item.record_id]: { phase: 'error', message: getApiErrorMessage(err) },
          }));
          if (shouldStopBatch(err)) {
            // 여기서 멈추고 아직 보내지 않은 항목은 '미실행'으로 표시한다.
            const rest = items.slice(index + 1);
            setStates((prev) => ({
              ...prev,
              ...Object.fromEntries(rest.map((r) => [r.record_id, { phase: 'skipped' as const }])),
            }));
            setStopped(true);
            break;
          }
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
  // 검증 결과로 지갑의 상태가 정해진 실패만 센다. 요청 오류와 검증 서버가 확인하지 못한 항목은 저장된 상태가 그대로다.
  const movedCount = values.filter((s) => s.phase === 'done' && !s.verified && !s.unchanged).length;
  const skippedCount = values.filter((s) => s.phase === 'skipped').length;
  const progressPct = items.length > 0 ? Math.round((completedCount / items.length) * 100) : 0;

  return (
    <Modal
      open={open}
      title={
        running
          ? `실시간 자격증명 재검증 중 (${completedCount}/${items.length})`
          : stopped
            ? '자격증명 일괄 재검증 중단'
            : '자격증명 일괄 재검증 완료'
      }
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
              {running
                ? '서명 원본을 순서대로 대조하고 있어요...'
                : skippedCount > 0
                  ? `총 ${items.length}개 중 ${completedCount}개만 재검증을 시도했습니다.`
                  : stopped
                    ? `총 ${items.length}개 재검증을 시도했습니다.`
                    : `총 ${items.length}개 재검증이 완료되었습니다.`}
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
              {skippedCount > 0 ? '미실행' : '대기'} {Math.max(0, items.length - completedCount)}건
            </span>
          </div>
        </div>

        {!running && stopped && skippedCount > 0 && (
          <Alert variant="warning" title={`재검증을 중단했습니다. 나머지 ${skippedCount}건은 실행하지 않았어요.`}>
            검증 요청이 서버 오류나 요청 한도로 실패해 남은 항목은 보내지 않았습니다. 잠시 후 다시 시도해주세요. 이미
            끝난 항목의 결과는 지갑에 반영되었습니다.
          </Alert>
        )}
        {/* 마지막(또는 하나뿐인) 항목에서 멈춘 경우: 보내지 않은 항목은 없지만 그 요청은 실패했다 */}
        {!running && stopped && skippedCount === 0 && (
          <Alert variant="warning" title="재검증을 중단했습니다.">
            검증 요청이 서버 오류나 요청 한도로 실패했습니다. 잠시 후 다시 시도해주세요.
            {items.length > 1 && ' 이미 끝난 항목의 결과는 지갑에 반영되었습니다.'}
          </Alert>
        )}
        {!running && !stopped && movedCount > 0 && (
          <Alert variant="warning" title={`${movedCount}건의 자격증명이 '확인 필요' 탭으로 이동했습니다.`}>
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
                    {st.phase === 'skipped' && '실행하지 않음'}
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
                  {st.phase === 'skipped' && (
                    <span className="inline-flex items-center rounded-md bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium text-[var(--color-text-muted)]">
                      미실행
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

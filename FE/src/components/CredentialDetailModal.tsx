import { useMutation, useQueryClient } from '@tanstack/react-query';
import { reverifyCredential } from '@/api/credentials';
import { IssuerAvatar } from '@/components/IssuerAvatar';
import { StatusBadge } from '@/components/StatusBadge';
import { VerificationChecks } from '@/components/VerificationChecks';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { getApiErrorMessage } from '@/lib/apiClient';
import { getStatusDescription, getStatusGroup } from '@/lib/credentialStatus';
import { useCopy } from '@/lib/useCopy';
import type { VerifiedCredential } from '@/types/api';

interface Props {
  credential: VerifiedCredential | null;
  onClose: () => void;
}

/** 재검증 결과 메시지 (BE reverify 의 excluded reason 과 같은 값) */
function reverifyFailText(status: string, verifiedAt: string | null): string {
  if (!verifiedAt) return '서명 원본이 저장되어 있지 않아 다시 검증할 수 없어요. (이전 방식으로 저장된 자격증명)';
  return `지금 검증을 통과하지 못했어요. 상태가 갱신되었습니다. (${status})`;
}

export function CredentialDetailModal({ credential, onClose }: Props) {
  const { copy, isCopied } = useCopy();
  const queryClient = useQueryClient();
  // BE v0.6.0 실행 시점 재검증: 저장된 서명 원본을 지금 다시 검증하고 DB 상태도 갱신한다.
  const reverify = useMutation({
    mutationFn: (recordId: string) => reverifyCredential(recordId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['careers'] }),
  });

  if (!credential) return null;

  // 재검증했으면 갱신된 상태를 우선 표시한다.
  const result = reverify.data?.data;
  const status = result?.status ?? credential.status;
  const group = getStatusGroup(status);
  const rawJson = JSON.stringify(credential.raw_data ?? {}, null, 2);
  const hasRaw = Object.keys(credential.raw_data ?? {}).length > 0;
  const descBox =
    group === 'verified'
      ? 'bg-[#e9f8f1]'
      : group === 'pending'
        ? 'bg-[var(--color-primary-weak)]'
        : 'bg-[#fff6e6]';

  return (
    <Modal
      open
      title="자격증명 상세"
      onClose={onClose}
      footer={
        <>
          <Button variant="tertiary" className="flex-1" onClick={() => copy(rawJson, 'json')}>
            {isCopied('json') ? '복사됨' : 'JSON 복사'}
          </Button>
          <Button className="flex-1" onClick={onClose}>
            확인
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-5">
        <div className="flex items-center gap-4">
          <IssuerAvatar issuer={credential.issuer} size="lg" />
          <div className="min-w-0 flex-1">
            <p className="text-[19px] font-bold leading-snug">{credential.achievement_name}</p>
            <p className="truncate text-[13px] text-[var(--color-text-muted)]">{credential.issuer}</p>
          </div>
          <StatusBadge status={status} />
        </div>

        <p className={`rounded-2xl px-4 py-3 text-[13px] leading-relaxed text-[var(--color-text-sub)] ${descBox}`}>
          {getStatusDescription(status)}
        </p>

        <div className="flex flex-col gap-3">
          <Button
            variant="secondary"
            isLoading={reverify.isPending}
            onClick={() => reverify.mutate(credential.record_id)}
          >
            지금 다시 검증하기
          </Button>
          {reverify.isError && (
            <Alert variant="error" title="재검증 요청에 실패했습니다.">
              {getApiErrorMessage(reverify.error)}
            </Alert>
          )}
          {result && (
            <Alert
              variant={result.verified ? 'success' : 'warning'}
              title={result.verified ? '지금도 유효한 자격증명이에요.' : '재검증을 통과하지 못했어요.'}
            >
              {result.verified
                ? `서명 원본을 ${new Date(result.verified_at!).toLocaleString('ko-KR')}에 다시 검증했습니다.`
                : reverifyFailText(result.status, result.verified_at)}
              {result.checks.length > 0 && (
                <div className="mt-2">
                  <VerificationChecks checks={result.checks} />
                </div>
              )}
            </Alert>
          )}
        </div>

        <dl className="flex flex-col gap-3 text-[15px]">
          <div className="flex justify-between gap-4">
            <dt className="shrink-0 text-[var(--color-text-muted)]">발급 기관</dt>
            <dd className="text-right font-medium">{credential.issuer}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="shrink-0 text-[var(--color-text-muted)]">발급일</dt>
            <dd className="text-right font-medium">
              {new Date(credential.issued_at).toLocaleString('ko-KR', {
                dateStyle: 'long',
                timeStyle: 'short',
              })}
            </dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="shrink-0 text-[var(--color-text-muted)]">기록 ID</dt>
            <dd className="flex min-w-0 items-center gap-2">
              <span className="font-mono-did truncate text-[13px] text-[var(--color-text-sub)]">
                {credential.record_id}
              </span>
              <button
                type="button"
                onClick={() => copy(credential.record_id, 'id')}
                className="shrink-0 text-[13px] font-medium text-[var(--color-primary)] hover:underline"
              >
                {isCopied('id') ? '복사됨' : '복사'}
              </button>
            </dd>
          </div>
        </dl>

        <details className="group rounded-2xl bg-[var(--color-surface)]">
          <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-[15px] font-semibold">
            원본 데이터 (raw_data)
            <span className="text-[13px] text-[var(--color-text-muted)] group-open:rotate-180">▾</span>
          </summary>
          {hasRaw ? (
            <pre className="font-mono-did max-h-64 overflow-auto px-4 pb-4 text-[12px] leading-relaxed text-[var(--color-text-sub)]">
              {rawJson}
            </pre>
          ) : (
            <p className="px-4 pb-4 text-[13px] text-[var(--color-text-muted)]">저장된 원본 데이터가 없어요.</p>
          )}
        </details>
      </div>
    </Modal>
  );
}

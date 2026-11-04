import { IssuerAvatar } from '@/components/IssuerAvatar';
import { StatusBadge } from '@/components/StatusBadge';
import { Button } from '@/components/ui/Button';
import { Modal } from '@/components/ui/Modal';
import { getStatusDescription, getStatusGroup } from '@/lib/credentialStatus';
import { useCopy } from '@/lib/useCopy';
import type { VerifiedCredential } from '@/types/api';

interface Props {
  credential: VerifiedCredential | null;
  onClose: () => void;
}

export function CredentialDetailModal({ credential, onClose }: Props) {
  const { copy, isCopied } = useCopy();

  if (!credential) return null;

  const group = getStatusGroup(credential.status);
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
          <StatusBadge status={credential.status} />
        </div>

        <p className={`rounded-2xl px-4 py-3 text-[13px] leading-relaxed text-[var(--color-text-sub)] ${descBox}`}>
          {getStatusDescription(credential.status)}
        </p>

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

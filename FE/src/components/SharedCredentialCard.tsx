import { IssuerAvatar } from '@/components/IssuerAvatar';
import { StatusBadge } from '@/components/StatusBadge';
import { VerificationChecks } from '@/components/VerificationChecks';
import { Card } from '@/components/ui/Card';
import { useCopy } from '@/lib/useCopy';
import type { SharedCredential } from '@/types/api';

const formatDate = (iso: string) => new Date(iso).toLocaleDateString('ko-KR');

/** 기업용 조회 화면의 자격증명 카드 (열람 시점 재검증 결과 + 직접 검증용 서명 원본) */
export function SharedCredentialCard({ credential }: { credential: SharedCredential }) {
  const { copy, isCopied } = useCopy();
  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-center gap-4">
        <IssuerAvatar issuer={credential.issuer_name} />
        <div className="min-w-0 flex-1">
          <p className="text-[17px] font-bold leading-snug">{credential.achievement_name}</p>
          <p className="truncate text-[13px] text-[var(--color-text-muted)]">{credential.issuer_name}</p>
        </div>
        <StatusBadge status={credential.status} />
      </div>

      {credential.achievement_description && (
        <p className="text-[14px] leading-relaxed text-[var(--color-text-sub)]">{credential.achievement_description}</p>
      )}

      <dl className="flex flex-col gap-2 text-[14px]">
        <div className="flex justify-between gap-4">
          <dt className="text-[var(--color-text-muted)]">발급일</dt>
          <dd className="font-medium">{formatDate(credential.issued_at)}</dd>
        </div>
        {credential.expires_at && (
          <div className="flex justify-between gap-4">
            <dt className="text-[var(--color-text-muted)]">만료일</dt>
            <dd className="font-medium">{formatDate(credential.expires_at)}</dd>
          </div>
        )}
        {credential.criteria && (
          <div className="flex flex-col gap-1">
            <dt className="text-[var(--color-text-muted)]">성취 기준</dt>
            <dd className="text-[var(--color-text-sub)]">{credential.criteria}</dd>
          </div>
        )}
      </dl>

      <details className="group rounded-2xl bg-[var(--color-surface)]">
        <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-[14px] font-semibold">
          검증 상세
          <span className="text-[13px] text-[var(--color-text-muted)] group-open:rotate-180">▾</span>
        </summary>
        <div className="flex flex-col gap-3 px-4 pb-4">
          <VerificationChecks checks={credential.checks} />
          {credential.vc_jwt ? (
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <span className="text-[13px] text-[var(--color-text-muted)]">서명 원본 (VC-JWT, 직접 검증용)</span>
                <button
                  type="button"
                  onClick={() => copy(credential.vc_jwt!)}
                  className="text-[13px] font-medium text-[var(--color-primary)] hover:underline"
                >
                  {isCopied() ? '복사됨' : '복사'}
                </button>
              </div>
              <p className="font-mono-did max-h-24 overflow-auto break-all text-[12px] text-[var(--color-text-sub)]">
                {credential.vc_jwt}
              </p>
            </div>
          ) : (
            <p className="text-[13px] text-[var(--color-text-muted)]">서명 원본이 없어 직접 검증할 수 없는 자격증명이에요.</p>
          )}
        </div>
      </details>
    </Card>
  );
}

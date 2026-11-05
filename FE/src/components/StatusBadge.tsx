import type { CredentialStatus } from '@/types/api';

const NEUTRAL = 'bg-[var(--color-surface)] text-[var(--color-text-sub)]';

const STATUS_STYLE: Record<string, { label: string; className: string }> = {
  verified: { label: '검증됨', className: 'bg-[#e9f8f1] text-[#029359]' },
  invalid: { label: '유효하지 않음', className: 'bg-[#ffeeee] text-[#d22030]' },
  revoked: { label: '취소됨', className: 'bg-[#ffeeee] text-[#d22030]' },
  expired: { label: '만료됨', className: 'bg-[#fff6e6] text-[#c77700]' },
  unverifiable: { label: '검증 불가', className: NEUTRAL },
  pending: { label: '대기 중', className: 'bg-[var(--color-primary-weak)] text-[var(--color-primary)]' },
};

export function StatusBadge({ status }: { status: CredentialStatus }) {
  const style = STATUS_STYLE[status] ?? { label: status, className: NEUTRAL };

  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-md px-2 py-0.5 text-xs font-semibold ${style.className}`}
    >
      {style.label}
    </span>
  );
}

import type { CredentialStatus } from '@/types/api';

const NEUTRAL = 'bg-[var(--color-surface)] text-[var(--color-text-sub)]';

const STATUS_STYLE: Record<string, { label: string; className: string }> = {
  verified: { label: '검증됨', className: 'bg-[#e9f8f1] text-[#029359]' },
  invalid: { label: '유효하지 않음', className: 'bg-[#ffeeee] text-[#d22030]' },
  revoked: { label: '취소됨', className: 'bg-[#ffeeee] text-[#d22030]' },
  expired: { label: '만료됨', className: 'bg-[#fff6e6] text-[#c77700]' },
  unverifiable: { label: '검증 불가', className: 'bg-[#fff6e6] text-[#c77700]' },
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

/**
 * '미검증 기록' 경고 태그: 실행 시점 재검증을 통과하지 못해 AI 결과의 근거에서 뺀 기록,
 * 또는 올릴 때 서명·발급자를 확인하지 못해 격리 보관한 업로드(위조·변조 의심).
 */
export function UnverifiedRecordTag({ title }: { title?: string }) {
  return (
    <span
      title={title}
      className="inline-flex shrink-0 items-center gap-1 rounded-md border border-[#f5c2c7] bg-[#fff4f4] px-2 py-0.5 text-xs font-semibold text-[#d22030]"
    >
      <span aria-hidden="true">⚠</span>
      미검증 기록
    </span>
  );
}

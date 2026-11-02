import type { CredentialStatus } from '@/types/api';

const STATUS_STYLE: Record<string, { label: string; className: string }> = {
  verified: { label: 'VERIFIED VC', className: 'bg-emerald-400/10 text-emerald-300 border-emerald-400/30' },
  invalid: { label: '유효하지 않음', className: 'bg-red-400/10 text-red-300 border-red-400/30' },
  revoked: { label: '취소됨', className: 'bg-red-400/10 text-red-300 border-red-400/30' },
  expired: { label: '만료됨', className: 'bg-amber-400/10 text-amber-300 border-amber-400/30' },
  unverifiable: { label: '검증 불가', className: 'bg-slate-400/10 text-slate-300 border-slate-400/30' },
  pending: { label: '대기 중', className: 'bg-blue-400/10 text-blue-300 border-blue-400/30' },
};

export function StatusBadge({ status }: { status: CredentialStatus }) {
  const style = STATUS_STYLE[status] ?? {
    label: status,
    className: 'bg-slate-400/10 text-slate-300 border-slate-400/30',
  };

  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold tracking-wide ${style.className}`}
    >
      {style.label}
    </span>
  );
}

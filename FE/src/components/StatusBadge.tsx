import type { CredentialStatus } from '@/types/api';

const STATUS_STYLE: Record<string, { label: string; className: string }> = {
  verified: { label: '검증됨', className: 'bg-green-100 text-green-700' },
  invalid: { label: '유효하지 않음', className: 'bg-red-100 text-red-700' },
  revoked: { label: '취소됨', className: 'bg-red-100 text-red-700' },
  expired: { label: '만료됨', className: 'bg-amber-100 text-amber-700' },
  unverifiable: { label: '검증 불가', className: 'bg-gray-100 text-gray-700' },
  pending: { label: '대기 중', className: 'bg-blue-100 text-blue-700' },
};

export function StatusBadge({ status }: { status: CredentialStatus }) {
  const style = STATUS_STYLE[status] ?? {
    label: status,
    className: 'bg-gray-100 text-gray-700',
  };

  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold ${style.className}`}
    >
      {style.label}
    </span>
  );
}

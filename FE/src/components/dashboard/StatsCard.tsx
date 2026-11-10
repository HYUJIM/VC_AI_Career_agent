import { Card } from '@/components/ui/Card';

function Stat({
  label,
  value,
  unit,
  tone = 'default',
}: {
  label: string;
  value: number | string;
  unit?: string;
  tone?: 'default' | 'success' | 'warning';
}) {
  const color =
    tone === 'success'
      ? 'text-[var(--color-success)]'
      : tone === 'warning'
        ? 'text-[var(--color-warning)]'
        : 'text-[var(--color-text)]';
  return (
    <div className="flex flex-col gap-1 rounded-2xl bg-[var(--color-surface)] px-4 py-3">
      <span className="text-[13px] text-[var(--color-text-muted)]">{label}</span>
      <span className={`truncate text-[20px] font-bold tracking-tight ${color}`}>
        {value}
        {unit && <span className="ml-0.5 text-[14px] font-medium text-[var(--color-text-sub)]">{unit}</span>}
      </span>
    </div>
  );
}

interface StatsCardProps {
  total: number;
  verified: number;
  attention: number;
  latestDate: string | null;
}

export function StatsCard({ total, verified, attention, latestDate }: StatsCardProps) {
  return (
    <Card className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <Stat label="전체" value={total} unit="개" />
      <Stat label="검증됨" value={verified} unit="개" tone="success" />
      <Stat label="확인 필요" value={attention} unit="개" tone={attention > 0 ? 'warning' : 'default'} />
      <Stat label="최근 발급" value={latestDate ?? '-'} />
    </Card>
  );
}

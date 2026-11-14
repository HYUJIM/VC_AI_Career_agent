import { useMemo } from 'react';
import { Ring } from '@/components/charts/ring';
import { RingCenter } from '@/components/charts/ring-center';
import { RingChart } from '@/components/charts/ring-chart';
import type { RingData } from '@/components/charts/ring-context';
import { Card } from '@/components/ui/Card';

interface Props {
  total: number;
  verified: number;
  attention: number;
  pending: number;
}

/** Bklit UI Ring Chart로 상태별 비율을 보여준다. 링에 마우스를 올리면 가운데 숫자가 바뀐다. */
export function StatusRingCard({ total, verified, attention, pending }: Props) {
  // RingChart는 data 참조가 같으면 다시 그리지 않으므로 메모이즈한다.
  const data = useMemo<RingData[]>(
    () => [
      { label: '검증됨', value: verified, maxValue: total, color: 'var(--color-success)' },
      { label: '확인 필요', value: attention, maxValue: total, color: 'var(--color-warning)' },
      { label: '대기 중', value: pending, maxValue: total, color: 'var(--color-primary)' },
    ],
    [total, verified, attention, pending],
  );

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[15px] font-bold">검증 현황</h2>
        <span className="text-[13px] text-[var(--color-text-muted)]">
          {total > 0 ? `검증률 ${Math.round((verified / total) * 100)}%` : '데이터 없음'}
        </span>
      </div>
      {total === 0 ? (
        <p className="text-[13px] text-[var(--color-text-muted)]">자격증명을 발급받으면 상태 비율이 표시돼요.</p>
      ) : (
        <>
          <div className="mx-auto w-full max-w-[220px]">
            <RingChart data={data} strokeWidth={14} ringGap={6} baseInnerRadius={52}>
              {data.map((d, i) => (
                <Ring key={d.label} index={i} />
              ))}
              <RingCenter
                defaultLabel="전체"
                suffix="개"
                labelClassName="max-w-full truncate text-[12px] text-[var(--color-text-muted)]"
              />
            </RingChart>
          </div>
          <ul className="flex justify-center gap-4 text-[12px] text-[var(--color-text-sub)]">
            {data.map((d) => (
              <li key={d.label} className="flex items-center gap-1.5">
                <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: d.color }} />
                {d.label} {d.value}
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

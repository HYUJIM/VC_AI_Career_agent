import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';

export interface OnboardingStep {
  label: string;
  done: boolean;
  to?: string;
}

/** 새 사용자용 시작 가이드. 모든 단계를 마치면 숨겨진다. */
export function OnboardingChecklist({ steps }: { steps: OnboardingStep[] }) {
  const doneCount = steps.filter((s) => s.done).length;
  if (doneCount === steps.length) return null;

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[17px] font-bold">시작 가이드</h2>
        <span className="text-[13px] font-semibold text-[var(--color-primary)]">
          {doneCount}/{steps.length} 완료
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-[var(--color-surface)]">
        <div
          className="h-full rounded-full bg-[var(--color-primary)] transition-all"
          style={{ width: `${(doneCount / steps.length) * 100}%` }}
        />
      </div>
      <ol className="flex flex-col gap-2">
        {steps.map((step, i) => (
          <li key={step.label} className="flex items-center gap-3">
            <span
              aria-hidden="true"
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[12px] font-bold ${
                step.done
                  ? 'bg-[var(--color-primary)] text-white'
                  : 'bg-[var(--color-surface)] text-[var(--color-text-muted)]'
              }`}
            >
              {step.done ? '✓' : i + 1}
            </span>
            <span
              className={`flex-1 text-[15px] ${
                step.done ? 'text-[var(--color-text-muted)] line-through' : 'font-medium'
              }`}
            >
              {step.label}
            </span>
            {!step.done && step.to && (
              <Link to={step.to}>
                <Button variant="secondary" size="sm">
                  하러 가기
                </Button>
              </Link>
            )}
          </li>
        ))}
      </ol>
    </Card>
  );
}

import type { ReactNode } from 'react';

export type AlertVariant = 'error' | 'warning' | 'success' | 'info';

interface AlertProps {
  variant?: AlertVariant;
  title: string;
  children?: ReactNode;
  /** 알림 하단에 표시할 버튼/링크 */
  actions?: ReactNode;
  onClose?: () => void;
  className?: string;
}

const STYLES: Record<AlertVariant, { box: string; icon: string; title: string; symbol: string }> = {
  error: {
    box: 'border-red-400/40 bg-red-400/10',
    icon: 'bg-red-400/20 text-red-300',
    title: 'text-red-200',
    symbol: '!',
  },
  warning: {
    box: 'border-amber-400/40 bg-amber-400/10',
    icon: 'bg-amber-400/20 text-amber-300',
    title: 'text-amber-200',
    symbol: '!',
  },
  success: {
    box: 'border-emerald-400/40 bg-emerald-400/10',
    icon: 'bg-emerald-400/20 text-emerald-300',
    title: 'text-emerald-200',
    symbol: '✓',
  },
  info: {
    box: 'border-cyan-400/40 bg-cyan-400/10',
    icon: 'bg-cyan-400/20 text-cyan-300',
    title: 'text-cyan-200',
    symbol: 'i',
  },
};

export function Alert({
  variant = 'error',
  title,
  children,
  actions,
  onClose,
  className = '',
}: AlertProps) {
  const style = STYLES[variant];
  return (
    <div
      role={variant === 'error' || variant === 'warning' ? 'alert' : 'status'}
      className={`flex gap-3 rounded-xl border p-4 ${style.box} ${className}`}
    >
      <span
        aria-hidden="true"
        className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${style.icon}`}
      >
        {style.symbol}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className={`text-sm font-semibold ${style.title}`}>{title}</p>
        {children && (
          <div className="text-xs leading-relaxed text-[var(--color-text-muted)]">{children}</div>
        )}
        {actions && <div className="mt-2 flex flex-wrap gap-2">{actions}</div>}
      </div>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          aria-label="닫기"
          className="h-6 w-6 shrink-0 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          ×
        </button>
      )}
    </div>
  );
}

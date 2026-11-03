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

const STYLES: Record<AlertVariant, { box: string; icon: string; symbol: string }> = {
  error: { box: 'bg-[#ffeeee]', icon: 'bg-[var(--color-danger)]', symbol: '!' },
  warning: { box: 'bg-[#fff6e6]', icon: 'bg-[var(--color-warning)]', symbol: '!' },
  success: { box: 'bg-[#e9f8f1]', icon: 'bg-[var(--color-success)]', symbol: '✓' },
  info: { box: 'bg-[var(--color-primary-weak)]', icon: 'bg-[var(--color-primary)]', symbol: 'i' },
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
      className={`flex gap-3 rounded-2xl px-4 py-3.5 ${style.box} ${className}`}
    >
      <span
        aria-hidden="true"
        className={`mt-px flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold text-white ${style.icon}`}
      >
        {style.symbol}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="text-[15px] font-semibold text-[var(--color-text)]">{title}</p>
        {children && (
          <div className="text-[13px] leading-relaxed text-[var(--color-text-sub)]">{children}</div>
        )}
        {actions && <div className="mt-2 flex flex-wrap gap-2">{actions}</div>}
      </div>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          aria-label="닫기"
          className="h-6 w-6 shrink-0 rounded-md text-lg leading-none text-[var(--color-text-muted)] hover:bg-black/5 hover:text-[var(--color-text)]"
        >
          ×
        </button>
      )}
    </div>
  );
}

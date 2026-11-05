import type { ButtonHTMLAttributes, ReactNode } from 'react';

type Variant = 'primary' | 'secondary' | 'tertiary' | 'outline' | 'ghost' | 'danger';
type Size = 'sm' | 'md' | 'lg';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  isLoading?: boolean;
  children: ReactNode;
}

const variantClasses: Record<Variant, string> = {
  /** 화면의 핵심 행동 1개 */
  primary:
    'bg-[var(--color-primary)] text-[var(--color-primary-fg)] hover:bg-[var(--color-primary-hover)] disabled:bg-[var(--color-border)] disabled:text-[var(--color-text-muted)]',
  /** 보조 행동 — 연한 포인트 배경 */
  secondary:
    'bg-[var(--color-primary-weak)] text-[var(--color-primary)] hover:bg-[#d6e8ff] disabled:opacity-50',
  /** 중립 행동 — 회색 배경 (로그아웃, 다시 시도 등) */
  tertiary:
    'bg-[var(--color-surface)] text-[var(--color-text-sub)] hover:bg-[var(--color-border)] disabled:opacity-50',
  /** 색 배경(알림 등) 위에 올리는 흰 버튼 */
  outline:
    'border border-[var(--color-border)] bg-white text-[var(--color-text)] hover:bg-[var(--color-card-hover)] disabled:opacity-50',
  ghost: 'bg-transparent text-[var(--color-text-sub)] hover:bg-[var(--color-surface)]',
  danger: 'bg-[var(--color-danger)] text-white hover:opacity-90',
};

const sizeClasses: Record<Size, string> = {
  sm: 'h-8 rounded-lg px-3 text-[13px]',
  md: 'h-11 rounded-xl px-4 text-[15px]',
  lg: 'h-14 rounded-2xl px-5 text-[17px]',
};

export function Button({
  variant = 'primary',
  size = 'md',
  isLoading = false,
  disabled,
  className = '',
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      disabled={disabled || isLoading}
      className={`inline-flex shrink-0 items-center justify-center gap-1.5 font-semibold transition-colors active:scale-[0.98] disabled:cursor-not-allowed disabled:active:scale-100 ${sizeClasses[size]} ${variantClasses[variant]} ${className}`}
      {...rest}
    >
      {isLoading ? '처리 중...' : children}
    </button>
  );
}

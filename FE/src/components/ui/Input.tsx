import type { InputHTMLAttributes } from 'react';

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  /** 메시지 없이 테두리만 에러 상태로 표시 (예: 로그인 실패 시 비밀번호 칸) */
  invalid?: boolean;
}

export function Input({ label, error, invalid = false, id, className = '', ...rest }: InputProps) {
  const hasError = Boolean(error) || invalid;
  return (
    <div className="flex flex-col gap-1">
      {label && (
        <label htmlFor={id} className="text-sm font-medium text-[var(--color-text)]">
          {label}
        </label>
      )}
      <input
        id={id}
        aria-invalid={hasError || undefined}
        className={`rounded-lg border bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] outline-none transition-colors placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-primary)] ${
          hasError ? 'border-[var(--color-danger)]' : 'border-[var(--color-border)]'
        } ${className}`}
        {...rest}
      />
      {error && <span className="text-xs text-[var(--color-danger)]">{error}</span>}
    </div>
  );
}

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
    <div className="flex flex-col gap-2">
      {label && (
        <label htmlFor={id} className="text-[13px] font-medium text-[var(--color-text-sub)]">
          {label}
        </label>
      )}
      <input
        id={id}
        aria-invalid={hasError || undefined}
        className={`h-12 rounded-xl border-2 bg-[var(--color-surface)] px-4 text-[15px] text-[var(--color-text)] outline-none transition-colors placeholder:text-[#b0b8c1] focus:bg-white ${
          hasError
            ? 'border-[var(--color-danger)] bg-white'
            : 'border-transparent focus:border-[var(--color-primary)]'
        } ${className}`}
        {...rest}
      />
      {error && <span className="text-[13px] text-[var(--color-danger)]">{error}</span>}
    </div>
  );
}

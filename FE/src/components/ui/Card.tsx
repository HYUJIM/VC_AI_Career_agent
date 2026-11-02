import type { HTMLAttributes, ReactNode } from 'react';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  /** 상단에 강조 라인(그라데이션)을 표시 */
  accent?: boolean;
}

export function Card({ children, className = '', accent = false, ...rest }: CardProps) {
  return (
    <div
      className={`relative overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] p-6 ${className}`}
      {...rest}
    >
      {accent && (
        <span className="pointer-events-none absolute inset-x-0 top-0 h-0.5 bg-gradient-to-r from-indigo-500 via-cyan-400 to-emerald-400" />
      )}
      {children}
    </div>
  );
}

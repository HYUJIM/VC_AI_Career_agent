import type { HTMLAttributes, ReactNode } from 'react';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
}

/** 회색 배경 위의 흰 카드. 테두리·그림자 없이 면(surface)으로만 구분한다. */
export function Card({ children, className = '', ...rest }: CardProps) {
  return (
    <div className={`rounded-3xl bg-[var(--color-card)] p-6 ${className}`} {...rest}>
      {children}
    </div>
  );
}

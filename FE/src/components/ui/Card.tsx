import type { HTMLAttributes, ReactNode } from 'react';

type CardPadding = 'none' | 'sm' | 'md' | 'lg';

// p-* 유틸리티는 우선순위가 같고 숫자 순서(p-0 < p-4 < p-6 < p-8)로 생성되어 className 의 p-0/p-4 는 p-6 에 진다.
// 그래서 안쪽 여백 전체는 이 prop 으로만 바꾼다. (px-*/py-* 는 p-* 뒤에 생성되므로 className 으로 덮어써도 된다)
const paddingClasses: Record<CardPadding, string> = { none: 'p-0', sm: 'p-4', md: 'p-6', lg: 'p-8' };

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  /** 안쪽 여백. 기본 md = p-6(24px) */
  padding?: CardPadding;
}

/** 회색 배경 위의 흰 카드. 테두리·그림자 없이 면(surface)으로만 구분한다. */
export function Card({ children, className = '', padding = 'md', ...rest }: CardProps) {
  return (
    <div className={`rounded-3xl bg-[var(--color-card)] ${paddingClasses[padding]} ${className}`} {...rest}>
      {children}
    </div>
  );
}

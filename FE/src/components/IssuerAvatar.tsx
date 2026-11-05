/** 이름의 첫 글자로 만드는 원형 아바타 (아이콘 리소스 없이 목록 구분용) */
export function IssuerAvatar({ issuer, size = 'md' }: { issuer: string; size?: 'sm' | 'md' | 'lg' }) {
  const initial = issuer.replace(/[^\p{L}\p{N}]/gu, '').charAt(0).toUpperCase() || '?';
  const sizeClass = {
    sm: 'h-8 w-8 text-[13px]',
    md: 'h-11 w-11 text-[15px]',
    lg: 'h-14 w-14 text-[20px]',
  }[size];
  return (
    <span
      aria-hidden="true"
      className={`flex shrink-0 items-center justify-center rounded-full bg-[var(--color-surface)] font-bold text-[var(--color-text-sub)] ${sizeClass}`}
    >
      {initial}
    </span>
  );
}

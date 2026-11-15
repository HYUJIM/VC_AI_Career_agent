import { OAUTH_PROVIDER_IDS, OAUTH_PROVIDERS } from '@/lib/oauthProviders';

/**
 * 연동된 소셜 로그인 배지 ("Google 연동됨", "카카오 연동됨", "네이버 연동됨").
 * `GET /users/me` 의 oauth_providers 항목마다 하나씩, 알려진 제공자만 OAUTH_PROVIDER_IDS 순서로 그린다 (중복·모르는 값은 뺀다).
 * 연동된 제공자가 없거나 아직 /me 를 받지 못했으면 아무것도 그리지 않는다.
 */
export function OAuthProviderBadges({
  providers,
  className = '',
}: {
  providers: readonly unknown[] | undefined;
  className?: string;
}) {
  // 알려진 제공자 목록을 기준으로 거르므로 모르는 값·중복은 자연히 빠진다 (이전 BE 처럼 필드가 없으면 빈 목록).
  const linked = Array.isArray(providers) ? OAUTH_PROVIDER_IDS.filter((provider) => providers.includes(provider)) : [];
  if (linked.length === 0) return null;
  return (
    <span className={`flex flex-wrap items-center gap-1 ${className}`}>
      {linked.map((provider) => (
        <span
          key={provider}
          className="shrink-0 rounded-md bg-[var(--color-primary-weak)] px-1.5 py-0.5 text-[11px] font-semibold text-[var(--color-primary)]"
        >
          {OAUTH_PROVIDERS[provider].label} 연동됨
        </span>
      ))}
    </span>
  );
}

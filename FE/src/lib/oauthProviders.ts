import type { OAuthProviderId } from '@/types/api';

/**
 * 소셜 로그인 제공자별 화면 이름·FE 콜백 경로·허용 인가 호스트.
 * 다른 모듈을 import 하지 않는다: authStore(교차 탭 로그인 반영)도 콜백 경로를 써야 하는데,
 * lib/oauth.ts 를 부르면 api/auth → apiClient → authStore 로 순환 import 가 생긴다.
 */
export interface OAuthProviderConfig {
  /** 화면에 보이는 제공자 이름 (버튼·콜백 화면·연동 배지) */
  label: string;
  /** 제공자가 돌려보낼 FE 콜백 경로. BE 개발 기본값 *_REDIRECT_URI(http://localhost:5173/auth/<p>/callback)와 맞춘다 */
  callbackPath: string;
  /** BE가 돌려준 authorization_url 이 가리켜도 되는 인가 엔드포인트 호스트 (https 만, open redirect·javascript: 스킴 방지) */
  allowedAuthHosts: ReadonlySet<string>;
}

export const OAUTH_PROVIDERS: Readonly<Record<OAuthProviderId, OAuthProviderConfig>> = {
  google: {
    label: 'Google',
    callbackPath: '/auth/google/callback',
    allowedAuthHosts: new Set(['accounts.google.com']),
  },
  kakao: {
    label: '카카오',
    callbackPath: '/auth/kakao/callback',
    allowedAuthHosts: new Set(['kauth.kakao.com']),
  },
  naver: {
    label: '네이버',
    callbackPath: '/auth/naver/callback',
    allowedAuthHosts: new Set(['nid.naver.com']),
  },
};

/** 로그인·회원가입 화면의 버튼 순서이자 연동 배지 순서 */
export const OAUTH_PROVIDER_IDS: readonly OAuthProviderId[] = ['google', 'kakao', 'naver'];

/** 지원하는 제공자 이름인지 (sessionStorage·라우터 state·BE 응답처럼 형식을 믿을 수 없는 값을 거른다) */
export function isOAuthProviderId(value: unknown): value is OAuthProviderId {
  return typeof value === 'string' && Object.hasOwn(OAUTH_PROVIDERS, value);
}

/** 모든 제공자의 FE 콜백 경로 (소문자) */
export const OAUTH_CALLBACK_PATHS: ReadonlySet<string> = new Set(
  OAUTH_PROVIDER_IDS.map((provider) => OAUTH_PROVIDERS[provider].callbackPath),
);

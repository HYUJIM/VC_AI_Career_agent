import { type ComponentType, useEffect, useState } from 'react';
import { Alert } from '@/components/ui/Alert';
import {
  type ApiErrorKind,
  getApiErrorDetail,
  getApiErrorKind,
  getApiErrorMessage,
  getApiErrorStatus,
  getConnectionHint,
  needsRetryHint,
  OAUTH_INVALID_REDIRECT_DETAIL,
  RETRY_LATER_MESSAGE,
} from '@/lib/apiClient';
import { startOAuth } from '@/lib/oauth';
import { OAUTH_PROVIDER_IDS, OAUTH_PROVIDERS } from '@/lib/oauthProviders';
import type { OAuthProviderId } from '@/types/api';

interface OAuthStartError {
  provider: OAuthProviderId;
  kind: ApiErrorKind;
  message: string;
  status?: number;
  /** BE가 보낸 detail 원문 (문자열일 때만) */
  detail?: string;
}

function GoogleMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5 shrink-0">
      <path
        fill="#4285F4"
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09Z"
      />
      <path
        fill="#34A853"
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23Z"
      />
      <path
        fill="#FBBC05"
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84Z"
      />
      <path
        fill="#EA4335"
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53Z"
      />
    </svg>
  );
}

/**
 * GitHub 마크 (Invertocat). Primer Octicons `mark-github` 16px 공개 SVG path 를 그대로 인라인한다
 * (https://primer.style/octicons/icon/mark-github-16/). GitHub 로고 가이드: 흰색·검정만, 모양·비율 변경 금지, 외부 이미지 없이.
 * 크기만 Google 마크와 같은 20px 로 키운다 (viewBox 그대로라 모양은 바뀌지 않는다).
 */
function GitHubMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="h-5 w-5 shrink-0">
      <path
        fill="#FFFFFF"
        d="M6.766 11.328c-2.063-.25-3.516-1.734-3.516-3.656 0-.781.281-1.625.75-2.188-.203-.515-.172-1.609.063-2.062.625-.078 1.468.25 1.968.703.594-.187 1.219-.281 1.985-.281.765 0 1.39.094 1.953.265.484-.437 1.344-.765 1.969-.687.218.422.25 1.515.046 2.047.5.593.766 1.39.766 2.203 0 1.922-1.453 3.375-3.547 3.64.531.344.89 1.094.89 1.954v1.625c0 .468.391.734.86.547C13.781 14.359 16 11.53 16 8.03 16 3.61 12.406 0 7.984 0 3.563 0 0 3.61 0 8.031a7.88 7.88 0 0 0 5.172 7.422c.422.156.828-.125.828-.547v-1.25c-.219.094-.5.156-.75.156-1.031 0-1.64-.562-2.078-1.609-.172-.422-.36-.672-.719-.719-.187-.015-.25-.093-.25-.187 0-.188.313-.328.625-.328.453 0 .844.281 1.25.86.313.452.64.655 1.031.655s.641-.14 1-.5c.266-.265.47-.5.657-.656"
      />
    </svg>
  );
}

/** 네이버 N 로고 (네이버 로그인 버튼 가이드: 흰색 #FFFFFF, 최소 16px, 외부 이미지 없이 인라인 SVG) */
function NaverMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" className="h-4 w-4 shrink-0">
      <path fill="#FFFFFF" d="M16.27 12.84 7.46 0H0v24h7.73V11.16L16.54 24H24V0h-7.73v12.84Z" />
    </svg>
  );
}

interface SocialButtonSkin {
  Mark: ComponentType;
  /** 제공자 가이드의 기본 문구 (화면이 label 을 넘기지 않을 때) */
  defaultLabel: string;
  /** 색·모서리·심볼 간격. 크기·글꼴·눌림·비활성 표현은 BASE_CLASS 로 모든 제공자가 같다 */
  className: string;
}

/**
 * 버튼 공통 모양 (ui/Button 의 size="lg" 와 같은 높이·여백·글꼴·눌림·비활성 표현).
 * 제공자 색을 클래스 순서와 상관없이 정확히 쓰려고 ui/Button 대신 같은 클래스를 가진 button 을 직접 그린다.
 */
const BASE_CLASS =
  'inline-flex h-14 w-full shrink-0 items-center justify-center px-5 text-[17px] font-semibold transition-colors active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-50 disabled:active:scale-100';

/**
 * 제공자별 버튼 모양.
 * - Google: 기존 버튼 그대로 (ui/Button variant="outline" size="lg" 와 같은 클래스)
 * - GitHub: 공식 버튼 규격이 없어 GitHub 로고 가이드(흰색 마크, 높은 대비)에 맞춘 어두운 버튼
 *   (배경 #24292F, hover #32383F, 흰색 마크·문구, Google 과 같은 높이·모서리). 키보드 포커스 링을 직접 그린다
 *   (어두운 배경 위에서도 보이도록 2px 띄운 포인트 색 테두리). 앱에 다크 모드가 없어 색은 항상 같다.
 * - 네이버: 네이버 로그인 버튼 가이드 완성형 (배경 #03A94D, 흰색 N 로고·문구, 로고와 문구 사이 8px)
 */
const SKINS: Record<OAuthProviderId, SocialButtonSkin> = {
  google: {
    Mark: GoogleMark,
    defaultLabel: 'Google로 계속하기',
    className:
      'gap-1.5 rounded-2xl border border-[var(--color-border)] bg-white text-[var(--color-text)] hover:bg-[var(--color-card-hover)]',
  },
  github: {
    Mark: GitHubMark,
    defaultLabel: 'GitHub로 로그인',
    className:
      'gap-2 rounded-2xl bg-[#24292F] text-white hover:bg-[#32383F] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary)]',
  },
  naver: {
    Mark: NaverMark,
    defaultLabel: '네이버 로그인',
    className: 'gap-2 rounded-2xl bg-[#03A94D] text-white hover:brightness-95',
  },
};

/** 제공자 모양을 입힌 소셜 로그인 버튼 하나. 시작·오류 처리는 SocialLoginButtons 가 맡는다. */
export function SocialLoginButton({
  provider,
  label,
  disabled = false,
  isLoading = false,
  onClick,
}: {
  provider: OAuthProviderId;
  label?: string;
  disabled?: boolean;
  isLoading?: boolean;
  onClick: () => void;
}) {
  const { Mark, defaultLabel, className } = SKINS[provider];
  return (
    <button type="button" disabled={disabled || isLoading} onClick={onClick} className={`${BASE_CLASS} ${className}`}>
      {isLoading ? (
        '처리 중...'
      ) : (
        <>
          <Mark />
          <span>{label ?? defaultLabel}</span>
        </>
      )}
    </button>
  );
}

/** labels 를 넘기지 않으면 모든 제공자가 기본 문구를 쓴다 */
const DEFAULT_LABELS: Partial<Record<OAuthProviderId, string>> = {};

/**
 * 로그인·회원가입 화면에서 공통으로 쓰는 소셜 로그인(Google·GitHub·네이버 OAuth 2.0) 버튼 묶음.
 * "또는" 구분선 하나 아래에 제공자 버튼을 OAUTH_PROVIDER_IDS 순서로 그린다.
 * 누른 제공자로 PKCE S256과 서명된 state를 준비해 그 제공자의 인증 화면으로 이동한다.
 * 한 제공자를 시작하는 동안에는 다른 버튼을 잠근다 (sessionStorage 의 로그인 트랜잭션은 탭마다 하나뿐이다).
 */
export function SocialLoginButtons({
  disabled = false,
  labels = DEFAULT_LABELS,
}: {
  disabled?: boolean;
  /** 제공자별 버튼 문구 (없으면 제공자 가이드의 기본 문구) */
  labels?: Partial<Record<OAuthProviderId, string>>;
}) {
  const [startingProvider, setStartingProvider] = useState<OAuthProviderId | null>(null);
  const [oauthError, setOauthError] = useState<OAuthStartError | null>(null);

  useEffect(() => {
    // 제공자 화면에서 뒤로 가기로 돌아오면 브라우저가 이 화면을 뒤로 가기 캐시(bfcache)에서 그대로 꺼낸다.
    // 이동 직전의 "처리 중..." 상태가 남아 버튼이 모두 잠긴 채로 보이지 않게 풀어 준다.
    const onPageShow = (event: PageTransitionEvent) => {
      if (event.persisted) setStartingProvider(null);
    };
    window.addEventListener('pageshow', onPageShow);
    return () => window.removeEventListener('pageshow', onPageShow);
  }, []);

  async function handleStart(provider: OAuthProviderId) {
    setOauthError(null);
    setStartingProvider(provider);
    try {
      await startOAuth(provider);
    } catch (error) {
      setStartingProvider(null);
      setOauthError({
        provider,
        kind: getApiErrorKind(error),
        message: getApiErrorMessage(error),
        status: getApiErrorStatus(error),
        detail: getApiErrorDetail(error),
      });
    }
  }

  function renderError(err: OAuthStartError) {
    if (err.kind === 'network') {
      return (
        <Alert variant="warning" title="서버에 연결할 수 없습니다." onClose={() => setOauthError(null)}>
          {getConnectionHint()}
        </Alert>
      );
    }
    if (err.kind === 'rateLimited') {
      return (
        <Alert variant="warning" title="요청이 너무 많습니다." onClose={() => setOauthError(null)}>
          보안을 위해 잠시 동안 요청이 제한되었습니다. 잠시 후 다시 시도해주세요.
        </Alert>
      );
    }
    return (
      <Alert
        variant="error"
        title={`${OAUTH_PROVIDERS[err.provider].label} 로그인을 시작할 수 없습니다.`}
        onClose={() => setOauthError(null)}
      >
        {err.message}
        {/* 400 'Invalid redirect_uri'(배포 설정 문제)는 다시 시도해도 같으므로 다시 시도 안내를 붙이지 않는다 */}
        {err.detail !== OAUTH_INVALID_REDIRECT_DETAIL && needsRetryHint(err.status, err.message) && (
          <>
            <br />
            {import.meta.env.DEV
              ? '잠시 후 다시 시도하거나 이메일 로그인을 이용해주세요.'
              : RETRY_LATER_MESSAGE}
          </>
        )}
      </Alert>
    );
  }

  return (
    <div className="mt-5 flex flex-col gap-3">
      <div className="flex items-center gap-3" aria-hidden="true">
        <div className="h-px flex-1 bg-[var(--color-border)]" />
        <span className="text-[13px] text-[var(--color-text-muted)]">또는</span>
        <div className="h-px flex-1 bg-[var(--color-border)]" />
      </div>
      {oauthError && renderError(oauthError)}
      {OAUTH_PROVIDER_IDS.map((provider) => (
        <SocialLoginButton
          key={provider}
          provider={provider}
          label={labels[provider]}
          disabled={disabled || (startingProvider !== null && startingProvider !== provider)}
          isLoading={startingProvider === provider}
          onClick={() => handleStart(provider)}
        />
      ))}
    </div>
  );
}

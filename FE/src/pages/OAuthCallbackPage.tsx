import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import {
  type ApiErrorKind,
  getApiErrorDetail,
  getApiErrorKind,
  getApiErrorMessage,
  getApiErrorStatus,
  getConnectionHint,
  needsRetryHint,
  OAUTH_ALREADY_LINKED_DETAIL,
  OAUTH_EMAIL_NOT_VERIFIED_DETAIL,
  OAUTH_EMAIL_REQUIRED_DETAIL,
  OAUTH_OTHER_METHOD_ACCOUNT_DETAIL,
  OAUTH_PASSWORD_ACCOUNT_DETAIL,
  RETRY_LATER_MESSAGE,
} from '@/lib/apiClient';
import {
  finishOAuthCallback,
  OAuthCallbackClientError,
  type OAuthCallbackClientErrorCode,
  startOAuth,
} from '@/lib/oauth';
import { OAUTH_PROVIDERS } from '@/lib/oauthProviders';
import type { DashboardLocationState } from '@/pages/DashboardPage';
import { startSession } from '@/store/authStore';
import type { OAuthProviderId } from '@/types/api';

interface CallbackFailure {
  kind: ApiErrorKind | 'client';
  clientCode?: OAuthCallbackClientErrorCode;
  status?: number;
  message: string;
  /**
   * BE가 보낸 detail 원문. 같은 409 안에서 화면을 나눈다:
   * 이메일·비밀번호 계정(OAUTH_PASSWORD_ACCOUNT_DETAIL), 다른 방법으로 가입한 이메일(OAUTH_OTHER_METHOD_ACCOUNT_DETAIL),
   * 같은 제공자의 다른 계정이 이미 연동됨(그 밖의 409)
   */
  detail?: string;
}

/** 이메일·연동 안내는 콜백 화면에서 제공자 이름을 넣어 보여 준다 (나머지는 apiClient 번역 그대로). */
function providerMessage(provider: OAuthProviderId, detail: string | undefined, fallback: string): string {
  const { label } = OAUTH_PROVIDERS[provider];
  switch (detail) {
    case OAUTH_EMAIL_REQUIRED_DETAIL:
      return `${label} 계정의 이메일 제공에 동의해야 로그인할 수 있어요. 동의 항목을 확인한 뒤 다시 시도해주세요.`;
    case OAUTH_EMAIL_NOT_VERIFIED_DETAIL:
      return `${label} 계정의 이메일이 인증되지 않아 로그인할 수 없습니다.`;
    case OAUTH_ALREADY_LINKED_DETAIL:
      return `이 이메일 계정에는 이미 다른 ${label} 계정이 연동되어 있습니다. 연동된 ${label} 계정으로 로그인해주세요.`;
    default:
      return fallback;
  }
}

/**
 * 소셜 로그인(Google·카카오·네이버 OAuth 2.0) 콜백 화면 (`/auth/{google,kakao,naver}/callback`).
 * 1) 주소창의 일회성 code·state 쿼리를 즉시 지워 브라우저 기록·Referer로 새지 않게 한다.
 * 2) sessionStorage에 보관한 provider·state·PKCE code_verifier로 검증한 뒤 BE `POST /v1/users/oauth/{provider}/callback`을 호출한다.
 */
export function OAuthCallbackPage({ provider }: { provider: OAuthProviderId }) {
  const navigate = useNavigate();
  const { label } = OAUTH_PROVIDERS[provider];
  // React 19 StrictMode 개발 환경에서는 useEffect가 두 번 실행된다.
  // 첫 번째 이펙트에서 window.history.replaceState로 주소창 쿼리를 지우더라도
  // 두 번째 이펙트가 동일한 초기 쿼리(code, state)를 읽어 inflightCallbacks 캐시를 공유하도록 초기값을 고정한다.
  const [initialSearch] = useState(() => window.location.search);
  const [failure, setFailure] = useState<CallbackFailure | null>(null);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    const searchParams = new URLSearchParams(initialSearch);
    if (window.location.search) {
      // 일회성 인가 코드(code)와 state가 브라우저 히스토리·주소창에 남지 않도록 즉시 제거한다.
      window.history.replaceState(null, '', window.location.pathname);
    }

    let cancelled = false;
    finishOAuthCallback(provider, searchParams)
      .then((res) => {
        if (cancelled) return;
        startSession(res);
        // message 가 없는 응답이어도 로그인은 끝낸다 (환영 문구만 기본값으로 나온다).
        const newUser = (res.message ?? '').includes('회원가입');
        const oauthLinked = (res.message ?? '').includes('연동');
        const state: DashboardLocationState = {
          welcome: res.data.name,
          newUser,
          oauthLinked,
          oauthProvider: provider,
        };
        navigate('/dashboard', { replace: true, state });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof OAuthCallbackClientError) {
          setFailure({
            kind: 'client',
            clientCode: error.code,
            message: error.message,
          });
          return;
        }
        const detail = getApiErrorDetail(error);
        setFailure({
          kind: getApiErrorKind(error),
          status: getApiErrorStatus(error),
          message: providerMessage(provider, detail, getApiErrorMessage(error)),
          detail,
        });
      });

    return () => {
      cancelled = true;
    };
  }, [initialSearch, navigate, provider]);

  async function handleRetry() {
    setRetrying(true);
    try {
      await startOAuth(provider);
    } catch (error) {
      setRetrying(false);
      setFailure({
        kind: getApiErrorKind(error),
        status: getApiErrorStatus(error),
        message: getApiErrorMessage(error),
      });
    }
  }

  function renderFailure(err: CallbackFailure) {
    if (err.kind === 'network') {
      return (
        <Alert variant="warning" title="서버에 연결할 수 없습니다.">
          {getConnectionHint()}
        </Alert>
      );
    }
    if (err.kind === 'rateLimited') {
      return (
        <Alert variant="warning" title="로그인 시도가 너무 많습니다.">
          보안을 위해 잠시 동안 요청이 제한되었습니다. 잠시 후 다시 시도해주세요.
        </Alert>
      );
    }
    if (
      err.status === 409 &&
      (err.detail === OAUTH_PASSWORD_ACCOUNT_DETAIL || err.detail === OAUTH_OTHER_METHOD_ACCOUNT_DETAIL)
    ) {
      return (
        <Alert variant="warning" title={`이 이메일은 ${label}로 로그인할 수 없습니다.`}>
          {err.message}
        </Alert>
      );
    }
    if (err.status === 409) {
      return (
        <Alert variant="warning" title={`다른 ${label} 계정이 이미 연동되어 있습니다.`}>
          {err.message}
        </Alert>
      );
    }
    if (err.status === 400 && err.detail === OAUTH_EMAIL_REQUIRED_DETAIL) {
      return (
        <Alert variant="warning" title="이메일 제공 동의가 필요합니다.">
          {err.message}
        </Alert>
      );
    }
    if (err.kind === 'client' && err.clientCode === 'provider_denied') {
      return (
        <Alert variant="info" title={`${label} 로그인이 취소되었습니다.`}>
          {err.message}
        </Alert>
      );
    }
    return (
      <Alert variant="error" title={`${label} 로그인을 완료할 수 없습니다.`}>
        {err.message}
        {err.kind !== 'client' && needsRetryHint(err.status, err.message) && (
          <>
            <br />
            {import.meta.env.DEV
              ? '처음부터 다시 시도하거나 이메일 로그인을 이용해주세요.'
              : RETRY_LATER_MESSAGE}
          </>
        )}
      </Alert>
    );
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center">
      <Card className="w-full max-w-md p-8">
        {!failure ? (
          <div className="flex flex-col items-center gap-3 py-6 text-center" role="status" aria-live="polite">
            <div
              aria-hidden="true"
              className="h-9 w-9 animate-spin rounded-full border-3 border-[var(--color-border)] border-t-[var(--color-primary)]"
            />
            <h1 className="text-[20px] font-bold">{label} 계정을 확인하고 있어요</h1>
            <p className="text-[14px] text-[var(--color-text-muted)]">
              잠시만 기다려주세요. 인증이 끝나면 자동으로 지갑 화면으로 이동합니다.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            <div>
              <h1 className="text-[22px] font-bold leading-snug tracking-tight">{label} 로그인 안내</h1>
              <p className="mt-1 text-[14px] text-[var(--color-text-sub)]">
                아래 내용을 확인한 뒤 다시 시도해주세요.
              </p>
            </div>
            {renderFailure(failure)}
            <div className="flex flex-col gap-2 sm:flex-row">
              <Button
                type="button"
                size="md"
                className="flex-1"
                isLoading={retrying}
                onClick={handleRetry}
              >
                {label}로 다시 시도
              </Button>
              <Link to="/login" replace className="flex-1">
                <Button type="button" variant="tertiary" size="md" className="w-full" disabled={retrying}>
                  로그인 화면으로
                </Button>
              </Link>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}

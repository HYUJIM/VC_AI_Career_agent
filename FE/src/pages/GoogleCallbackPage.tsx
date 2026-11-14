import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import {
  type ApiErrorKind,
  getApiErrorKind,
  getApiErrorMessage,
  getApiErrorStatus,
  getConnectionHint,
  RETRY_LATER_MESSAGE,
} from '@/lib/apiClient';
import {
  finishGoogleOAuthCallback,
  OAuthCallbackClientError,
  type OAuthCallbackClientErrorCode,
  startGoogleOAuth,
} from '@/lib/oauth';
import { startSession } from '@/store/authStore';

interface CallbackFailure {
  kind: ApiErrorKind | 'client';
  clientCode?: OAuthCallbackClientErrorCode;
  status?: number;
  message: string;
}

/**
 * Google OAuth 2.0 콜백 화면 (`/auth/google/callback`).
 * 1) 주소창의 일회성 code·state 쿼리를 즉시 지워 브라우저 기록·Referer로 새지 않게 한다.
 * 2) sessionStorage에 보관한 state·PKCE code_verifier로 검증한 뒤 BE `POST /v1/users/oauth/google/callback`을 호출한다.
 */
export function GoogleCallbackPage() {
  const navigate = useNavigate();
  const [failure, setFailure] = useState<CallbackFailure | null>(null);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    const searchParams = new URLSearchParams(window.location.search);
    if (window.location.search) {
      // 일회성 인가 코드(code)와 state가 브라우저 히스토리·주소창에 남지 않도록 즉시 제거한다.
      window.history.replaceState(null, '', window.location.pathname);
    }

    let cancelled = false;
    finishGoogleOAuthCallback(searchParams)
      .then((res) => {
        if (cancelled) return;
        startSession(res);
        const newUser = res.message.includes('회원가입');
        const oauthLinked = res.message.includes('연동');
        navigate('/dashboard', {
          replace: true,
          state: { welcome: res.data.name, newUser, oauthLinked },
        });
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
        setFailure({
          kind: getApiErrorKind(error),
          status: getApiErrorStatus(error),
          message: getApiErrorMessage(error),
        });
      });

    return () => {
      cancelled = true;
    };
  }, [navigate]);

  async function handleRetryGoogle() {
    setRetrying(true);
    try {
      await startGoogleOAuth();
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
    if (err.status === 409) {
      return (
        <Alert variant="warning" title="다른 Google 계정이 이미 연동되어 있습니다.">
          {err.message}
        </Alert>
      );
    }
    if (err.kind === 'client' && err.clientCode === 'provider_denied') {
      return (
        <Alert variant="info" title="Google 로그인이 취소되었습니다.">
          {err.message}
        </Alert>
      );
    }
    return (
      <Alert variant="error" title="Google 로그인을 완료할 수 없습니다.">
        {err.message}
        {err.kind !== 'client' && err.status !== 429 && (
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
            <h1 className="text-[20px] font-bold">Google 계정을 확인하고 있어요</h1>
            <p className="text-[14px] text-[var(--color-text-muted)]">
              잠시만 기다려주세요. 인증이 끝나면 자동으로 지갑 화면으로 이동합니다.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-5">
            <div>
              <h1 className="text-[22px] font-bold leading-snug tracking-tight">Google 로그인 안내</h1>
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
                onClick={handleRetryGoogle}
              >
                Google로 다시 시도
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

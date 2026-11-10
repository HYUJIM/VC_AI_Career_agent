import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { loginUser } from '@/api/auth';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import {
  type ApiErrorKind,
  getApiErrorKind,
  getApiErrorMessage,
  getApiErrorStatus,
  getConnectionHint,
  RETRY_LATER_MESSAGE,
} from '@/lib/apiClient';
import { PREVIEW_ENABLED, PREVIEW_USER } from '@/lib/preview';
import { useAuthStore } from '@/store/authStore';

/** 다른 화면(회원가입 등)에서 로그인 화면으로 넘어올 때 전달하는 상태 */
export interface LoginLocationState {
  /** 이메일 입력칸 미리 채우기 */
  email?: string;
}

interface LoginError {
  kind: ApiErrorKind;
  message: string;
  email: string;
  /** HTTP 상태 코드 (응답이 없으면 undefined) */
  status?: number;
}

const loginSchema = z.object({
  email: z.string().email('올바른 이메일 형식이 아닙니다.'),
  password: z.string().min(1, '비밀번호를 입력해주세요.'),
});

type LoginForm = z.infer<typeof loginSchema>;

export function LoginPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const locationState = (location.state ?? {}) as LoginLocationState;
  const setUser = useAuthStore((state) => state.setUser);
  const clearLogoutReason = useAuthStore((state) => state.clearLogoutReason);
  const [loginError, setLoginError] = useState<LoginError | null>(null);
  // 로그아웃/세션 만료 안내는 스토어에서 한 번만 읽고 바로 비운다.
  const [notice, setNotice] = useState(() => useAuthStore.getState().logoutReason);

  useEffect(() => {
    // 비울 안내가 있을 때만 set()을 부른다. persist 는 set() 마다 이 탭의 세션을 저장소에 다시 쓰므로,
    // 로그인한 채 뒤로 가기로 이 화면에 온 오래된 탭이 다른 탭의 로그아웃·로그인을 덮어쓰지 않게 한다.
    if (useAuthStore.getState().logoutReason !== null) clearLogoutReason();
  }, [clearLogoutReason]);

  const {
    register,
    handleSubmit,
    setValue,
    setFocus,
    formState: { errors },
  } = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: locationState.email ?? '', password: '' },
  });

  const mutation = useMutation({
    mutationFn: loginUser,
    onSuccess: (res) => {
      setUser(res.data, res.access_token, res.expires_in);
      navigate('/dashboard', { state: { welcome: res.data.name } });
    },
    onError: (error, variables) => {
      const kind = getApiErrorKind(error);
      setLoginError({
        kind,
        message: getApiErrorMessage(error),
        email: variables.email,
        status: getApiErrorStatus(error),
      });
      if (kind === 'unauthorized') {
        // 비밀번호만 비우고 다시 입력하도록 포커스
        setValue('password', '');
        setFocus('password');
      }
    },
  });

  function onSubmit(values: LoginForm) {
    setLoginError(null);
    setNotice(null);
    mutation.mutate(values);
  }

  function renderLoginError(err: LoginError) {
    switch (err.kind) {
      case 'unauthorized':
        // BE는 보안상 "미가입 이메일"과 "비밀번호 오류"를 구분하지 않고 같은 401을 반환한다.
        return (
          <Alert
            variant="error"
            title="이메일 또는 비밀번호가 올바르지 않습니다."
            onClose={() => setLoginError(null)}
            actions={
              <Link to="/register" state={{ email: err.email }}>
                <Button type="button" variant="outline" size="sm">
                  이 이메일로 회원가입
                </Button>
              </Link>
            }
          >
            <ul className="list-inside list-disc">
              <li>비밀번호를 다시 확인해주세요. (대소문자 구분)</li>
              <li>
                <span className="font-mono-did">{err.email}</span> 로 가입한 적이 없다면 먼저
                회원가입을 해주세요.
              </li>
            </ul>
          </Alert>
        );
      case 'network':
        return (
          <Alert variant="warning" title="서버에 연결할 수 없습니다." onClose={() => setLoginError(null)}>
            {getConnectionHint()}
          </Alert>
        );
      case 'validation':
        return (
          <Alert variant="warning" title="입력값을 확인해주세요." onClose={() => setLoginError(null)}>
            {err.message}
          </Alert>
        );
      default:
        return (
          <Alert variant="error" title="로그인 중 오류가 발생했습니다." onClose={() => setLoginError(null)}>
            {err.message}
            {/* 429(시도 횟수 초과)는 메시지가 이미 "잠시 후 다시 시도"를 안내한다 */}
            {err.status !== 429 && (
              <>
                <br />
                {import.meta.env.DEV
                  ? '잠시 후 다시 시도해주세요. 계속되면 BE 서버 로그를 확인해주세요.'
                  : RETRY_LATER_MESSAGE}
              </>
            )}
          </Alert>
        );
    }
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center">
      <Card className="w-full max-w-md p-8">
        <h1 className="text-[24px] font-bold leading-snug tracking-tight">
          이메일로 로그인해주세요
        </h1>
        <p className="mb-8 mt-2 text-[15px] text-[var(--color-text-sub)]">
          검증된 자격증명을 한곳에서 관리할 수 있어요.
        </p>
        {notice === 'sessionExpired' && (
          <Alert variant="warning" title="로그인 정보가 만료되었습니다." className="mb-4" onClose={() => setNotice(null)}>
            저장된 계정 정보를 서버에서 찾을 수 없습니다. 다시 로그인해주세요.
          </Alert>
        )}
        {notice === 'tokenExpired' && (
          <Alert variant="warning" title="로그인 시간이 만료되었습니다." className="mb-4" onClose={() => setNotice(null)}>
            보안을 위해 다시 로그인해주세요.
          </Alert>
        )}
        {notice === 'loggedOut' && (
          <Alert variant="info" title="로그아웃되었습니다." className="mb-4" onClose={() => setNotice(null)} />
        )}
        <form className="flex flex-col gap-4" noValidate onSubmit={handleSubmit(onSubmit)}>
          <Input
            id="email"
            type="email"
            label="이메일"
            placeholder="hong@test.com"
            error={errors.email?.message}
            {...register('email')}
          />
          <Input
            id="password"
            type="password"
            label="비밀번호"
            autoComplete="current-password"
            error={errors.password?.message}
            invalid={loginError?.kind === 'unauthorized'}
            {...register('password')}
          />
          {loginError && renderLoginError(loginError)}
          <Button type="submit" size="lg" className="mt-2" isLoading={mutation.isPending}>
            로그인
          </Button>
        </form>
        <p className="mt-5 text-center text-[15px] text-[var(--color-text-muted)]">
          계정이 없으신가요?{' '}
          <Link to="/register" className="font-semibold text-[var(--color-primary)] hover:underline">
            회원가입
          </Link>
        </p>
        {import.meta.env.DEV && (
          <p className="mt-6 rounded-xl bg-[var(--color-surface)] px-4 py-3 text-[13px] leading-relaxed text-[var(--color-text-muted)]">
            개발용 시드 계정: <span className="font-mono-did">user001@example.com</span> ~{' '}
            <span className="font-mono-did">user045@example.com</span> / 비밀번호{' '}
            <span className="font-mono-did">test1234</span>
          </p>
        )}
        {PREVIEW_ENABLED && (
          <Button
            type="button"
            variant="tertiary"
            className="mt-3 w-full"
            onClick={() => {
              setUser(PREVIEW_USER);
              navigate('/dashboard');
            }}
          >
            미리보기로 입장 (개발용)
          </Button>
        )}
      </Card>
    </div>
  );
}

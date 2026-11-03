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
import { type ApiErrorKind, getApiErrorKind, getApiErrorMessage } from '@/lib/apiClient';
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
    clearLogoutReason();
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
      setUser(res.data);
      navigate('/dashboard', { state: { welcome: res.data.name } });
    },
    onError: (error, variables) => {
      const kind = getApiErrorKind(error);
      setLoginError({ kind, message: getApiErrorMessage(error), email: variables.email });
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
                <Button type="button" variant="secondary" className="px-3 py-1 text-xs">
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
            BE 서버(http://localhost:3000)가 실행 중인지 확인한 뒤 다시 시도해주세요.
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
            <br />
            잠시 후 다시 시도해주세요. 계속되면 BE 서버 로그를 확인해주세요.
          </Alert>
        );
    }
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center">
      <Card className="w-full max-w-sm">
        <h1 className="mb-6 text-xl font-bold">로그인</h1>
        {notice === 'sessionExpired' && (
          <Alert variant="warning" title="로그인 정보가 만료되었습니다." className="mb-4" onClose={() => setNotice(null)}>
            저장된 계정 정보를 서버에서 찾을 수 없습니다. 다시 로그인해주세요.
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
          <Button type="submit" isLoading={mutation.isPending}>
            로그인
          </Button>
        </form>
        <p className="mt-4 text-center text-sm text-[var(--color-text-muted)]">
          계정이 없으신가요?{' '}
          <Link to="/register" className="text-[var(--color-primary)] hover:underline">
            회원가입
          </Link>
        </p>
        {import.meta.env.DEV && (
          <p className="mt-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-xs text-[var(--color-text-muted)]">
            개발용 시드 계정: <span className="font-mono-did">user001@example.com</span> ~{' '}
            <span className="font-mono-did">user045@example.com</span> / 비밀번호{' '}
            <span className="font-mono-did">test1234</span>
          </p>
        )}
        {PREVIEW_ENABLED && (
          <Button
            type="button"
            variant="secondary"
            className="mt-4 w-full"
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

import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { registerUser } from '@/api/auth';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { type ApiErrorKind, getApiErrorKind, getApiErrorMessage } from '@/lib/apiClient';
import { useAuthStore } from '@/store/authStore';

interface RegisterError {
  kind: ApiErrorKind;
  message: string;
  email: string;
}

/** BE는 bcrypt 제약으로 UTF-8 기준 72바이트를 넘는 비밀번호를 거부한다(422). */
const MAX_PASSWORD_BYTES = 72;

const registerSchema = z.object({
  name: z.string().min(1, '이름을 입력해주세요.'),
  email: z.string().email('올바른 이메일 형식이 아닙니다.'),
  password: z
    .string()
    .min(8, '비밀번호는 8자 이상이어야 합니다.')
    .refine((v) => new TextEncoder().encode(v).length <= MAX_PASSWORD_BYTES, {
      message: `비밀번호가 너무 깁니다. (최대 ${MAX_PASSWORD_BYTES}바이트, 한글은 글자당 3바이트)`,
    }),
  passwordConfirm: z.string(),
}).refine((v) => v.password === v.passwordConfirm, {
  message: '비밀번호가 일치하지 않습니다.',
  path: ['passwordConfirm'],
});

type RegisterForm = z.infer<typeof registerSchema>;

export function RegisterPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const prefillEmail = (location.state as { email?: string } | null)?.email ?? '';
  const setSession = useAuthStore((state) => state.setSession);
  const [registerError, setRegisterError] = useState<RegisterError | null>(null);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<RegisterForm>({
    resolver: zodResolver(registerSchema),
    defaultValues: { name: '', email: prefillEmail, password: '', passwordConfirm: '' },
  });

  const mutation = useMutation({
    mutationFn: registerUser,
    onSuccess: (res) => {
      // BE가 가입과 동시에 사용자 정보(DID 포함)와 액세스 토큰을 내려주므로 바로 로그인 상태로 전환
      setSession(res.data, res.access_token);
      navigate('/dashboard', { state: { welcome: res.data.name, newUser: true } });
    },
    onError: (error, variables) => {
      const kind = getApiErrorKind(error);
      setRegisterError({ kind, message: getApiErrorMessage(error), email: variables.email });
      if (kind === 'conflict') {
        setError('email', { message: '이미 가입된 이메일입니다.' }, { shouldFocus: true });
      }
    },
  });

  function onSubmit(values: RegisterForm) {
    setRegisterError(null);
    mutation.mutate({ name: values.name, email: values.email, password: values.password });
  }

  function renderRegisterError(err: RegisterError) {
    switch (err.kind) {
      case 'conflict':
        return (
          <Alert
            variant="warning"
            title="이미 가입된 이메일입니다."
            onClose={() => setRegisterError(null)}
            actions={
              <Link to="/login" state={{ email: err.email }}>
                <Button type="button" variant="outline" size="sm">
                  이 이메일로 로그인
                </Button>
              </Link>
            }
          >
            <span className="font-mono-did">{err.email}</span> 은(는) 이미 사용 중입니다. 로그인하거나
            다른 이메일을 사용해주세요.
          </Alert>
        );
      case 'validation':
        return (
          <Alert variant="warning" title="입력값을 확인해주세요." onClose={() => setRegisterError(null)}>
            {err.message}
          </Alert>
        );
      case 'network':
        return (
          <Alert variant="warning" title="서버에 연결할 수 없습니다." onClose={() => setRegisterError(null)}>
            BE 서버(http://localhost:3000)가 실행 중인지 확인한 뒤 다시 시도해주세요.
          </Alert>
        );
      default:
        return (
          <Alert variant="error" title="회원가입 중 오류가 발생했습니다." onClose={() => setRegisterError(null)}>
            {err.message}
            <br />
            잠시 후 다시 시도해주세요. 계속되면 BE 서버 로그(DB 마이그레이션 여부)를 확인해주세요.
          </Alert>
        );
    }
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center">
      <Card className="w-full max-w-md p-8">
        <h1 className="text-[24px] font-bold leading-snug tracking-tight">
          가입하고 DID를 받아보세요
        </h1>
        <p className="mb-8 mt-2 text-[15px] text-[var(--color-text-sub)]">
          가입과 동시에 나만의 탈중앙 식별자(DID)가 발급돼요.
        </p>
        <form className="flex flex-col gap-4" noValidate onSubmit={handleSubmit(onSubmit)}>
          <Input
            id="name"
            label="이름"
            placeholder="홍길동"
            error={errors.name?.message}
            {...register('name')}
          />
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
            placeholder="8자 이상 (최대 72바이트)"
            autoComplete="new-password"
            error={errors.password?.message}
            {...register('password')}
          />
          <Input
            id="passwordConfirm"
            type="password"
            label="비밀번호 확인"
            autoComplete="new-password"
            error={errors.passwordConfirm?.message}
            {...register('passwordConfirm')}
          />
          {registerError && renderRegisterError(registerError)}
          <Button type="submit" size="lg" className="mt-2" isLoading={mutation.isPending}>
            가입하기
          </Button>
        </form>
        <p className="mt-5 text-center text-[15px] text-[var(--color-text-muted)]">
          이미 계정이 있으신가요?{' '}
          <Link to="/login" className="font-semibold text-[var(--color-primary)] hover:underline">
            로그인
          </Link>
        </p>
      </Card>
    </div>
  );
}

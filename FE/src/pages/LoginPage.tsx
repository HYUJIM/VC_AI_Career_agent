import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { loginUser } from '@/api/auth';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { getApiErrorMessage } from '@/lib/apiClient';
import { PREVIEW_ENABLED, PREVIEW_USER } from '@/lib/preview';
import { useAuthStore } from '@/store/authStore';

const loginSchema = z.object({
  email: z.string().email('올바른 이메일 형식이 아닙니다.'),
  password: z.string().min(1, '비밀번호를 입력해주세요.'),
});

type LoginForm = z.infer<typeof loginSchema>;

export function LoginPage() {
  const navigate = useNavigate();
  const setUser = useAuthStore((state) => state.setUser);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginForm>({ resolver: zodResolver(loginSchema) });

  const mutation = useMutation({
    mutationFn: loginUser,
    onSuccess: (res) => {
      setUser(res.data);
      navigate('/dashboard');
    },
    onError: (error) => setErrorMessage(getApiErrorMessage(error)),
  });

  function onSubmit(values: LoginForm) {
    setErrorMessage(null);
    mutation.mutate(values);
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center">
      <Card className="w-full max-w-sm">
        <h1 className="mb-6 text-xl font-bold">로그인</h1>
        <form className="flex flex-col gap-4" onSubmit={handleSubmit(onSubmit)}>
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
            {...register('password')}
          />
          {errorMessage && <p className="text-sm text-[var(--color-danger)]">{errorMessage}</p>}
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

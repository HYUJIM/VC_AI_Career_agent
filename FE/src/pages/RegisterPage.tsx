import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { Link, useNavigate } from 'react-router-dom';
import { z } from 'zod';
import { registerUser } from '@/api/auth';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { getApiErrorMessage } from '@/lib/apiClient';
import { useAuthStore } from '@/store/authStore';

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
  const setUser = useAuthStore((state) => state.setUser);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<RegisterForm>({ resolver: zodResolver(registerSchema) });

  const mutation = useMutation({
    mutationFn: registerUser,
    onSuccess: (res) => {
      setUser(res.data);
      navigate('/dashboard');
    },
    onError: (error) => setErrorMessage(getApiErrorMessage(error)),
  });

  function onSubmit(values: RegisterForm) {
    setErrorMessage(null);
    mutation.mutate({ name: values.name, email: values.email, password: values.password });
  }

  return (
    <div className="flex min-h-[70vh] items-center justify-center">
      <Card className="w-full max-w-sm">
        <h1 className="mb-6 text-xl font-bold">회원가입</h1>
        <form className="flex flex-col gap-4" onSubmit={handleSubmit(onSubmit)}>
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
          {errorMessage && <p className="text-sm text-[var(--color-danger)]">{errorMessage}</p>}
          <Button type="submit" isLoading={mutation.isPending}>
            가입하고 DID 발급받기
          </Button>
        </form>
        <p className="mt-4 text-center text-sm text-[var(--color-text-muted)]">
          이미 계정이 있으신가요?{' '}
          <Link to="/login" className="text-[var(--color-primary)] hover:underline">
            로그인
          </Link>
        </p>
      </Card>
    </div>
  );
}

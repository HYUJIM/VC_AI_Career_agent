import { useMutation } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { issueMockBadge, verifyCredential } from '@/api/credentials';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { getApiErrorMessage } from '@/lib/apiClient';
import { useAuthStore } from '@/store/authStore';

/**
 * BE README "[Step 1] 테스트용 임시 발급 서버(Mock Issuer)" 에서 사용하는 샘플 템플릿 ID.
 */
const DEFAULT_TEMPLATE_ID = 'f9499c5f-6611-45c6-b5a8-683a99040aab';

type Step = 'issue' | 'verify' | 'done';

export function IssueCredentialPage() {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const [templateId, setTemplateId] = useState(DEFAULT_TEMPLATE_ID);
  const [jwt, setJwt] = useState<string | null>(null);
  const [step, setStep] = useState<Step>('issue');
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const issueMutation = useMutation({
    mutationFn: () => issueMockBadge(templateId, user!.did),
    onSuccess: (res) => {
      setJwt(res.vcJwt);
      setStep('verify');
    },
    onError: (error) => setErrorMessage(getApiErrorMessage(error)),
  });

  const verifyMutation = useMutation({
    mutationFn: () => verifyCredential({ type: 'badge', token: jwt! }),
    onSuccess: (res) => {
      if (res.success) {
        setStep('done');
      } else {
        setErrorMessage(res.message ?? '검증에 실패했습니다.');
      }
    },
    onError: (error) => setErrorMessage(getApiErrorMessage(error)),
  });

  if (!user) return null;

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-6">
      <h1 className="text-2xl font-bold">자격증명 발급 및 검증</h1>
      <p className="text-sm text-[var(--color-text-muted)]">
        테스트용 모의 발급 서버(Mock Issuer)에서 배지를 발급받고, BE가 DXWorks API로 검증한 뒤
        지갑에 저장합니다.
      </p>

      {errorMessage && (
        <Card className="border-[var(--color-danger)]">
          <p className="text-sm text-[var(--color-danger)]">{errorMessage}</p>
        </Card>
      )}

      <Card className="flex flex-col gap-4">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <span
            className={step === 'issue' ? 'text-[var(--color-primary)]' : 'text-green-600'}
          >
            1. 배지 발급
          </span>
          <span>→</span>
          <span
            className={
              step === 'verify'
                ? 'text-[var(--color-primary)]'
                : step === 'done'
                  ? 'text-green-600'
                  : 'text-[var(--color-text-muted)]'
            }
          >
            2. 검증 및 저장
          </span>
        </div>

        {step === 'issue' && (
          <>
            <Input
              id="templateId"
              label="Template ID"
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}
            />
            <Button
              onClick={() => {
                setErrorMessage(null);
                issueMutation.mutate();
              }}
              isLoading={issueMutation.isPending}
            >
              모의 배지 발급받기
            </Button>
          </>
        )}

        {step === 'verify' && jwt && (
          <>
            <p className="break-all rounded-lg bg-gray-50 p-3 text-xs text-[var(--color-text-muted)]">
              발급된 JWT: {jwt.slice(0, 60)}...
            </p>
            <Button
              onClick={() => {
                setErrorMessage(null);
                verifyMutation.mutate();
              }}
              isLoading={verifyMutation.isPending}
            >
              검증하고 지갑에 저장하기
            </Button>
          </>
        )}

        {step === 'done' && (
          <>
            <p className="text-sm text-green-700">
              검증 및 저장이 완료되었습니다. 지갑 대시보드에서 확인하세요.
            </p>
            <Button onClick={() => navigate('/dashboard')}>대시보드로 이동</Button>
          </>
        )}
      </Card>
    </div>
  );
}

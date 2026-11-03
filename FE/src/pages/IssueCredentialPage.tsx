import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { issueMockBadge, verifyCredential } from '@/api/credentials';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Alert } from '@/components/ui/Alert';
import { type ApiErrorKind, getApiErrorKind, getApiErrorMessage } from '@/lib/apiClient';
import { useAuthStore } from '@/store/authStore';
import type { DashboardLocationState } from '@/pages/DashboardPage';
import type { BadgeVerifyData } from '@/types/api';

/**
 * BE README "[Step 1] 테스트용 임시 발급 서버(Mock Issuer)" 에서 사용하는 샘플 템플릿 ID.
 */
const DEFAULT_TEMPLATE_ID = 'f9499c5f-6611-45c6-b5a8-683a99040aab';

type Step = 'issue' | 'verify' | 'done';

interface IssueError {
  stage: 'issue' | 'verify';
  /** rejected: BE가 HTTP 200 + success:false 로 검증 실패를 알린 경우 */
  kind: ApiErrorKind | 'rejected';
  message: string;
}

const STEPS: { key: Step; title: string; desc: string }[] = [
  { key: 'issue', title: '1단계: 배지 발급', desc: 'Mock Issuer에서 VC(JWT) 발급' },
  { key: 'verify', title: '2단계: 검증 및 저장', desc: 'DXWorks API로 검증 후 지갑에 저장' },
  { key: 'done', title: '3단계: 완료', desc: '자격증명 지갑에서 확인' },
];

export function IssueCredentialPage() {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  const [templateId, setTemplateId] = useState(DEFAULT_TEMPLATE_ID);
  const [jwt, setJwt] = useState<string | null>(null);
  const [step, setStep] = useState<Step>('issue');
  const [issueError, setIssueError] = useState<IssueError | null>(null);
  const [verified, setVerified] = useState<BadgeVerifyData | null>(null);
  const queryClient = useQueryClient();

  const issueMutation = useMutation({
    mutationFn: () => issueMockBadge(templateId, user!.did),
    onSuccess: (res) => {
      if (!res.vcJwt || typeof res.vcJwt !== 'string') {
        setIssueError({ stage: 'issue', kind: 'unknown', message: '발급 응답에 VC-JWT가 없습니다.' });
        return;
      }
      setJwt(res.vcJwt);
      setStep('verify');
    },
    onError: (error) =>
      setIssueError({ stage: 'issue', kind: getApiErrorKind(error), message: getApiErrorMessage(error) }),
  });

  const verifyMutation = useMutation({
    mutationFn: () => verifyCredential({ type: 'badge', token: jwt! }),
    onSuccess: (res) => {
      // BE는 검증 실패도 HTTP 200 + success:false 로 응답한다.
      if (res.success) {
        setVerified(res.data as BadgeVerifyData);
        setStep('done');
        // 대시보드/포트폴리오가 새로 저장된 자격증명을 다시 불러오도록 캐시 무효화
        queryClient.invalidateQueries({ queryKey: ['careers'] });
      } else {
        setIssueError({ stage: 'verify', kind: 'rejected', message: res.message });
      }
    },
    onError: (error) =>
      setIssueError({ stage: 'verify', kind: getApiErrorKind(error), message: getApiErrorMessage(error) }),
  });

  function restart() {
    setIssueError(null);
    setJwt(null);
    setVerified(null);
    setStep('issue');
  }

  function renderIssueError(err: IssueError) {
    const close = () => setIssueError(null);
    if (err.kind === 'network') {
      return (
        <Alert variant="warning" title="서버에 연결할 수 없습니다." onClose={close}>
          BE 서버(http://localhost:3000)가 실행 중인지 확인한 뒤 다시 시도해주세요.
        </Alert>
      );
    }
    if (err.stage === 'verify' && err.kind === 'rejected') {
      return (
        <Alert
          variant="error"
          title="배지 검증에 실패했습니다."
          onClose={close}
          actions={
            <Button variant="secondary" className="px-3 py-1 text-xs" onClick={restart}>
              처음부터 다시 발급
            </Button>
          }
        >
          DXWorks 검증을 통과하지 못해 지갑에 저장되지 않았습니다. (서버 메시지: {err.message})
        </Alert>
      );
    }
    if (err.kind === 'validation') {
      return (
        <Alert variant="warning" title="요청 값이 올바르지 않습니다." onClose={close}>
          {err.message}
        </Alert>
      );
    }
    return (
      <Alert
        variant="error"
        title={err.stage === 'issue' ? '배지 발급에 실패했습니다.' : '검증 요청 중 오류가 발생했습니다.'}
        onClose={close}
      >
        {err.message}
        {err.stage === 'issue' && (
          <>
            <br />
            Template ID가 올바른지, BE의 DXWORKS_API_KEY 설정을 확인해주세요.
          </>
        )}
      </Alert>
    );
  }

  if (!user) return null;

  const currentIndex = STEPS.findIndex((s) => s.key === step);

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6">
      <div>
        <span className="text-xs font-semibold tracking-widest text-[var(--color-primary)]">
          CREDENTIAL ISSUANCE
        </span>
        <h1 className="mt-1 text-3xl font-bold">자격증명 발급 및 검증</h1>
        <p className="mt-2 text-sm text-[var(--color-text-muted)]">
          테스트용 모의 발급 서버(Mock Issuer)에서 배지를 발급받고, BE가 DXWorks API로 검증한 뒤
          지갑에 저장합니다.
        </p>
      </div>

      <Card className="flex flex-col gap-1 p-5">
        <span className="text-xs text-[var(--color-text-muted)]">내 DID</span>
        <span className="font-mono-did break-all text-sm text-emerald-300">{user.did}</span>
      </Card>

      {issueError && renderIssueError(issueError)}

      <Card accent className="flex flex-col gap-6">
        <ol className="flex flex-col gap-3">
          {STEPS.map((s, i) => {
            const done = i < currentIndex || step === 'done';
            const active = i === currentIndex && step !== 'done';
            return (
              <li
                key={s.key}
                className={`flex items-center gap-3 rounded-lg border px-4 py-3 ${
                  active
                    ? 'border-[var(--color-primary)] bg-cyan-400/5'
                    : 'border-[var(--color-border)]'
                }`}
              >
                <span
                  className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${
                    done
                      ? 'bg-emerald-400 text-[#04140d]'
                      : active
                        ? 'bg-[var(--color-primary)] text-[var(--color-primary-fg)]'
                        : 'bg-[var(--color-border)] text-[var(--color-text-muted)]'
                  }`}
                >
                  {done ? '✓' : i + 1}
                </span>
                <div>
                  <p className="text-sm font-semibold">{s.title}</p>
                  <p className="text-xs text-[var(--color-text-muted)]">{s.desc}</p>
                </div>
              </li>
            );
          })}
        </ol>

        {step === 'issue' && (
          <div className="flex flex-col gap-4">
            <Input
              id="templateId"
              label="Template ID"
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}
            />
            <Button
              onClick={() => {
                setIssueError(null);
                issueMutation.mutate();
              }}
              disabled={!templateId.trim()}
              isLoading={issueMutation.isPending}
            >
              모의 배지 발급받기
            </Button>
          </div>
        )}

        {step === 'verify' && jwt && (
          <div className="flex flex-col gap-4">
            <p className="font-mono-did break-all rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-xs text-[var(--color-text-muted)]">
              발급된 JWT: {jwt.slice(0, 60)}...
            </p>
            <Button
              onClick={() => {
                setIssueError(null);
                verifyMutation.mutate();
              }}
              isLoading={verifyMutation.isPending}
            >
              검증하고 지갑에 저장하기
            </Button>
          </div>
        )}

        {step === 'done' && (
          <div className="flex flex-col gap-4">
            <p className="text-sm text-emerald-300">
              검증 및 저장이 완료되었습니다. 지갑 대시보드에서 확인하세요.
            </p>
            {verified && (verified.badge?.name || verified.checks?.issuer?.issuerName) && (
              <dl className="flex flex-col gap-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 text-xs">
                {verified.badge?.name && (
                  <div className="flex justify-between gap-2">
                    <dt className="text-[var(--color-text-muted)]">배지</dt>
                    <dd>{verified.badge.name}</dd>
                  </div>
                )}
                {verified.checks?.issuer?.issuerName && (
                  <div className="flex justify-between gap-2">
                    <dt className="text-[var(--color-text-muted)]">발급 기관</dt>
                    <dd>
                      {verified.checks.issuer.issuerName}
                      {verified.checks.issuerTrust?.trusted && (
                        <span className="ml-1 text-emerald-300">(신뢰된 발급처)</span>
                      )}
                    </dd>
                  </div>
                )}
              </dl>
            )}
            <Button
              onClick={() =>
                navigate('/dashboard', {
                  state: { issued: verified?.badge?.name ?? '새' } satisfies DashboardLocationState,
                })
              }
            >
              자격증명 지갑으로 이동 →
            </Button>
            <Button variant="ghost" onClick={restart}>
              다른 배지 발급하기
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}

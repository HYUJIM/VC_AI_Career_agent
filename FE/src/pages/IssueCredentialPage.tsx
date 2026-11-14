import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { issueMockBadge, verifyCredential } from '@/api/credentials';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Alert } from '@/components/ui/Alert';
import {
  type ApiErrorKind,
  getApiErrorKind,
  getApiErrorMessage,
  getApiErrorStatus,
  getConnectionHint,
  RETRY_LATER_MESSAGE,
} from '@/lib/apiClient';
import { useAuthStore } from '@/store/authStore';
import type { DashboardLocationState } from '@/pages/DashboardPage';
import type { BadgeVerifyData } from '@/types/api';

type Step = 'issue' | 'verify' | 'done';

interface IssueError {
  stage: 'issue' | 'verify';
  /** rejected: BE가 HTTP 200 + success:false 로 검증 실패를 알린 경우 */
  kind: ApiErrorKind | 'rejected';
  message: string;
  /** HTTP 상태 코드 (응답이 없거나 rejected 면 undefined) */
  status?: number;
}

const STEPS: { key: Step; title: string; desc: string }[] = [
  { key: 'issue', title: '배지 발급', desc: 'Mock Issuer에서 VC(JWT)를 받아요' },
  { key: 'verify', title: '검증 및 저장', desc: 'DXWorks API로 검증한 뒤 지갑에 저장해요' },
  { key: 'done', title: '완료', desc: '자격증명 지갑에서 확인할 수 있어요' },
];

export function IssueCredentialPage() {
  const navigate = useNavigate();
  const user = useAuthStore((state) => state.user);
  // 비워두면 BE가 서버 설정(.env)의 기본 템플릿으로 발급한다. 템플릿 ID는 협력사 자료라 FE 코드에 두지 않는다.
  const [templateId, setTemplateId] = useState('');
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
      setIssueError({
        stage: 'issue',
        kind: getApiErrorKind(error),
        message: getApiErrorMessage(error),
        status: getApiErrorStatus(error),
      }),
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
          {getConnectionHint()}
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
            <Button variant="outline" size="sm" onClick={restart}>
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
        {/* 429(발급 한도 초과)는 메시지가 이미 "나중에 다시 시도"를 안내한다 */}
        {err.stage === 'issue' && err.status !== 429 && (
          <>
            <br />
            {import.meta.env.DEV
              ? 'Template ID가 올바른지, BE의 DXWORKS_API_KEY 설정을 확인해주세요.'
              : RETRY_LATER_MESSAGE}
          </>
        )}
      </Alert>
    );
  }

  if (!user) return null;

  const currentIndex = STEPS.findIndex((s) => s.key === step);

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-4">
      <section className="flex flex-col gap-2 px-1">
        <h1 className="text-[26px] font-bold leading-snug tracking-tight">자격증명 발급받기</h1>
        <p className="text-[15px] leading-relaxed text-[var(--color-text-sub)]">
          모의 발급 서버에서 배지를 받고, 검증을 통과하면 지갑에 저장돼요.
        </p>
      </section>

      {issueError && renderIssueError(issueError)}

      <Card className="flex flex-col gap-6">
        <ol className="flex flex-col">
          {STEPS.map((s, i) => {
            const done = i < currentIndex || step === 'done';
            const active = i === currentIndex && step !== 'done';
            const last = i === STEPS.length - 1;
            return (
              <li key={s.key} className="flex gap-4">
                <div className="flex flex-col items-center">
                  <span
                    className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[13px] font-bold ${
                      done || active
                        ? 'bg-[var(--color-primary)] text-white'
                        : 'bg-[var(--color-surface)] text-[var(--color-text-muted)]'
                    }`}
                  >
                    {done ? '✓' : i + 1}
                  </span>
                  {!last && (
                    <span
                      className={`my-1 w-0.5 flex-1 ${
                        done ? 'bg-[var(--color-primary)]' : 'bg-[var(--color-border)]'
                      }`}
                    />
                  )}
                </div>
                <div className={last ? 'pb-0' : 'pb-5'}>
                  <p
                    className={`text-[15px] font-semibold ${
                      done || active ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)]'
                    }`}
                  >
                    {s.title}
                  </p>
                  <p className="text-[13px] text-[var(--color-text-muted)]">{s.desc}</p>
                </div>
              </li>
            );
          })}
        </ol>

        <div className="rounded-2xl bg-[var(--color-surface)] px-4 py-3">
          <p className="text-[13px] text-[var(--color-text-muted)]">받는 사람 DID</p>
          <p className="font-mono-did mt-0.5 break-all text-[13px] text-[var(--color-text-sub)]">{user.did}</p>
        </div>

        {step === 'issue' && (
          <div className="flex flex-col gap-4">
            <Input
              id="templateId"
              label="Template ID (선택)"
              placeholder="비워두면 서버 기본 템플릿으로 발급해요"
              value={templateId}
              onChange={(e) => setTemplateId(e.target.value)}
            />
            <Button
              onClick={() => {
                setIssueError(null);
                issueMutation.mutate();
              }}
              size="lg"
              isLoading={issueMutation.isPending}
            >
              모의 배지 발급받기
            </Button>
          </div>
        )}

        {step === 'verify' && jwt && (
          <div className="flex flex-col gap-4">
            <div className="rounded-2xl bg-[var(--color-surface)] px-4 py-3">
              <p className="text-[13px] text-[var(--color-text-muted)]">발급된 VC-JWT</p>
              <p className="font-mono-did mt-0.5 break-all text-[13px] text-[var(--color-text-sub)]">
                {jwt.slice(0, 60)}...
              </p>
            </div>
            <Button
              size="lg"
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
            <p className="text-[17px] font-bold">지갑에 저장했어요</p>
            {verified && (
              <dl className="flex flex-col gap-2 rounded-2xl bg-[var(--color-surface)] px-4 py-3 text-[15px]">
                <div className="flex justify-between gap-2">
                  <dt className="text-[var(--color-text-muted)]">배지</dt>
                  <dd className="font-medium">{verified.achievement_name}</dd>
                </div>
                {verified.issuer_name && (
                  <div className="flex justify-between gap-2">
                    <dt className="text-[var(--color-text-muted)]">발급 기관</dt>
                    <dd className="font-medium">
                      {verified.issuer_name}
                      {verified.issuer_trusted && (
                        <span className="ml-1.5 text-[13px] font-semibold text-[var(--color-success)]">
                          신뢰된 발급처
                        </span>
                      )}
                    </dd>
                  </div>
                )}
              </dl>
            )}
            <Button
              size="lg"
              onClick={() =>
                navigate('/dashboard', {
                  state: { issued: verified?.achievement_name ?? '새' } satisfies DashboardLocationState,
                })
              }
            >
              지갑에서 확인하기
            </Button>
            <Button variant="tertiary" size="lg" onClick={restart}>
              다른 배지 발급하기
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { verifyCredential } from '@/api/credentials';
import { StatusBadge, UnverifiedRecordTag } from '@/components/StatusBadge';
import { VerificationChecks } from '@/components/VerificationChecks';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { getApiErrorMessage } from '@/lib/apiClient';
import type { BadgeVerifyData, VerifyResponse } from '@/types/api';

/** BE verify.MAX_TOKEN_CHARS와 같다 (넘으면 BE가 422로 거절한다) */
const MAX_TOKEN_CHARS = 256 * 1024;
/** BE verify.UNVERIFIED_LIMIT_REACHED */
const UNVERIFIED_LIMIT_REACHED = 'Badge verification failed. Unverified record limit reached.';
const TOKEN_KEYS = ['vcJwt', 'vc_jwt', 'jwt', 'token', 'credential'] as const;

/** 붙여 넣은 값·파일 내용에서 VC-JWT를 꺼낸다: JWT 문자열 그대로, 또는 {"vcJwt": "..."} 같은 JSON */
function extractToken(text: string): string {
  const trimmed = text.trim();
  if (!trimmed.startsWith('{')) return trimmed;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (parsed && typeof parsed === 'object') {
      for (const key of TOKEN_KEYS) {
        const value = (parsed as Record<string, unknown>)[key];
        if (typeof value === 'string' && value.trim()) return value.trim();
      }
    }
  } catch {
    // JSON이 아니면 적힌 그대로 보낸다 (BE가 형식을 판정한다)
  }
  return trimmed;
}

type Outcome = { token: string; res: VerifyResponse<BadgeVerifyData> };

/**
 * 가지고 있는 배지(VC-JWT) 올리기. 서버가 검증 서버(DXWorks)로 서명·발급기관·폐기 여부를 확인한다.
 * - 통과: 지갑에 저장.
 * - 서명·발급자를 확인하지 못했지만 내 DID에 발급됐다고 적힌 배지(위조·변조 의심): '미검증 기록'으로 격리 보관.
 *   AI 포트폴리오·직무 비교를 실행할 때마다 다시 검증하고, 근거로 쓰지 않고 '미검증 기록'으로 표시한다.
 */
export function BadgeUploadCard({ did }: { did: string }) {
  const [text, setText] = useState('');
  const [fileError, setFileError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const token = extractToken(text);

  const upload = useMutation({
    mutationFn: async (value: string) => ({
      token: value,
      res: (await verifyCredential({ type: 'badge', token: value })) as VerifyResponse<BadgeVerifyData>,
    }),
    onSuccess: (result) => {
      setOutcome(result);
      if (result.res.data?.record_id) queryClient.invalidateQueries({ queryKey: ['careers'] });
    },
  });

  async function readFile(file: File | undefined) {
    setFileError(null);
    if (!file) return;
    if (file.size > MAX_TOKEN_CHARS * 2) {
      setFileError('파일이 너무 커요. VC-JWT 파일(.jwt·.txt·.json)을 골라 주세요.');
      return;
    }
    setText(await file.text());
  }

  const data = outcome?.res.data;
  const failed = data?.checks.filter((c) => c.result === 'fail') ?? [];

  return (
    <Card className="flex flex-col gap-4">
      <div>
        <h2 className="text-[17px] font-bold">가지고 있는 배지 올리기</h2>
        <p className="mt-1 text-[13px] leading-relaxed text-[var(--color-text-muted)]">
          받은 배지의 VC-JWT를 붙여 넣거나 파일로 올려요. 검증 서버가 발급 기관의 서명·신뢰와 폐기 여부를 확인해요. 서명을
          확인하지 못한 배지(위조·변조 의심)는 근거로 쓰지 않는 &apos;미검증 기록&apos;으로만 보관해요.
        </p>
      </div>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="eyJhbGciOi... (VC-JWT)"
        rows={4}
        maxLength={MAX_TOKEN_CHARS * 2}
        spellCheck={false}
        className="font-mono-did w-full resize-y rounded-xl border-2 border-transparent bg-[var(--color-surface)] px-4 py-3 text-[13px] outline-none placeholder:text-[#b0b8c1] focus:border-[var(--color-primary)] focus:bg-white"
      />
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={fileRef}
          type="file"
          accept=".jwt,.txt,.json,text/plain,application/json"
          className="hidden"
          onChange={(e) => {
            void readFile(e.target.files?.[0]);
            e.target.value = '';
          }}
        />
        <Button variant="secondary" size="sm" onClick={() => fileRef.current?.click()}>
          파일에서 불러오기
        </Button>
        <Button
          size="sm"
          disabled={!token || token.length > MAX_TOKEN_CHARS}
          isLoading={upload.isPending}
          onClick={() => {
            setOutcome(null);
            upload.mutate(token);
          }}
        >
          검증하고 올리기
        </Button>
        {token.length > MAX_TOKEN_CHARS && (
          <span className="text-[13px] text-[var(--color-danger)]">너무 길어요 (최대 256KB)</span>
        )}
      </div>
      {fileError && <Alert variant="warning" title={fileError} onClose={() => setFileError(null)} />}
      {upload.isError && (
        <Alert variant="error" title="배지를 올리지 못했어요." onClose={() => upload.reset()}>
          {getApiErrorMessage(upload.error)}
        </Alert>
      )}

      {outcome && data && outcome.res.success && (
        <Alert variant="success" title="검증을 통과해 지갑에 저장했어요." onClose={() => setOutcome(null)}>
          <span className="mr-1.5 align-middle">
            <StatusBadge status={data.status} />
          </span>
          {data.achievement_name} · {data.issuer_name}{' '}
          <Link to="/dashboard" className="font-semibold hover:underline">
            지갑에서 확인
          </Link>
        </Alert>
      )}

      {outcome && data && !outcome.res.success && data.unverified_upload && (
        <Alert variant="error" title="서명 검증에 실패해 '미검증 기록'으로 격리 보관했어요." onClose={() => setOutcome(null)}>
          <div className="flex flex-col gap-2">
            <span className="flex flex-wrap items-center gap-1.5">
              <UnverifiedRecordTag />
              <b>{data.achievement_name}</b>
              <span className="text-[var(--color-text-muted)]">· 발급 기관 표기: {data.issuer_name}</span>
            </span>
            <span>
              검증 서버가 발급 기관의 서명·신뢰를 확인하지 못했어요 (위조·변조 의심). 이 기록은 AI 포트폴리오·직무 비교를
              실행할 때마다 다시 검증하고, 근거로 쓰지 않고 결과에 &apos;미검증 기록&apos;으로 표시해요.
            </span>
            {failed.length > 0 && <VerificationChecks checks={failed} />}
          </div>
        </Alert>
      )}

      {outcome && data && !outcome.res.success && !data.unverified_upload && data.record_id && (
        <Alert variant="warning" title="검증을 통과하지 못했어요." onClose={() => setOutcome(null)}>
          <span className="mr-1.5 align-middle">
            <StatusBadge status={data.status} />
          </span>
          발급 기관이 서명한 배지지만 폐기·만료되어 그 상태로 지갑에 기록했어요. 포트폴리오 근거로는 쓰지 않아요.
        </Alert>
      )}

      {outcome && data && !outcome.res.success && !data.record_id && (
        <Alert variant="error" title="검증을 통과하지 못해 저장하지 않았어요." onClose={() => setOutcome(null)}>
          {outcome.res.message === UNVERIFIED_LIMIT_REACHED
            ? '미검증 기록은 10개까지만 보관해요. 이 배지는 서명 검증에 실패했고 저장하지 않았어요.'
            : data.recipient_did && data.recipient_did !== did
              ? '내 DID에 발급된 배지만 올릴 수 있어요. 이 배지는 서명도 확인되지 않았어요.'
              : '서명이 없거나 VC-JWT 형식이 아닌 값이에요 (alg=none 등). 검증 서버로 보내지 않았어요.'}
          {failed.length > 0 && (
            <div className="mt-2">
              <VerificationChecks checks={failed} />
            </div>
          )}
        </Alert>
      )}
    </Card>
  );
}

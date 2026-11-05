import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { buildShareUrl, createShare, listShares, revokeShare } from '@/api/shares';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { getApiErrorMessage } from '@/lib/apiClient';
import { useCopy } from '@/lib/useCopy';

const EXPIRY_OPTIONS = [7, 14, 30, 90];
const formatDate = (iso: string) => new Date(iso).toLocaleDateString('ko-KR');

/**
 * 선택 공개 링크 (BE v0.6.0 /shares).
 * 고른 자격증명만 기업에 보여 주는 링크를 만든다. 링크 주소(토큰)는 만든 직후 한 번만 볼 수 있다.
 */
export function ShareLinksCard({ selectedIds }: { selectedIds: string[] }) {
  const queryClient = useQueryClient();
  const { copy, isCopied } = useCopy();
  const [label, setLabel] = useState('');
  const [days, setDays] = useState(14);
  const [createdUrl, setCreatedUrl] = useState<string | null>(null);

  const { data, dataUpdatedAt, isLoading, isError, error } = useQuery({ queryKey: ['shares'], queryFn: listShares });
  const shares = data?.data ?? [];

  const create = useMutation({
    mutationFn: () => createShare({ record_ids: selectedIds, label, expires_in_days: days }),
    onSuccess: (res) => {
      setCreatedUrl(buildShareUrl(res.data.share_path));
      setLabel('');
      queryClient.invalidateQueries({ queryKey: ['shares'] });
    },
  });

  const revoke = useMutation({
    mutationFn: (id: string) => revokeShare(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['shares'] }),
  });

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="text-[17px] font-bold">기업에 선택 공개하기</h2>
        <p className="text-[13px] leading-relaxed text-[var(--color-text-muted)]">
          왼쪽에서 고른 {selectedIds.length}개 자격증명만 볼 수 있는 링크를 만들어요. 기업이 열 때마다 서명 원본을 다시
          검증하고, 이메일은 보여주지 않아요.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-[1fr_140px]">
        <Input
          id="shareLabel"
          label="링크 이름 (나만 보여요)"
          placeholder="예: OO회사 백엔드 지원"
          maxLength={100}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <div className="flex flex-col gap-2">
          <label htmlFor="shareDays" className="text-[13px] font-medium text-[var(--color-text-sub)]">
            유효 기간
          </label>
          <select
            id="shareDays"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
            className="h-12 rounded-xl border-2 border-transparent bg-[var(--color-surface)] px-3 text-[15px] outline-none focus:border-[var(--color-primary)] focus:bg-white"
          >
            {EXPIRY_OPTIONS.map((d) => (
              <option key={d} value={d}>
                {d}일
              </option>
            ))}
          </select>
        </div>
      </div>
      <Button
        disabled={selectedIds.length === 0}
        isLoading={create.isPending}
        onClick={() => {
          setCreatedUrl(null);
          create.mutate();
        }}
      >
        공유 링크 만들기
      </Button>

      {create.isError && (
        <Alert variant="error" title="링크를 만들지 못했습니다." onClose={() => create.reset()}>
          {getApiErrorMessage(create.error)}
        </Alert>
      )}
      {createdUrl && (
        <Alert
          variant="success"
          title="공유 링크를 만들었어요. 이 주소는 지금만 볼 수 있어요."
          onClose={() => setCreatedUrl(null)}
          actions={
            <Button variant="outline" size="sm" onClick={() => copy(createdUrl, 'url')}>
              {isCopied('url') ? '복사됨' : '링크 복사'}
            </Button>
          }
        >
          <span className="font-mono-did break-all">{createdUrl}</span>
        </Alert>
      )}

      <div className="flex flex-col gap-2">
        <p className="text-[13px] font-semibold text-[var(--color-text-sub)]">내 공유 링크</p>
        {isLoading && <p className="text-[13px] text-[var(--color-text-muted)]">불러오는 중...</p>}
        {isError && <p className="text-[13px] text-[var(--color-danger)]">{getApiErrorMessage(error)}</p>}
        {!isLoading && !isError && shares.length === 0 && (
          <p className="text-[13px] text-[var(--color-text-muted)]">만든 링크가 없어요.</p>
        )}
        {revoke.isError && (
          <p className="text-[13px] text-[var(--color-danger)]">{getApiErrorMessage(revoke.error)}</p>
        )}
        <ul className="flex flex-col gap-1">
          {shares.map((s) => {
            // 목록을 받아온 시각 기준으로 만료 여부를 판단한다 (렌더 중 Date.now() 호출 회피)
            const expired = new Date(s.expires_at).getTime() < dataUpdatedAt;
            const inactive = s.revoked || expired;
            return (
              <li key={s.id} className="flex items-center justify-between gap-3 rounded-xl px-2 py-2">
                <span className={`min-w-0 ${inactive ? 'opacity-50' : ''}`}>
                  <span className="block truncate text-[15px] font-semibold">{s.label ?? '이름 없는 링크'}</span>
                  <span className="block truncate text-[12px] text-[var(--color-text-muted)]">
                    자격증명 {s.record_ids.length}개 · 열람 {s.view_count}회 ·{' '}
                    {s.revoked ? '철회됨' : expired ? '만료됨' : `${formatDate(s.expires_at)}까지`}
                  </span>
                </span>
                {!inactive && (
                  <Button
                    variant="tertiary"
                    size="sm"
                    isLoading={revoke.isPending && revoke.variables === s.id}
                    onClick={() => {
                      if (window.confirm('이 링크를 철회할까요? 기업이 더 이상 열 수 없게 됩니다.')) revoke.mutate(s.id);
                    }}
                  >
                    철회
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </Card>
  );
}

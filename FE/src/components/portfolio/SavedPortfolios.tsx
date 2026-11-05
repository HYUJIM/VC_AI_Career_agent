import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { deletePortfolio, getPortfolio, listPortfolios } from '@/api/portfolios';
import { ExcludedList } from '@/components/portfolio/ExcludedList';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Modal } from '@/components/ui/Modal';
import { getApiErrorMessage } from '@/lib/apiClient';
import { useCopy } from '@/lib/useCopy';

const formatDate = (iso: string) => new Date(iso).toLocaleDateString('ko-KR');

function PortfolioDetailModal({ id, onClose }: { id: string; onClose: () => void }) {
  const { copy, isCopied } = useCopy();
  const queryClient = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['portfolio', id],
    queryFn: () => getPortfolio(id),
  });
  const remove = useMutation({
    mutationFn: () => deletePortfolio(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['portfolios'] });
      onClose();
    },
  });
  const portfolio = data?.data;

  return (
    <Modal
      open
      title={portfolio?.title ?? '포트폴리오'}
      onClose={onClose}
      footer={
        <>
          <Button
            variant="tertiary"
            className="flex-1"
            isLoading={remove.isPending}
            onClick={() => {
              if (window.confirm('이 포트폴리오를 삭제할까요?')) remove.mutate();
            }}
          >
            삭제
          </Button>
          <Button
            variant="secondary"
            className="flex-1"
            disabled={!portfolio}
            onClick={() => portfolio && copy(portfolio.content_markdown)}
          >
            {isCopied() ? '복사됨' : '본문 복사'}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {isLoading && <p className="text-[13px] text-[var(--color-text-muted)]">불러오는 중...</p>}
        {isError && (
          <Alert variant="error" title="포트폴리오를 불러오지 못했습니다.">
            {getApiErrorMessage(error)}
          </Alert>
        )}
        {remove.isError && (
          <Alert variant="error" title="삭제하지 못했습니다.">
            {getApiErrorMessage(remove.error)}
          </Alert>
        )}
        {portfolio && (
          <>
            <p className="text-[13px] text-[var(--color-text-muted)]">
              {portfolio.target_role ? `${portfolio.target_role} · ` : ''}
              검증된 자격증명 {portfolio.credential_count}개 · {formatDate(portfolio.created_at)} 생성 · 근거 출처{' '}
              {portfolio.source}
            </p>
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-2xl bg-[var(--color-surface)] px-4 py-3 text-[14px] leading-relaxed">
              {portfolio.content_markdown}
            </pre>
            {portfolio.excluded.length > 0 && (
              <div className="flex flex-col gap-2">
                <p className="text-[13px] font-semibold text-[var(--color-text-sub)]">
                  검증을 통과하지 못해 빠진 자격증명
                </p>
                <ExcludedList excluded={portfolio.excluded} />
              </div>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

/** 저장된 포트폴리오 목록 (BE v0.6.0 GET /portfolios) */
export function SavedPortfolios() {
  const [openId, setOpenId] = useState<string | null>(null);
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['portfolios'],
    queryFn: listPortfolios,
  });
  const items = data?.data ?? [];

  return (
    <Card className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between">
        <h2 className="text-[17px] font-bold">저장한 포트폴리오</h2>
        <span className="text-[13px] text-[var(--color-text-muted)]">{items.length}개</span>
      </div>
      {isLoading && <p className="text-[13px] text-[var(--color-text-muted)]">불러오는 중...</p>}
      {isError && (
        <Alert
          variant="error"
          title="목록을 불러오지 못했습니다."
          actions={
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              다시 시도
            </Button>
          }
        >
          {getApiErrorMessage(error)}
        </Alert>
      )}
      {!isLoading && !isError && items.length === 0 && (
        <p className="text-[13px] text-[var(--color-text-muted)]">
          아직 저장한 포트폴리오가 없어요. 미리보기 탭에서 &quot;검증 후 저장&quot;을 눌러보세요.
        </p>
      )}
      <ul className="flex flex-col">
        {items.map((p) => (
          <li key={p.id}>
            <button
              type="button"
              onClick={() => setOpenId(p.id)}
              className="flex w-full items-center justify-between gap-3 rounded-xl px-2 py-2.5 text-left hover:bg-[var(--color-card-hover)]"
            >
              <span className="min-w-0">
                <span className="block truncate text-[15px] font-semibold">{p.title}</span>
                <span className="block truncate text-[13px] text-[var(--color-text-muted)]">
                  {p.target_role ? `${p.target_role} · ` : ''}자격증명 {p.credential_count}개 · {formatDate(p.updated_at)}
                </span>
              </span>
              <span className="shrink-0 text-[13px] text-[var(--color-text-muted)]">
                {p.status === 'published' ? '게시됨' : '초안'}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {openId && <PortfolioDetailModal id={openId} onClose={() => setOpenId(null)} />}
    </Card>
  );
}

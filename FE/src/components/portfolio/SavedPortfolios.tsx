import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { deletePortfolio, getPortfolio, listPortfolios, updatePortfolio } from '@/api/portfolios';
import { ExcludedList } from '@/components/portfolio/ExcludedList';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { Modal } from '@/components/ui/Modal';
import { getApiErrorMessage } from '@/lib/apiClient';
import { useCopy } from '@/lib/useCopy';
import type { Portfolio, PortfolioUpdateRequest } from '@/types/api';

const formatDate = (iso: string) => new Date(iso).toLocaleDateString('ko-KR');

/**
 * 제목·희망 직무 수정 폼 (BE v0.6.0 PATCH /portfolios/{id}).
 * 바뀐 필드만 보낸다. BE가 본문 머리(`# 제목`, `- 희망 직무:` 줄)도 함께 갱신하고,
 * 근거 섹션(VC_ONLY)은 그대로 둔다.
 */
function PortfolioEditForm({
  portfolio,
  isSaving,
  onSave,
  onCancel,
}: {
  portfolio: Portfolio;
  isSaving: boolean;
  onSave: (fields: PortfolioUpdateRequest) => void;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(portfolio.title);
  const [role, setRole] = useState(portfolio.target_role ?? '');

  const fields: PortfolioUpdateRequest = {};
  if (title.trim() !== portfolio.title) fields.title = title.trim();
  if ((role.trim() || null) !== portfolio.target_role) fields.target_role = role.trim() || null;
  const changed = Object.keys(fields).length > 0;

  return (
    <form
      className="flex flex-col gap-3 rounded-2xl bg-[var(--color-surface)] p-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (changed && title.trim()) onSave(fields);
      }}
    >
      <Input
        id="editTitle"
        label="제목"
        maxLength={255}
        value={title}
        error={title.trim() ? undefined : '제목을 입력해주세요.'}
        onChange={(e) => setTitle(e.target.value)}
        className="bg-white"
      />
      <Input
        id="editRole"
        label="희망 직무 (비우면 삭제)"
        maxLength={100}
        value={role}
        onChange={(e) => setRole(e.target.value)}
        className="bg-white"
      />
      <p className="text-[12px] leading-relaxed text-[var(--color-text-muted)]">
        본문 맨 위의 제목·희망 직무도 함께 바뀌어요. 검증된 자격증명 내용은 그대로 유지되며 직접 수정할 수 없어요.
        자격증명 구성을 바꾸려면 새로 만들어주세요.
      </p>
      <div className="flex gap-2">
        <Button type="button" variant="tertiary" size="sm" className="flex-1" onClick={onCancel}>
          취소
        </Button>
        <Button type="submit" size="sm" className="flex-1" disabled={!changed || !title.trim()} isLoading={isSaving}>
          저장
        </Button>
      </div>
    </form>
  );
}

function PortfolioDetailModal({ id, onClose }: { id: string; onClose: () => void }) {
  const { copy, isCopied } = useCopy();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['portfolio', id],
    queryFn: () => getPortfolio(id),
  });
  const update = useMutation({
    mutationFn: (fields: PortfolioUpdateRequest) => updatePortfolio(id, fields),
    onSuccess: (res) => {
      queryClient.setQueryData(['portfolio', id], res);
      queryClient.invalidateQueries({ queryKey: ['portfolios'] });
      setEditing(false);
    },
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
        {update.isError && (
          <Alert variant="error" title="수정하지 못했습니다." onClose={() => update.reset()}>
            {getApiErrorMessage(update.error)}
          </Alert>
        )}
        {portfolio && (
          <>
            <div className="flex items-start justify-between gap-3">
              <p className="text-[13px] text-[var(--color-text-muted)]">
                {portfolio.target_role ? `${portfolio.target_role} · ` : ''}
                검증된 자격증명 {portfolio.credential_count}개 · {formatDate(portfolio.created_at)} 생성 · 근거 출처{' '}
                {portfolio.source}
              </p>
              <span
                className={`shrink-0 rounded-md px-2 py-0.5 text-xs font-semibold ${
                  portfolio.status === 'published'
                    ? 'bg-[#e9f8f1] text-[#029359]'
                    : 'bg-[var(--color-surface)] text-[var(--color-text-sub)]'
                }`}
              >
                {portfolio.status === 'published' ? '게시됨' : '초안'}
              </span>
            </div>

            {editing ? (
              <PortfolioEditForm
                portfolio={portfolio}
                isSaving={update.isPending}
                onSave={(fields) => update.mutate(fields)}
                onCancel={() => {
                  update.reset();
                  setEditing(false);
                }}
              />
            ) : (
              <div className="flex gap-2">
                <Button variant="tertiary" size="sm" className="flex-1" onClick={() => setEditing(true)}>
                  제목·직무 수정
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  className="flex-1"
                  isLoading={update.isPending}
                  onClick={() =>
                    update.mutate({ status: portfolio.status === 'published' ? 'draft' : 'published' })
                  }
                >
                  {portfolio.status === 'published' ? '초안으로 되돌리기' : '게시하기'}
                </Button>
              </div>
            )}
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

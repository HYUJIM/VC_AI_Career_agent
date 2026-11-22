import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getCareersByDid } from '@/api/credentials';
import { createPortfolio, regeneratePortfolio } from '@/api/portfolios';
import { CareersErrorAlert } from '@/components/CareersErrorAlert';
import { ExcludedList } from '@/components/portfolio/ExcludedList';
import { SavedPortfolios } from '@/components/portfolio/SavedPortfolios';
import { ShareLinksCard } from '@/components/portfolio/ShareLinksCard';
import { StatusBadge, UnverifiedRecordTag } from '@/components/StatusBadge';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Input } from '@/components/ui/Input';
import { isSendableSingleLine, NO_VISIBLE_TEXT_MESSAGE } from '@/lib/text';
import { getApiErrorMessage } from '@/lib/apiClient';
import { markPortfolioUsed } from '@/lib/onboarding';
import { useCopy } from '@/lib/useCopy';
import { useAuthStore } from '@/store/authStore';
import type { ExcludedEvidence, Portfolio, PortfolioMode, UserProfile, VerifiedCredential } from '@/types/api';

interface ChatMessage {
  id: number;
  role: 'agent' | 'user';
  text: string;
  /** 에이전트 답: 실행 시점 재검증을 통과하지 못해 근거에서 뺀 '미검증 기록' */
  excluded?: ExcludedEvidence[];
}

const SUGGESTIONS = ['경력 중심으로 강조해줘', '해커톤 수상 내역 추가', 'GitHub 링크 반영'];
const PAGE_SIZE = 10;

type PanelTab = 'chat' | 'preview';

/** 저장 전 미리보기: 선택한 자격증명으로 브라우저에서 만드는 초안 (실제 본문은 서버가 다시 검증해 만든다) */
function buildDraft(user: UserProfile, selected: VerifiedCredential[], direction: string | null): string {
  const items = selected.filter((c) => c.status === 'verified');
  const unverified = selected.filter((c) => c.status !== 'verified');
  const lines = [
    `# ${user.name} 포트폴리오`,
    '',
    `- 이메일: ${user.email}`,
    `- DID: ${user.did}`,
    direction ? `- 작성 방향: ${direction}` : null,
    '',
    '## 검증된 자격증명',
    ...(items.length > 0
      ? [...items]
          .sort((a, b) => +new Date(b.issued_at) - +new Date(a.issued_at))
          .map(
            (c) =>
              `- ${c.achievement_name} — ${c.issuer} (${new Date(c.issued_at).toLocaleDateString('ko-KR')} 발급, 블록체인 검증 완료)`,
          )
      : ['- (선택된 자격증명이 없습니다)']),
    ...(unverified.length > 0
      ? ['', '## 미검증 기록 (근거로 쓰지 않음)', ...unverified.map((c) => `- [미검증 기록] ${c.achievement_name} — ${c.issuer}`)]
      : []),
    '',
    '## 요약',
    items.length > 0
      ? `${new Set(items.map((c) => c.issuer)).size}개 기관에서 발급한 ${items.length}개의 검증된 자격증명을 근거로 역량을 증명합니다.`
      : '왼쪽에서 포함할 자격증명을 선택해주세요.',
  ];
  return lines.filter((l) => l !== null).join('\n');
}

/** 서버 본문에서 AI가 쓴 요약 한 줄 (BE portfolio_writer.AI_SUMMARY_NOTE 다음 줄). 없으면 null */
function aiSummaryOf(content: string): string | null {
  const lines = content.split('\n');
  const note = lines.findIndex((l) => l.startsWith('(AI 작성:'));
  if (note < 0) return null;
  return lines.slice(note + 1).find((l) => l.trim()) ?? null;
}

/** 대화창에 남기는 에이전트 답 (만든 방식과 빠진 자격증명) */
function describeResult(p: Portfolio, direction: string): string {
  const excluded =
    p.excluded.length > 0
      ? `\n\n⚠ 미검증 기록 ${p.excluded.length}개: 만들 때 서명 원본을 다시 검증했더니 통과하지 못해 근거에서 뺐어요.`
      : '';
  if (p.generator === 'llm') {
    const summary = aiSummaryOf(p.content_markdown);
    return (
      `Claude가 "${direction}" 방향으로 소개를 썼어요. 사실 근거는 검증된 자격증명 ${p.credential_count}개뿐이에요.` +
      (summary ? `\n\n${summary}` : '') +
      `\n\n미리보기 탭에서 전체 본문을 확인하세요.${excluded}`
    );
  }
  if (p.generator === 'llm_fallback') {
    return (
      'Claude가 쓴 문장이 검증 기준(근거에 없는 숫자·링크 등)을 통과하지 못해 버리고, 검증된 자격증명만으로 본문을 만들었어요. ' +
      `방향을 조금 바꿔 다시 보내 보세요.${excluded}`
    );
  }
  return (
    `검증된 자격증명 ${p.credential_count}개로 본문을 만들었어요. 소개 문장을 Claude로 쓰려면 아래 "Claude로 작성"에 동의해 주세요.` +
    excluded
  );
}

/** AI 결과 옆에 붙이는 '미검증 기록' 경고: 만들 때 다시 검증했더니 통과하지 못해 근거에서 뺀 기록 */
function UnverifiedBox({ excluded, names }: { excluded: ExcludedEvidence[]; names: Map<string, string> }) {
  return (
    <div className="flex shrink-0 flex-col gap-2 rounded-2xl border border-[#f5c2c7] bg-[#fff8f8] px-4 py-3">
      <p className="text-[13px] font-semibold text-[#d22030]">
        ⚠ 미검증 기록 {excluded.length}개 — 실행 시점 재검증을 통과하지 못해 AI 결과의 근거로 쓰지 않았어요
      </p>
      <ExcludedList excluded={excluded} names={names} />
    </div>
  );
}

export function PortfolioBuilderPage() {
  const user = useAuthStore((state) => state.user);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState<PanelTab>('chat');
  const [direction, setDirection] = useState<string | null>(null);
  const { copy, isCopied } = useCopy();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [thinking, setThinking] = useState(false);
  // "Claude로 작성" 동의 (체크해야 AI 모드로 보낸다)
  const [useAi, setUseAi] = useState(false);
  // 대화로 만들고 고쳐 쓰는 포트폴리오 (첫 메시지에 만들고, 이후 메시지는 같은 포트폴리오를 다시 쓴다)
  const [current, setCurrent] = useState<Portfolio | null>(null);
  const [title, setTitle] = useState('');
  const [targetRole, setTargetRole] = useState('');
  // 보낼 값에 보이는 글자가 있어야 BE가 받는다 (결합 문자·이형 선택자만으로 된 값은 422)
  const titleOk = isSendableSingleLine(title, true);
  const roleOk = isSendableSingleLine(targetRole);
  const [page, setPage] = useState(1);
  const chatRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ['careers', user?.did],
    queryFn: () => getCareersByDid(user!.did),
    enabled: Boolean(user?.did),
  });

  // 지갑의 모든 자격증명을 보여 준다. 저장된 상태를 믿지 않고 만들 때 서명 원본을 다시 검증해, 통과한 것만 근거로 쓰고
  // 나머지(위조·변조 의심 업로드, 폐기·만료 등)는 결과에 '미검증 기록'으로 표시한다.
  const credentials = data?.verified_credentials ?? [];
  const verifiedIds = new Set(credentials.filter((c) => c.status === 'verified').map((c) => c.record_id));
  const credentialNames = new Map(credentials.map((c) => [c.record_id, c.achievement_name]));
  const totalPages = Math.max(1, Math.ceil(credentials.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pagedCredentials = credentials.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);

  // BE v0.6.0 VC_ONLY 포트폴리오: 자격증명 ID만 보내고, BE가 지금 다시 검증해 통과한 것으로만 본문을 만든다.
  const mode: PortfolioMode = useAi ? 'llm' : 'template';
  const save = useMutation({
    mutationFn: () =>
      createPortfolio({
        title,
        target_role: targetRole,
        record_ids: credentials.filter((c) => selected.has(c.record_id)).map((c) => c.record_id),
        mode,
        allow_external_processing: useAi,
        direction,
      }),
    onSuccess: (res) => {
      setCurrent(res.data);
      markPortfolioUsed(user!.did);
      queryClient.invalidateQueries({ queryKey: ['portfolios'] });
      // 재검증으로 상태가 바뀌었을 수 있으므로 지갑도 다시 불러온다.
      queryClient.invalidateQueries({ queryKey: ['careers'] });
    },
  });
  const saved = save.data?.data;

  // 지갑의 자격증명은 기본으로 모두 포함 (검증을 통과하지 못한 것은 결과에 '미검증 기록'으로 남는다)
  useEffect(() => {
    setSelected(new Set(credentials.map((c) => c.record_id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  useEffect(() => {
    if (!user) return;
    setMessages([
      {
        id: 0,
        role: 'agent',
        text:
          `안녕하세요 ${user.name} 님! 왼쪽에서 포함할 자격증명을 고르고 강조하고 싶은 방향을 보내 주세요. ` +
          '서버가 고른 자격증명을 지금 다시 검증해 통과한 것만으로 포트폴리오를 만들고, 통과하지 못한 것은 ' +
          "'미검증 기록'으로 표시해요. " +
          '아래 "Claude로 작성"에 동의하면 요약과 관련성 문장을 Claude가 써요.',
      },
    ]);
  }, [user]);

  // 새 메시지가 오면 대화창 내부만 맨 아래로 스크롤한다.
  // scrollIntoView는 페이지(window)까지 스크롤해서 탭 진입 시 화면이 중간부터 보이므로 쓰지 않는다.
  useEffect(() => {
    const el = chatRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [messages, thinking]);

  if (!user) return null;

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || thinking) return;
    const recordIds = credentials.filter((c) => selected.has(c.record_id)).map((c) => c.record_id);
    setMessages((prev) => [...prev, { id: Date.now(), role: 'user', text: trimmed }]);
    setInput('');
    if (recordIds.length === 0) {
      setMessages((prev) => [
        ...prev,
        { id: Date.now() + 1, role: 'agent', text: '왼쪽에서 포함할 자격증명을 먼저 골라 주세요.' },
      ]);
      return;
    }
    setThinking(true);
    setDirection(trimmed);
    const llm = { mode, allow_external_processing: useAi, direction: trimmed };
    try {
      const res = current
        ? await regeneratePortfolio(current.id, { record_ids: recordIds, ...llm })
        : await createPortfolio({
            title: titleOk ? title : `${user!.name} 님 포트폴리오`,
            target_role: roleOk ? targetRole : null,
            record_ids: recordIds,
            ...llm,
          });
      setCurrent(res.data);
      markPortfolioUsed(user!.did);
      queryClient.invalidateQueries({ queryKey: ['portfolios'] });
      queryClient.invalidateQueries({ queryKey: ['careers'] });
      setMessages((prev) => [
        ...prev,
        { id: Date.now() + 1, role: 'agent', text: describeResult(res.data, trimmed), excluded: res.data.excluded },
      ]);
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        { id: Date.now() + 1, role: 'agent', text: `포트폴리오를 만들지 못했어요. ${getApiErrorMessage(error)}` },
      ]);
    } finally {
      setThinking(false);
    }
  }

  const draft = buildDraft(
    user,
    credentials.filter((c) => selected.has(c.record_id)),
    direction,
  );

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-2 px-1">
        <h1 className="text-[26px] font-bold leading-snug tracking-tight">AI 포트폴리오 만들기</h1>
        <p className="text-[15px] text-[var(--color-text-sub)]">
          만들 때 서명 원본을 다시 검증해 통과한 자격증명만 근거로 쓰고, 통과하지 못한 기록은 &apos;미검증 기록&apos;으로
          표시해요.
        </p>
      </section>

      {isError && <CareersErrorAlert error={error} onRetry={() => refetch()} isFetching={isFetching} />}

      <div className="grid items-start gap-4 lg:grid-cols-[340px_1fr]">
        <Card className="flex h-[760px] flex-col gap-3 px-0 py-5">
          <div className="flex items-baseline justify-between px-5">
            <h2 className="text-[17px] font-bold">포함할 자격증명</h2>
            <span className="text-[13px] text-[var(--color-text-muted)]">
              {selected.size}/{credentials.length} 선택됨
            </span>
          </div>
          {credentials.length > 0 && (
            <div className="flex gap-2 px-5">
              <Button
                variant="tertiary"
                size="sm"
                className="flex-1"
                disabled={selected.size === credentials.length}
                onClick={() => setSelected(new Set(credentials.map((c) => c.record_id)))}
              >
                전체 선택
              </Button>
              <Button
                variant="tertiary"
                size="sm"
                className="flex-1"
                disabled={selected.size === 0}
                onClick={() => setSelected(new Set())}
              >
                전체 해제
              </Button>
            </div>
          )}
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
            {isLoading && (
              <p className="px-5 text-[13px] text-[var(--color-text-muted)]">불러오는 중...</p>
            )}
            {!isLoading && !isError && credentials.length === 0 && (
              <p className="px-5 text-[13px] text-[var(--color-text-muted)]">
                자격증명이 없어요.{' '}
                <Link to="/issue" className="font-semibold text-[var(--color-primary)] hover:underline">
                  발급받기
                </Link>
              </p>
            )}
            <ul className="flex-1">
              {pagedCredentials.map((c) => (
                <li key={c.record_id}>
                  <label className="flex cursor-pointer items-center gap-3 px-5 py-2.5 hover:bg-[var(--color-card-hover)]">
                    <input
                      type="checkbox"
                      checked={selected.has(c.record_id)}
                      onChange={() => toggle(c.record_id)}
                      className="h-5 w-5 shrink-0 accent-[var(--color-primary)]"
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px] font-semibold">{c.achievement_name}</span>
                      <span className="block truncate text-[13px] text-[var(--color-text-muted)]">
                        {c.issuer}
                      </span>
                    </span>
                    {c.unverified_upload ? (
                      <UnverifiedRecordTag title="올릴 때 서명·발급자를 확인하지 못해 격리 보관한 기록" />
                    ) : (
                      c.status !== 'verified' && <StatusBadge status={c.status} />
                    )}
                  </label>
                </li>
              ))}
            </ul>
          </div>
          <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-[var(--color-border)] px-5 pt-3.5">
            <Button
              variant="secondary"
              size="sm"
              disabled={currentPage <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              ‹ 이전
            </Button>
            <div className="flex items-center gap-1">
              {Array.from({ length: totalPages }, (_, idx) => idx + 1).map((pageNum) => {
                const active = pageNum === currentPage;
                return (
                  <button
                    key={pageNum}
                    type="button"
                    onClick={() => setPage(pageNum)}
                    className={`flex h-8 min-w-8 items-center justify-center rounded-lg px-2 text-[13px] font-bold transition-colors ${
                      active
                        ? 'bg-[var(--color-primary)] text-white shadow-sm'
                        : 'bg-[var(--color-surface)] text-[var(--color-text-sub)] hover:bg-[var(--color-primary-weak)] hover:text-[var(--color-primary)]'
                    }`}
                  >
                    {pageNum}
                  </button>
                );
              })}
            </div>
            <Button
              variant="secondary"
              size="sm"
              disabled={currentPage >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >
              다음 ›
            </Button>
          </div>
        </Card>

        <Card className="flex h-[760px] flex-col p-0">
          <div className="flex items-center justify-between gap-2 border-b border-[var(--color-border)] px-5 py-3">
            <div className="flex items-center gap-2">
              <span className="text-[15px] font-bold">커리어 에이전트</span>
              <span className="hidden text-[13px] text-[var(--color-text-muted)] sm:inline">
                {thinking ? '답변 작성 중' : '대화 가능'}
              </span>
            </div>
            <div className="flex rounded-xl bg-[var(--color-surface)] p-1" role="tablist" aria-label="보기 전환">
              {(
                [
                  ['chat', '대화'],
                  ['preview', '미리보기'],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={tab === key}
                  onClick={() => setTab(key)}
                  className={`rounded-lg px-3 py-1 text-[13px] transition-colors ${
                    tab === key
                      ? 'bg-white font-semibold text-[var(--color-text)] shadow-sm'
                      : 'text-[var(--color-text-muted)]'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {tab === 'preview' && (
            <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 py-4">
              <div className="grid shrink-0 gap-3 sm:grid-cols-2">
                <Input
                  id="portfolioTitle"
                  label="제목"
                  placeholder="예: 백엔드 개발자 지원용"
                  maxLength={255}
                  value={title}
                  error={title.trim() && !titleOk ? NO_VISIBLE_TEXT_MESSAGE : undefined}
                  onChange={(e) => setTitle(e.target.value)}
                />
                <Input
                  id="portfolioRole"
                  label="희망 직무 (선택)"
                  placeholder="예: 백엔드 개발자"
                  maxLength={100}
                  value={targetRole}
                  error={roleOk ? undefined : NO_VISIBLE_TEXT_MESSAGE}
                  onChange={(e) => setTargetRole(e.target.value)}
                />
              </div>
              <Button
                className="shrink-0"
                disabled={!titleOk || !roleOk || selected.size === 0}
                isLoading={save.isPending}
                onClick={() => save.mutate()}
              >
                {useAi ? '검증 후 Claude로 작성해 새로 저장' : '검증 후 포트폴리오 새로 저장'}
              </Button>
              <p className="shrink-0 text-[12px] leading-relaxed text-[var(--color-text-muted)]">
                자유 텍스트 본문 없이 고른 자격증명만 보내요(VC_ONLY). 서버가 저장 직전에 서명 원본을 다시 검증해 통과한
                것만 본문에 넣어요. "Claude로 작성"에 동의하면 요약과 관련성 문장만 Claude가 쓰고, 근거(이름·발급 기관·날짜·검증
                결과)는 서버가 써요.
              </p>

              {save.isError && (
                <div className="shrink-0">
                  <Alert variant="error" title="포트폴리오를 저장하지 못했습니다." onClose={() => save.reset()}>
                    {getApiErrorMessage(save.error)}
                  </Alert>
                </div>
              )}
              {saved && (
                <div className="shrink-0">
                  <Alert variant="success" title={`"${saved.title}" 포트폴리오를 저장했어요.`} onClose={() => save.reset()}>
                    검증된 자격증명 {saved.credential_count}개로 본문을 만들었어요.
                    {saved.generator === 'llm' && ' 요약·관련성 문장은 Claude가 썼어요.'}
                    {saved.generator === 'llm_fallback' &&
                      ' Claude 문장이 검증 기준을 통과하지 못해 검증된 자격증명만으로 만들었어요.'}
                    {saved.sections.length > 0 && (
                      <div className="mt-2.5 flex flex-col gap-1.5">
                        <span className="font-semibold text-[#029359]">검증을 통과해 반영된 자격증명</span>
                        <ul className="flex flex-col gap-1.5">
                          {saved.sections.map((sec) => (
                            <li key={sec.record_id} className="flex items-center justify-between gap-2 text-[13px]">
                              <span className="min-w-0 truncate text-[var(--color-text-sub)]">
                                <span className="font-medium text-[var(--color-text)]">{sec.achievement_name}</span>
                                <span className="ml-1.5 text-[var(--color-text-muted)]">· {sec.issuer_name}</span>
                              </span>
                              <StatusBadge status={sec.status} />
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {saved.excluded.length > 0 && (
                      <div className="mt-2.5 flex flex-col gap-1.5">
                        <span className="font-semibold text-[#d22030]">미검증 기록 (실행 시점 재검증 실패, 근거에서 제외)</span>
                        <ExcludedList excluded={saved.excluded} names={credentialNames} />
                      </div>
                    )}
                  </Alert>
                </div>
              )}

              <div className="flex shrink-0 items-center justify-between gap-2 pt-1">
                <p className="text-[13px] font-medium text-[var(--color-text-sub)]">
                  {current
                    ? `서버가 만든 본문${current.generator === 'llm' ? ` (Claude 문장 포함 · ${current.model ?? ''})` : ''}`
                    : `선택한 ${selected.size}개 자격증명으로 만든 초안 (저장 전 미리보기)`}
                </p>
                <Button variant="secondary" size="sm" onClick={() => copy(current?.content_markdown ?? draft)}>
                  {isCopied() ? '복사됨' : '본문 복사'}
                </Button>
              </div>
              {current && current.excluded.length > 0 && (
                <UnverifiedBox excluded={current.excluded} names={credentialNames} />
              )}
              <pre className="min-h-[300px] shrink-0 whitespace-pre-wrap break-words rounded-2xl bg-[var(--color-surface)] p-4 text-[14px] leading-relaxed text-[var(--color-text)]">
                {current?.content_markdown ?? draft}
              </pre>
            </div>
          )}

          <div
            ref={chatRef}
            className={`flex-1 flex-col gap-3 overflow-y-auto px-5 py-4 ${tab === 'chat' ? 'flex' : 'hidden'}`}
          >
            {messages.map((m) => (
              <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : ''}`}>
                <div className="flex max-w-[80%] flex-col gap-2">
                  <p
                    className={`whitespace-pre-wrap px-4 py-2.5 text-[15px] leading-relaxed ${
                      m.role === 'user'
                        ? 'rounded-[20px] rounded-br-md bg-[var(--color-primary)] text-white'
                        : 'rounded-[20px] rounded-bl-md bg-[var(--color-surface)] text-[var(--color-text)]'
                    }`}
                  >
                    {m.text}
                  </p>
                  {m.excluded && m.excluded.length > 0 && <UnverifiedBox excluded={m.excluded} names={credentialNames} />}
                </div>
              </div>
            ))}
            {thinking && (
              <p className="w-fit rounded-[20px] rounded-bl-md bg-[var(--color-surface)] px-4 py-2.5 text-[15px] text-[var(--color-text-muted)]">
                ···
              </p>
            )}
          </div>

          <div
            className={`flex-col gap-3 border-t border-[var(--color-border)] px-5 py-4 ${tab === 'chat' ? 'flex' : 'hidden'}`}
          >
            <div className="flex gap-2 overflow-x-auto">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => send(s)}
                  className="shrink-0 rounded-full bg-[var(--color-surface)] px-3 py-1.5 text-[13px] font-medium text-[var(--color-text-sub)] hover:bg-[var(--color-border)]"
                >
                  {s}
                </button>
              ))}
            </div>
            <label className="flex items-start gap-2 text-[12px] leading-relaxed text-[var(--color-text-sub)]">
              <input
                type="checkbox"
                checked={useAi}
                onChange={(e) => setUseAi(e.target.checked)}
                className="mt-0.5 h-4 w-4 shrink-0 accent-[var(--color-primary)]"
              />
              <span>
                <b>Claude로 작성</b>: 고른 자격증명의 이름·발급 기관·발급일·설명·성취 기준과 희망 직무, 보낸 작성 방향이
                외부 서비스 Anthropic Claude API로 전달되는 것에 동의해요. 이름·이메일·DID와 자격증명 원문은 전달되지 않아요.
                전달된 내용은 Anthropic의 정책에 따라 처리·보관돼요.
              </span>
            </label>
            {current && (
              <p className="text-[12px] text-[var(--color-text-muted)]">
                지금 대화는 &quot;{current.title}&quot; 포트폴리오를 고쳐 써요.{' '}
                <button
                  type="button"
                  className="font-semibold text-[var(--color-primary)] underline"
                  onClick={() => setCurrent(null)}
                >
                  새 포트폴리오로 시작
                </button>
              </p>
            )}
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                send(input);
              }}
            >
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="강조하고 싶은 역량을 알려주세요"
                className="h-11 min-w-0 flex-1 rounded-xl border-2 border-transparent bg-[var(--color-surface)] px-4 text-[15px] outline-none placeholder:text-[#b0b8c1] focus:border-[var(--color-primary)] focus:bg-white"
              />
              <Button type="submit" disabled={!input.trim() || thinking}>
                전송
              </Button>
            </form>
          </div>
        </Card>
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <SavedPortfolios />
        <ShareLinksCard
          selectedIds={credentials
            .filter((c) => selected.has(c.record_id) && verifiedIds.has(c.record_id))
            .map((c) => c.record_id)}
        />
      </div>
    </div>
  );
}

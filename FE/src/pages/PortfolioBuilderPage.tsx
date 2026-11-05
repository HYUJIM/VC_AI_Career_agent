import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getCareersByDid } from '@/api/credentials';
import { CareersErrorAlert } from '@/components/CareersErrorAlert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { markPortfolioUsed } from '@/lib/onboarding';
import { useCopy } from '@/lib/useCopy';
import { useAuthStore } from '@/store/authStore';
import type { UserProfile, VerifiedCredential } from '@/types/api';

interface ChatMessage {
  id: number;
  role: 'agent' | 'user';
  text: string;
}

const SUGGESTIONS = ['경력 중심으로 강조해줘', '해커톤 수상 내역 추가', 'GitHub 링크 반영'];

type PanelTab = 'chat' | 'preview';

/** 선택한 자격증명과 마지막 요청으로 만드는 포트폴리오 초안 (AI 연동 전 화면 확인용) */
function buildDraft(user: UserProfile, items: VerifiedCredential[], direction: string | null): string {
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
    '',
    '## 요약',
    items.length > 0
      ? `${new Set(items.map((c) => c.issuer)).size}개 기관에서 발급한 ${items.length}개의 검증된 자격증명을 근거로 역량을 증명합니다.`
      : '왼쪽에서 포함할 자격증명을 선택해주세요.',
  ];
  return lines.filter((l) => l !== null).join('\n');
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
  const bottomRef = useRef<HTMLDivElement>(null);

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['careers', user?.did],
    queryFn: () => getCareersByDid(user!.did),
    enabled: Boolean(user?.did),
  });

  const credentials = (data?.verified_credentials ?? []).filter((c) => c.status === 'verified');

  // 검증된 자격증명은 기본으로 모두 포함
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
        text: `안녕하세요 ${user.name} 님! 검증된 DID 지갑에서 자격증명을 확인했습니다. 어떤 직무나 타겟 기업을 목표로 포트폴리오를 구성할까요?`,
      },
    ]);
  }, [user]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
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

  function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || thinking) return;
    setMessages((prev) => [...prev, { id: Date.now(), role: 'user', text: trimmed }]);
    setInput('');
    setThinking(true);
    setDirection(trimmed);
    markPortfolioUsed(user!.did);
    // TODO: AI 서버 연동 시 실제 호출로 교체 (현재는 화면 확인용 모의 응답)
    setTimeout(() => {
      const names = credentials
        .filter((c) => selected.has(c.record_id))
        .map((c) => c.achievement_name)
        .join(', ');
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now() + 1,
          role: 'agent',
          text: `확인했습니다! 블록체인으로 검증된 자격증명(${names || '선택된 항목 없음'})을 근거로 "${trimmed}" 방향의 포트폴리오 초안을 구성하겠습니다. (AI 연동 전 모의 응답입니다)`,
        },
      ]);
      setThinking(false);
    }, 900);
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
          검증된 자격증명만 골라서 포트폴리오의 근거로 사용해요.
        </p>
      </section>

      {isError && <CareersErrorAlert error={error} onRetry={() => refetch()} />}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <Card className="flex h-fit flex-col gap-3 px-0 py-5">
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
          {isLoading && (
            <p className="px-5 text-[13px] text-[var(--color-text-muted)]">불러오는 중...</p>
          )}
          {!isLoading && !isError && credentials.length === 0 && (
            <p className="px-5 text-[13px] text-[var(--color-text-muted)]">
              검증된 자격증명이 없어요.{' '}
              <Link to="/issue" className="font-semibold text-[var(--color-primary)] hover:underline">
                발급받기
              </Link>
            </p>
          )}
          <ul>
            {credentials.map((c) => (
              <li key={c.record_id}>
                <label className="flex cursor-pointer items-center gap-3 px-5 py-2.5 hover:bg-[var(--color-card-hover)]">
                  <input
                    type="checkbox"
                    checked={selected.has(c.record_id)}
                    onChange={() => toggle(c.record_id)}
                    className="h-5 w-5 shrink-0 accent-[var(--color-primary)]"
                  />
                  <span className="min-w-0">
                    <span className="block truncate text-[15px] font-semibold">{c.achievement_name}</span>
                    <span className="block truncate text-[13px] text-[var(--color-text-muted)]">
                      {c.issuer}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </Card>

        <Card className="flex h-[600px] flex-col p-0">
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
            <div className="flex flex-1 flex-col gap-3 overflow-hidden px-5 py-4">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[13px] text-[var(--color-text-muted)]">
                  선택한 {selected.size}개 자격증명으로 만든 초안 (AI 연동 전 모의 초안)
                </p>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => copy(draft)}
                >
                  {isCopied() ? '복사됨' : '초안 복사'}
                </Button>
              </div>
              <pre className="flex-1 overflow-auto whitespace-pre-wrap rounded-2xl bg-[var(--color-surface)] px-4 py-3 text-[14px] leading-relaxed text-[var(--color-text)]">
                {draft}
              </pre>
            </div>
          )}

          <div
            className={`flex-1 flex-col gap-3 overflow-y-auto px-5 py-4 ${tab === 'chat' ? 'flex' : 'hidden'}`}
          >
            {messages.map((m) => (
              <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : ''}`}>
                <p
                  className={`max-w-[80%] whitespace-pre-wrap px-4 py-2.5 text-[15px] leading-relaxed ${
                    m.role === 'user'
                      ? 'rounded-[20px] rounded-br-md bg-[var(--color-primary)] text-white'
                      : 'rounded-[20px] rounded-bl-md bg-[var(--color-surface)] text-[var(--color-text)]'
                  }`}
                >
                  {m.text}
                </p>
              </div>
            ))}
            {thinking && (
              <p className="w-fit rounded-[20px] rounded-bl-md bg-[var(--color-surface)] px-4 py-2.5 text-[15px] text-[var(--color-text-muted)]">
                ···
              </p>
            )}
            <div ref={bottomRef} />
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
    </div>
  );
}

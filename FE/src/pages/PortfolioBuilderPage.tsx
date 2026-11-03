import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { getCareersByDid } from '@/api/credentials';
import { CareersErrorAlert } from '@/components/CareersErrorAlert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { useAuthStore } from '@/store/authStore';

interface ChatMessage {
  id: number;
  role: 'agent' | 'user';
  text: string;
}

const SUGGESTIONS = ['경력 중심으로 강조해줘', '해커톤 수상 내역 추가', 'GitHub 링크 반영'];

export function PortfolioBuilderPage() {
  const user = useAuthStore((state) => state.user);
  const [selected, setSelected] = useState<Set<string>>(new Set());
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

  return (
    <div className="flex flex-col gap-6">
      <div>
        <span className="text-xs font-semibold tracking-widest text-[var(--color-primary)]">
          AGENT CO-PILOT
        </span>
        <h1 className="mt-1 text-3xl font-bold">AI 공인 포트폴리오 빌더</h1>
      </div>

      {isError && <CareersErrorAlert error={error} onRetry={() => refetch()} />}

      <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <Card className="flex h-fit flex-col gap-4 p-5">
          <h2 className="text-sm font-semibold">DID 데이터 동기화</h2>
          <div>
            <p className="text-xs text-[var(--color-text-muted)]">연결된 식별자</p>
            <p className="font-mono-did mt-1 break-all text-xs text-emerald-300">{user.did}</p>
          </div>
          <div>
            <p className="mb-2 text-xs text-[var(--color-text-muted)]">
              포함할 검증 자격증명 ({selected.size}/{credentials.length})
            </p>
            {isLoading && <p className="text-xs text-[var(--color-text-muted)]">불러오는 중...</p>}
            {!isLoading && !isError && credentials.length === 0 && (
              <p className="text-xs text-[var(--color-text-muted)]">
                검증된 자격증명이 없습니다.{' '}
                <Link to="/issue" className="text-[var(--color-primary)] hover:underline">
                  발급받기
                </Link>
              </p>
            )}
            <ul className="flex flex-col gap-2">
              {credentials.map((c) => (
                <li key={c.record_id}>
                  <label className="flex cursor-pointer items-start gap-2 rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs hover:bg-[var(--color-card-hover)]">
                    <input
                      type="checkbox"
                      checked={selected.has(c.record_id)}
                      onChange={() => toggle(c.record_id)}
                      className="mt-0.5 accent-cyan-400"
                    />
                    <span>
                      <span className="block font-semibold">{c.achievement_name}</span>
                      <span className="text-[var(--color-text-muted)]">{c.issuer}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </div>
        </Card>

        <Card accent className="flex h-[600px] flex-col gap-4 p-0">
          <div className="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-3">
            <span className="text-sm font-semibold">VC AI 에이전트</span>
            <span className="text-xs text-emerald-300">● Active</span>
          </div>

          <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-5">
            {messages.map((m) => (
              <div key={m.id} className={`flex ${m.role === 'user' ? 'justify-end' : ''}`}>
                <p
                  className={`max-w-[80%] whitespace-pre-wrap rounded-xl px-4 py-3 text-sm ${
                    m.role === 'user'
                      ? 'bg-indigo-600 text-white'
                      : 'border border-[var(--color-border)] bg-[var(--color-surface)]'
                  }`}
                >
                  {m.text}
                </p>
              </div>
            ))}
            {thinking && (
              <p className="text-xs text-[var(--color-text-muted)]">에이전트가 작성 중...</p>
            )}
            <div ref={bottomRef} />
          </div>

          <div className="flex flex-col gap-3 border-t border-[var(--color-border)] px-5 py-4">
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => send(s)}
                  className="rounded-full border border-[var(--color-border)] px-3 py-1 text-xs text-[var(--color-text-muted)] hover:border-[var(--color-primary)] hover:text-[var(--color-text)]"
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
                placeholder="AI 에이전트에게 추가 요구사항이나 강조하고 싶은 역량을 입력하세요..."
                className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-primary)]"
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

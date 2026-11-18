import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/Button';
import { COLLECTION_ROWS, PRIVACY_POLICY_PATH, PRIVACY_POLICY_VERSION, TRANSFER_ROWS } from '@/lib/privacy';

type ConsentKey = 'collection' | 'transfer' | 'age';

const REQUIRED: { key: ConsentKey; label: string }[] = [
  { key: 'collection', label: '[필수] 개인정보 수집·이용에 동의합니다.' },
  { key: 'transfer', label: '[필수] 개인정보 처리 위탁(자격증명 발급·검증, 서버 운영)에 동의합니다.' },
  { key: 'age', label: '[필수] 만 14세 이상입니다.' },
];

export function CollectionTable() {
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--color-border)]">
      <table className="w-full min-w-[520px] text-left text-[13px] leading-relaxed">
        <thead className="bg-[var(--color-surface)] text-[var(--color-text-sub)]">
          <tr>
            <th className="px-3 py-2 font-semibold">구분</th>
            <th className="px-3 py-2 font-semibold">항목</th>
            <th className="px-3 py-2 font-semibold">목적</th>
            <th className="px-3 py-2 font-semibold">보유 기간</th>
          </tr>
        </thead>
        <tbody>
          {COLLECTION_ROWS.map((row) => (
            <tr key={row.label} className="border-t border-[var(--color-border)] align-top">
              <td className="px-3 py-2 font-medium">{row.label}</td>
              <td className="px-3 py-2">{row.items}</td>
              <td className="px-3 py-2">{row.purpose}</td>
              <td className="px-3 py-2">{row.retention}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TransferTable() {
  return (
    <div className="overflow-x-auto rounded-xl border border-[var(--color-border)]">
      <table className="w-full min-w-[520px] text-left text-[13px] leading-relaxed">
        <thead className="bg-[var(--color-surface)] text-[var(--color-text-sub)]">
          <tr>
            <th className="px-3 py-2 font-semibold">받는 곳</th>
            <th className="px-3 py-2 font-semibold">하는 일</th>
            <th className="px-3 py-2 font-semibold">보내는 정보</th>
          </tr>
        </thead>
        <tbody>
          {TRANSFER_ROWS.map((row) => (
            <tr key={row.to} className="border-t border-[var(--color-border)] align-top">
              <td className="px-3 py-2 font-medium">{row.to}</td>
              <td className="px-3 py-2">{row.task}</td>
              <td className="px-3 py-2">{row.data}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 회원가입 첫 단계: 필수 항목에 모두 동의해야 가입 양식으로 넘어간다. */
export function PrivacyConsent({ onAgree }: { onAgree: () => void }) {
  const [checked, setChecked] = useState<Record<ConsentKey, boolean>>({
    collection: false,
    transfer: false,
    age: false,
  });
  const allChecked = REQUIRED.every(({ key }) => checked[key]);

  function setAll(value: boolean) {
    setChecked({ collection: value, transfer: value, age: value });
  }

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-[24px] font-bold leading-snug tracking-tight">개인정보 수집·이용 동의</h1>
        <p className="mt-2 text-[14px] leading-relaxed text-[var(--color-text-sub)]">
          가입하기 전에 어떤 정보를 왜 받고 어디로 보내는지 확인해 주세요. 필수 항목에 동의하지 않으면 가입할 수 없어요.
        </p>
      </div>

      <section className="flex flex-col gap-2">
        <h2 className="text-[15px] font-bold">1. 수집·이용하는 개인정보</h2>
        <CollectionTable />
      </section>

      <section className="flex flex-col gap-2">
        <h2 className="text-[15px] font-bold">2. 처리 위탁·외부 전송</h2>
        <TransferTable />
        <p className="text-[12px] leading-relaxed text-[var(--color-text-muted)]">
          Anthropic(미국)으로의 전송은 LLM 학습 계획·AI 포트폴리오 화면에서 따로 동의할 때만 일어나요. 소셜 로그인은 그 버튼을
          누를 때만 쓰여요.
        </p>
      </section>

      <section className="flex flex-col gap-1 text-[13px] leading-relaxed text-[var(--color-text-sub)]">
        <h2 className="text-[15px] font-bold text-[var(--color-text)]">3. 동의를 거부할 권리</h2>
        <p>
          동의하지 않을 수 있어요. 다만 필수 항목에 동의하지 않으면 가입할 수 없어요. 선택 기능(소셜 로그인, AI)은 쓰지 않아도
          다른 기능을 이용할 수 있어요.
        </p>
      </section>

      <div className="flex flex-col gap-2 rounded-xl bg-[var(--color-surface)] p-4">
        <label className="flex items-center gap-2 text-[15px] font-bold">
          <input
            type="checkbox"
            checked={allChecked}
            onChange={(e) => setAll(e.target.checked)}
            className="h-4 w-4 accent-[var(--color-primary)]"
          />
          전체 동의
        </label>
        <div className="h-px bg-[var(--color-border)]" />
        {REQUIRED.map(({ key, label }) => (
          <label key={key} className="flex items-start gap-2 text-[14px] text-[var(--color-text-sub)]">
            <input
              type="checkbox"
              checked={checked[key]}
              onChange={(e) => setChecked((prev) => ({ ...prev, [key]: e.target.checked }))}
              className="mt-1 h-4 w-4 shrink-0 accent-[var(--color-primary)]"
            />
            {label}
          </label>
        ))}
      </div>

      <Button size="lg" disabled={!allChecked} onClick={onAgree}>
        동의하고 계속하기
      </Button>
      <p className="text-center text-[13px] text-[var(--color-text-muted)]">
        전체 안내는{' '}
        <Link to={PRIVACY_POLICY_PATH} className="font-semibold text-[var(--color-primary)] hover:underline">
          개인정보 처리 안내
        </Link>
        에서 볼 수 있어요 (시행일 {PRIVACY_POLICY_VERSION}).{' '}
        <Link to="/login" className="hover:underline">
          가입하지 않고 돌아가기
        </Link>
      </p>
    </div>
  );
}

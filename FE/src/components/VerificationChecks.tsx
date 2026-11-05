import type { VerificationCheck } from '@/types/api';

/** BE 점검 항목 이름 (openapi `VerificationCheck.name`) → 화면 표시명 */
const CHECK_LABELS: Record<string, string> = {
  schema: '형식',
  recipient: '수신자',
  signature: '서명',
  issuer_trust: '발급기관 신뢰',
  revocation: '폐기 여부',
  expiration: '유효기간',
  did_resolution: 'DID 조회',
  anchor_match: '앵커 일치',
};

const RESULT_STYLE: Record<VerificationCheck['result'], { label: string; className: string }> = {
  pass: { label: '통과', className: 'text-[var(--color-success)]' },
  fail: { label: '실패', className: 'text-[var(--color-danger)]' },
  warn: { label: '주의', className: 'text-[var(--color-warning)]' },
  skip: { label: '건너뜀', className: 'text-[var(--color-text-muted)]' },
};

/** 검증 점검 항목 목록. 항목이 없으면 아무것도 그리지 않는다. */
export function VerificationChecks({ checks }: { checks: VerificationCheck[] }) {
  if (checks.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1.5 text-[13px]">
      {checks.map((c, i) => {
        const style = RESULT_STYLE[c.result] ?? RESULT_STYLE.skip;
        return (
          <li key={`${c.name}-${i}`} className="flex items-start justify-between gap-3">
            <span className="min-w-0 text-[var(--color-text-sub)]">
              {CHECK_LABELS[c.name] ?? c.name}
              {c.detail && <span className="block text-[12px] text-[var(--color-text-muted)]">{c.detail}</span>}
            </span>
            <span className={`shrink-0 font-semibold ${style.className}`}>{style.label}</span>
          </li>
        );
      })}
    </ul>
  );
}

import { StatusBadge } from '@/components/StatusBadge';
import type { ExcludedEvidence } from '@/types/api';

/** BE가 돌려주는 제외 사유 코드 → 화면 문구 */
const REASON_TEXT: Record<string, string> = {
  no_signed_original: '서명 원본이 없어 다시 검증할 수 없음',
  revoked: '발급 기관이 취소함',
  expired: '유효기간 만료',
  invalid: '서명·형식 검증 실패',
  verifier_error: '검증 서버 호출 실패',
  recipient_mismatch: '서명 원본의 수신자가 내 DID와 다름',
  record_mismatch: '서명 원본이 저장된 기록과 다름',
};

/** 골랐지만 생성 시점 재검증을 통과하지 못해 빠진 자격증명 */
export function ExcludedList({ excluded, names }: { excluded: ExcludedEvidence[]; names?: Map<string, string> }) {
  if (excluded.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1.5">
      {excluded.map((e) => (
        <li key={e.record_id} className="flex items-center justify-between gap-2 text-[13px]">
          <span className="min-w-0 truncate text-[var(--color-text-sub)]">
            {names?.get(e.record_id) ?? e.record_id}
            <span className="ml-1.5 text-[var(--color-text-muted)]">· {REASON_TEXT[e.reason] ?? e.reason}</span>
          </span>
          <StatusBadge status={e.status} />
        </li>
      ))}
    </ul>
  );
}

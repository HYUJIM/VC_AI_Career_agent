import { StatusBadge, UnverifiedRecordTag } from '@/components/StatusBadge';
import type { ExcludedEvidence } from '@/types/api';

/** BE가 돌려주는 제외 사유 코드 → 화면 문구 */
const REASON_TEXT: Record<string, string> = {
  no_signed_original: '서명 원본이 없어 다시 검증할 수 없음',
  revoked: '발급 기관이 취소함',
  expired: '유효기간 만료',
  invalid: '서명·발급자 검증 실패 (위조·변조 의심)',
  verifier_error: '검증 서버가 지금 응답하지 않음 (잠시 뒤 다시 시도)',
  recipient_mismatch: '서명 원본의 수신자가 내 DID와 다름',
  record_mismatch: '서명 원본이 저장된 기록과 다름',
  unverified_upload: '올릴 때 검증되지 않아 격리 보관한 기록',
};

function excludedReasonText(reason: string): string {
  return Object.hasOwn(REASON_TEXT, reason) ? REASON_TEXT[reason] : reason;
}

/** 골랐지만 생성 시점 재검증을 통과하지 못해 빠진 자격증명. AI 결과의 근거가 아니므로 '미검증 기록' 태그를 붙인다. */
export function ExcludedList({ excluded, names }: { excluded: ExcludedEvidence[]; names?: Map<string, string> }) {
  if (excluded.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1.5">
      {excluded.map((e) => (
        <li key={e.record_id} className="flex items-start justify-between gap-2 text-[13px]">
          <span className="flex min-w-0 items-start gap-1.5">
            <UnverifiedRecordTag title="실행 시점 재검증을 통과하지 못해 근거로 쓰지 않았어요" />
            <span className="min-w-0 break-words text-[var(--color-text-sub)]">
              {names?.get(e.record_id) ?? e.record_id}
              <span className="ml-1.5 text-[var(--color-text-muted)]">· {excludedReasonText(e.reason)}</span>
            </span>
          </span>
          <StatusBadge status={e.status} />
        </li>
      ))}
    </ul>
  );
}

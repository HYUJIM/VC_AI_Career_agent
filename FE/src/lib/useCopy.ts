import { useCallback, useEffect, useRef, useState } from 'react';
import { writeClipboard } from '@/lib/clipboard';

/**
 * 클립보드 복사 + "복사됨" 피드백 상태.
 * 한 화면에 복사 버튼이 여러 개일 수 있으므로 key로 어떤 항목이 복사됐는지 구분한다.
 */
export function useCopy(resetMs = 1500) {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const copy = useCallback(
    async (text: string, key = 'default') => {
      // 클립보드 API가 없거나(HTTP) 거부되면 execCommand 로 대신 복사한다. 그래도 실패하면 피드백 없이 무시
      if (!(await writeClipboard(text))) return;
      setCopiedKey(key);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopiedKey(null), resetMs);
    },
    [resetMs],
  );

  return { copy, isCopied: (key = 'default') => copiedKey === key };
}

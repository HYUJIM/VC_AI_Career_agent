/**
 * text 를 클립보드에 쓰고 성공 여부를 돌려준다.
 * navigator.clipboard 는 보안 컨텍스트(HTTPS·localhost) 전용이라 HTTP 배포에서는 없다.
 * 그때나 권한 거부 등으로 실패하면 숨긴 textarea + execCommand('copy') 로 한 번 더 시도한다.
 */
export async function writeClipboard(text: string): Promise<boolean> {
  // await 전에 확인해야 HTTP 에서 대체 경로가 클릭 처리 안에서 바로 실행된다 (Safari 는 사용자 동작 안에서만 복사 허용).
  if (typeof navigator.clipboard?.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 권한 거부·문서 포커스 없음 등 → 아래 대체 경로로 시도
    }
  }
  return legacyCopy(text);
}

function legacyCopy(text: string): boolean {
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const selection = document.getSelection();
  const ranges = selection
    ? Array.from({ length: selection.rangeCount }, (_, i) => selection.getRangeAt(i))
    : [];

  const el = document.createElement('textarea');
  el.value = text;
  el.readOnly = true; // 모바일에서 키보드가 뜨지 않게
  el.tabIndex = -1;
  // setAttribute('style') 대신 CSSOM 으로 지정해 CSP(style-src)에 걸리지 않게 한다. 12pt 는 iOS 확대 방지.
  // 포커스를 받으므로 aria-hidden 은 넣지 않는다.
  Object.assign(el.style, {
    position: 'fixed',
    top: '0',
    left: '0',
    width: '1px',
    height: '1px',
    padding: '0',
    border: '0',
    opacity: '0',
    pointerEvents: 'none',
    fontSize: '12pt',
  });
  document.body.appendChild(el);

  let ok = false;
  try {
    el.focus({ preventScroll: true });
    el.select();
    el.setSelectionRange(0, text.length);
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  } finally {
    el.remove();
    // 원래 선택 영역과 포커스(예: 열린 메뉴의 항목)를 되돌린다.
    if (selection) {
      selection.removeAllRanges();
      ranges.forEach((range) => selection.addRange(range));
    }
    previousFocus?.focus({ preventScroll: true });
  }
  return ok;
}

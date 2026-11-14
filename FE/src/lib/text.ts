/**
 * BE `app/core/text.py`와 맞춘 문자열 검증·정리 및 안전한 URL 헬퍼.
 *
 * - 제어문자(Unicode Cc: U+0000~U+001F, U+007F~U+009F)와 줄·문단 구분자(U+2028, U+2029),
 *   짝 없는 서로게이트(U+D800~U+DFFF)를 다룬다.
 * - 회원가입·포트폴리오·공유 링크에서 같은 규칙을 재사용한다 (이슈 #14, #18, #23).
 */

/** 줄바꿈·탭 등 제어문자(Cc)와 유니코드 줄/문단 구분자(U+2028, U+2029)가 없는 한 줄 문자열인지 검사하는 정규식 */
// oxlint-disable-next-line no-control-regex
export const SINGLE_LINE_PATTERN = /^[^\x00-\x1f\x7f-\x9f\u2028\u2029]*$/;

/** 제어문자·줄바꿈(U+2028·U+2029 포함)과 짝 없는 서로게이트를 공백으로 바꾸고 앞뒤 공백을 잘라낸다. */
export function sanitizeSingleLine(value: string): string {
  // u 플래그에서 [\ud800-\udfff]는 정상 이모지(서로게이트 쌍)는 건드리지 않고 짝 없는 서로게이트만 매치한다.
  // oxlint-disable-next-line no-control-regex
  return value.replace(/[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]+/gu, ' ').trim();
}

/**
 * 외부 링크(`<a href={...}>`)에 넣을 주소가 안전한 `http:` / `https:` 스킴인지 확인한다.
 * `javascript:`·`data:`·`vbscript:` 스킴 주입을 막고, 허용되지 않으면 `undefined`를 돌려준다.
 */
export function safeExternalHref(rawUrl: string | null | undefined): string | undefined {
  if (!rawUrl || typeof rawUrl !== 'string') return undefined;
  try {
    const parsed = new URL(rawUrl.trim());
    if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return undefined;
    return parsed.toString();
  } catch {
    return undefined;
  }
}

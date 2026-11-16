/**
 * BE `app/core/text.py`와 맞춘 문자열 검증·정리 및 안전한 URL 헬퍼.
 *
 * - 제어문자(Unicode Cc: U+0000~U+001F, U+007F~U+009F)와 줄·문단 구분자(U+2028, U+2029),
 *   짝 없는 서로게이트(U+D800~U+DFFF)를 다룬다.
 * - 글자 방향 제어·보이지 않는 글자(BE `is_invisible`: Unicode 서식 문자 Cf 전부(U+202E·U+200B·U+FEFF·소프트 하이픈
 *   U+00AD·태그 문자 U+E0000대 등), 빈칸처럼 보이는 글자 U+115F·U+1160·U+3164·U+FFA0·U+2800, 이집트 상형문자 서식 문자
 *   U+13430~U+1343F, 폭 0으로 그려지는 그 밖의 Default_Ignorable 글자 U+17B4·U+17B5·U+2065·U+FFF0~U+FFF8·U+E0000~U+E00FF·
 *   U+E01F0~U+E0FFF)도 한 줄 텍스트에 두지 않는다 (BE가 422로 거절한다). 서식 문자 중 이모지 결합 문자(ZWJ U+200D)만 그대로 둔다.
 * - 비어 있지 않은 값에는 보이는 글자(문자·숫자·문장부호·기호)가 하나는 있어야 한다 (BE `has_visible_text`).
 *   결합 문자·이형 선택자(U+FE0F)·ZWJ만으로 된 이름은 빈칸처럼 보이므로 받지 않는다.
 * - 회원가입·포트폴리오·공유 링크에서 같은 규칙을 재사용한다 (이슈 #14, #18, #23).
 */

/** 줄바꿈·탭 등 제어문자(Cc)·유니코드 줄/문단 구분자(U+2028, U+2029)·방향 제어/보이지 않는 글자가 없는 한 줄 문자열인지 검사하는 정규식 */
// oxlint-disable-next-line no-control-regex
export const SINGLE_LINE_PATTERN = /^(?:(?=[\s\S]*?[\p{L}\p{N}\p{P}\p{S}\p{Cn}])(?:\u200d|[^\x00-\x1f\x7f-\x9f\u2028\u2029\u115f\u1160\u3164\uffa0\u2800\u{13430}-\u{1343f}\u17b4\u17b5\u2065\ufff0-\ufff8\u{e0000}-\u{e00ff}\u{e01f0}-\u{e0fff}\p{Cf}])+)?$/u;

/** 방향 제어·보이지 않는 글자 (지운다). ZWJ(U+200D)는 남긴다 */
const INVISIBLE_FORMAT_CHARS =
  /(?!\u200d)[\p{Cf}\u115f\u1160\u3164\uffa0\u2800\u{13430}-\u{1343f}\u17b4\u17b5\u2065\ufff0-\ufff8\u{e0000}-\u{e00ff}\u{e01f0}-\u{e0fff}]/gu;

/**
 * 제어문자·줄바꿈(U+2028·U+2029 포함)과 짝 없는 서로게이트를 공백으로 바꾸고, 방향 제어·보이지 않는 서식 문자는 지운 뒤
 * 앞뒤 공백을 잘라낸다.
 */
export function sanitizeSingleLine(value: string): string {
  // u 플래그에서 [\ud800-\udfff]는 정상 이모지(서로게이트 쌍)는 건드리지 않고 짝 없는 서로게이트만 매치한다.
  // oxlint-disable-next-line no-control-regex
  return value.replace(INVISIBLE_FORMAT_CHARS, '').replace(/[\x00-\x1f\x7f-\x9f\u2028\u2029\ud800-\udfff]+/gu, ' ').trim();
}

/** 보이는 글자가 없는 한 줄 입력에 보여 주는 안내 */
export const NO_VISIBLE_TEXT_MESSAGE = '보이는 글자(문자·숫자·기호)를 하나 이상 입력해주세요.';

/**
 * 이 값을 sanitizeSingleLine으로 정리해 보냈을 때 BE가 받는지 (BE `ensure_single_line`·`has_visible_text`와 같은 기준).
 * 정리한 값이 비면 required가 아닐 때만 true (빈 값은 null로 보낸다). 정리 뒤에도 남는 실패 이유는 보이는 글자가 없는 것뿐이다.
 */
export function isSendableSingleLine(value: string, required = false): boolean {
  const cleaned = sanitizeSingleLine(value);
  return cleaned ? SINGLE_LINE_PATTERN.test(cleaned) : !required;
}

/**
 * 보이지 않는 글자(방향 제어·폭 없는 문자·태그 문자 등, ZWJ 제외)만 지운다. 줄바꿈은 그대로 둔다.
 * 발급기관이 서명한 배지 문구처럼 거절할 수 없고 화면에만 보여 주는 값에 쓴다 (U+202E로 글자 순서를 뒤집어 보이게 하는 것을 막는다).
 */
export function stripInvisible(value: string): string {
  return value.replace(INVISIBLE_FORMAT_CHARS, '');
}

/** 문자열이면 stripInvisible, 아니면(null·undefined·형식이 다른 값) 그대로 돌려준다 */
function cleanText<T>(value: T): T {
  return (typeof value === 'string' ? stripInvisible(value) : value) as T;
}

type EvidenceText = {
  achievement_name?: unknown;
  issuer_name?: unknown;
  issuer?: unknown;
  achievement_description?: unknown;
  criteria?: unknown;
};

/**
 * 발급기관이 서명한 배지 문구(이름·발급기관·설명·기준)에서 보이지 않는 글자만 지운 사본 (화면 표시용).
 * BE는 서명 원본의 문구를 그대로 돌려주므로, 방향 제어 문자(U+202E 등)로 글자 순서를 뒤집어 보이게 하지 못하게 FE가 표시
 * 전에 지운다. 서명 원본(vc_jwt·raw_data)은 건드리지 않는다. 없는 필드는 만들지 않는다.
 */
export function cleanEvidenceText<T extends EvidenceText>(evidence: T): T {
  const cleaned: T = { ...evidence };
  for (const key of ['achievement_name', 'issuer_name', 'issuer', 'achievement_description', 'criteria'] as const) {
    if (key in cleaned) cleaned[key] = cleanText(cleaned[key]);
  }
  return cleaned;
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

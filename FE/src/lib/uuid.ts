type RandomSource = Pick<Crypto, 'getRandomValues'> & Partial<Pick<Crypto, 'randomUUID'>>;

/**
 * RFC 4122 v4 UUID.
 * crypto.randomUUID 는 보안 컨텍스트(HTTPS·localhost) 전용이라 HTTP 배포에서는 getRandomValues 로 만든다.
 * source 인자는 대체 경로를 시험할 때만 쓴다.
 */
export function randomUUID(source: RandomSource = globalThis.crypto): string {
  // 메서드로 호출해야 한다 (떼어 내서 부르면 Illegal invocation)
  if (typeof source.randomUUID === 'function') return source.randomUUID();
  const bytes = source.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10xx
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** 사용자가 OS에서 "동작 줄이기"를 켰는지. 켜져 있으면 장식용 애니메이션을 건너뛴다. */
export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

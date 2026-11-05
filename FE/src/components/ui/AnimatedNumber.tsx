import { animate } from 'animejs';
import { useEffect, useLayoutEffect, useRef } from 'react';
import { prefersReducedMotion } from '@/lib/motion';

interface AnimatedNumberProps {
  value: number;
  /** 카운트업 시간(ms). 기본 900 */
  duration?: number;
  className?: string;
}

/**
 * Anime.js로 이전 값 → 새 값까지 숫자를 굴려 보여준다.
 * 텍스트는 DOM에 직접 쓰므로 React 자식을 두지 않는다 (재렌더 시 덮어쓰기 방지).
 */
export function AnimatedNumber({ value, duration = 900, className }: AnimatedNumberProps) {
  const ref = useRef<HTMLSpanElement>(null);
  // 마지막으로 화면에 표시된 값. 애니메이션 도중 값이 바뀌면 그 지점부터 이어서 굴린다.
  const shown = useRef(0);

  useLayoutEffect(() => {
    if (ref.current && !ref.current.textContent) ref.current.textContent = String(shown.current);
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (prefersReducedMotion() || shown.current === value) {
      shown.current = value;
      el.textContent = String(value);
      return;
    }
    const counter = { n: shown.current };
    const anim = animate(counter, {
      n: value,
      duration,
      ease: 'outCubic',
      onUpdate: () => {
        shown.current = Math.round(counter.n);
        el.textContent = String(shown.current);
      },
      onComplete: () => {
        shown.current = value;
        el.textContent = String(value);
      },
    });
    return () => {
      anim.cancel();
    };
  }, [value, duration]);

  return <span ref={ref} className={className} aria-label={String(value)} />;
}

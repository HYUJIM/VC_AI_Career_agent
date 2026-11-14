import { z } from 'zod';

/**
 * 운영 CSP(script-src 'self', 'unsafe-eval' 없음)에서는 zod 가 객체 스키마를 만들 때 하는 eval 가능 여부 시험
 * (new Function)이 예외는 잡히지만 브라우저에 CSP 위반으로 기록된다. 그래서 스키마를 만들기 전에 JIT 를 끈다.
 * 검증 결과·오류 메시지는 같다. 스키마는 LoginPage·RegisterPage 모듈 최상위에서 만들어지고 모듈은 import 순서대로
 * 실행되므로, 이 파일은 main.tsx 의 첫 import 여야 한다.
 */
z.config({ jitless: true });

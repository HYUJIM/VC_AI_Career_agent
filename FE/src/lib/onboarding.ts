/**
 * 대시보드 "시작 가이드"용 진행 상태. 서버에 저장할 데이터가 아니므로 브라우저(localStorage)에만 기록한다.
 */
const key = (did: string) => `career-wallet:portfolio-used:${did}`;

export function hasUsedPortfolio(did: string): boolean {
  try {
    return localStorage.getItem(key(did)) === '1';
  } catch {
    return false;
  }
}

export function markPortfolioUsed(did: string): void {
  try {
    localStorage.setItem(key(did), '1');
  } catch {
    // 저장소를 사용할 수 없는 환경에서는 무시
  }
}

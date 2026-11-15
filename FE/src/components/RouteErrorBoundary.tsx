import { Component, type ReactNode } from 'react';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';

/**
 * 화면(라우트) 하나가 그리는 도중 예외를 던져도 앱 전체가 흰 화면이 되지 않게 막는다.
 * Layout 이 `<Outlet />` 을 이 경계로 감싸므로 머리글·메뉴·로그아웃은 그대로 쓸 수 있다.
 * Layout 이 경로를 key 로 주어, 다른 화면으로 옮기면 새로 마운트되면서 오류 상태가 풀린다.
 * (이벤트 핸들러·비동기 코드의 오류는 잡지 않는다. 그런 오류는 각 화면의 onError 가 처리한다.)
 */
export class RouteErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <Alert
        title="화면을 표시하는 중 오류가 발생했습니다."
        actions={
          <Button variant="outline" size="sm" onClick={() => window.location.reload()}>
            새로고침
          </Button>
        }
      >
        새로고침한 뒤 다시 시도해주세요. 문제가 계속되면 관리자에게 문의해주세요.
      </Alert>
    );
  }
}

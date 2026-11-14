import { useNavigate } from 'react-router-dom';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { getApiErrorKind, getApiErrorMessage, getConnectionHint } from '@/lib/apiClient';
import { useAuthStore } from '@/store/authStore';

/**
 * GET /v1/subjects/{did}/careers 실패 시 공통 안내.
 * 대시보드와 AI 포트폴리오 화면이 같은 데이터를 쓰므로 에러 화면도 공유한다.
 */
export function CareersErrorAlert({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const logout = useAuthStore((state) => state.logout);
  const navigate = useNavigate();
  const kind = getApiErrorKind(error);

  if (kind === 'notFound') {
    return (
      <Alert
        variant="warning"
        title="계정 정보를 서버에서 찾을 수 없습니다."
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              logout('sessionExpired');
              navigate('/login');
            }}
          >
            다시 로그인
          </Button>
        }
      >
        저장된 로그인 정보의 DID가 서버에 없습니다. DB가 초기화되었을 수 있으니 다시 로그인해주세요.
      </Alert>
    );
  }

  const retry = (
    <Button variant="outline" size="sm" onClick={onRetry}>
      다시 시도
    </Button>
  );

  if (kind === 'network') {
    return (
      <Alert variant="warning" title="서버에 연결할 수 없습니다." actions={retry}>
        {getConnectionHint()}
      </Alert>
    );
  }

  return (
    <Alert variant="error" title="자격증명을 불러오지 못했습니다." actions={retry}>
      {getApiErrorMessage(error)}
    </Alert>
  );
}

import axios, { type AxiosError } from 'axios';
import { useAuthStore } from '@/store/authStore';
import type { ApiErrorBody, ValidationErrorItem } from '@/types/api';

/**
 * BE FastAPI 서버 베이스 URL.
 * 개발 중에는 vite.config.ts 의 /api 프록시(-> http://localhost:3000)를 사용한다.
 * 운영 빌드에서는 VITE_API_BASE_URL 환경변수로 실제 BE 주소를 주입한다.
 */
const baseURL = import.meta.env.VITE_API_BASE_URL ?? '/api';

export const apiClient = axios.create({
  baseURL,
  headers: {
    'Content-Type': 'application/json',
  },
});

/** 토큰이 필요 없는 엔드포인트(로그인/회원가입). 여기서 나는 401은 "비밀번호 오류"이지 세션 만료가 아니다. */
const PUBLIC_PATHS = ['/v1/users/login', '/v1/users/register'];

/** BE v0.4.0: 로그인 사용자의 JWT를 모든 요청에 `Authorization: Bearer` 헤더로 붙인다. */
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token && !PUBLIC_PATHS.includes(config.url ?? '')) {
    config.headers.set('Authorization', `Bearer ${token}`);
  }
  return config;
});

/**
 * 토큰을 보냈는데 401(만료·위조·서버 JWT_SECRET 변경)이면 저장된 세션이 더 이상 유효하지 않다.
 * 로그아웃 처리하면 ProtectedRoute 가 /login 으로 보내고, 로그인 화면이 만료 안내를 띄운다.
 * 로그인 실패(토큰 없이 보낸 401)는 여기서 처리하지 않고 화면에서 안내한다.
 */
apiClient.interceptors.response.use(undefined, (error: unknown) => {
  if (axios.isAxiosError(error) && error.response?.status === 401) {
    const sentToken = Boolean(error.config?.headers?.get?.('Authorization'));
    const { isAuthenticated, logout } = useAuthStore.getState();
    if (sentToken && isAuthenticated) {
      logout('sessionExpired');
    }
  }
  return Promise.reject(error);
});

/** axios 에러의 HTTP 상태 코드 (네트워크 오류 등 응답이 없으면 undefined) */
export function getApiErrorStatus(error: unknown): number | undefined {
  return axios.isAxiosError(error) ? error.response?.status : undefined;
}

/**
 * 화면 분기용 에러 종류.
 * - network: BE에 연결 불가 (서버 미실행 / vite 프록시가 연결 실패 시 본문 없이 5xx 반환)
 * - unauthorized: 401 (로그인 실패 — 비밀번호 오류와 미가입 이메일을 BE가 구분하지 않음,
 *   또는 토큰 없음/만료/위조)
 * - forbidden: 403 (남의 DID에 접근 — BE v0.4.0)
 * - conflict: 400 (이미 가입된 이메일 등 요청 거부), 409 (다른 사용자에게 이미 등록된 자격증명)
 * - notFound: 404 (DID에 해당하는 사용자 없음 / 가입되지 않은 DID로 발급·검증)
 * - validation: 422 (요청 본문 검증 실패)
 * - server: BE가 detail과 함께 반환한 5xx (DXWorks 오류 502, DXWorks 설정 누락 503, 내부 오류 500)
 */
export type ApiErrorKind =
  | 'network'
  | 'unauthorized'
  | 'forbidden'
  | 'conflict'
  | 'notFound'
  | 'validation'
  | 'server'
  | 'unknown';

export function getApiErrorKind(error: unknown): ApiErrorKind {
  if (!axios.isAxiosError(error)) return 'unknown';
  const response = error.response;
  if (!response) return 'network';
  const hasDetail = Boolean((response.data as ApiErrorBody | undefined)?.detail);
  switch (response.status) {
    case 400:
    case 409:
      return 'conflict';
    case 401:
      return 'unauthorized';
    case 403:
      return 'forbidden';
    case 404:
      return 'notFound';
    case 422:
      return 'validation';
    default:
      if (response.status >= 500) return hasDetail ? 'server' : 'network';
      return 'unknown';
  }
}

/** BE가 반환하는 알려진 영문 에러 메시지 → 사용자용 한국어 메시지 */
const KNOWN_ERROR_MESSAGES: Record<string, string> = {
  'Invalid email or password': '이메일 또는 비밀번호가 올바르지 않습니다.',
  'Email already registered': '이미 가입된 이메일입니다.',
  'User not found or not verified yet.': '사용자 정보를 찾을 수 없습니다. 다시 로그인해주세요.',
  // BE v0.4.0 인증/권한
  'Authentication required': '로그인이 필요합니다.',
  'Invalid token': '로그인 정보가 올바르지 않습니다. 다시 로그인해주세요.',
  'Token expired': '로그인이 만료되었습니다. 다시 로그인해주세요.',
  'Forbidden: not your DID': '본인 DID의 정보만 조회할 수 있습니다.',
  'Forbidden: can only issue to your own DID': '본인 DID로만 배지를 발급받을 수 있습니다.',
  'Forbidden: credential was issued to a different DID': '다른 DID로 발급된 배지는 저장할 수 없습니다.',
  'Forbidden: presentation holder is not you': '본인의 신원 증명만 검증할 수 있습니다.',
  // BE v0.4.0 발급/검증
  'Holder DID is not registered': '가입되지 않은 DID입니다. 다시 로그인해주세요.',
  'Recipient DID is not registered': '배지를 받은 DID가 가입된 사용자가 아닙니다.',
  'Credential already registered to another user': '이미 다른 사용자의 지갑에 등록된 자격증명입니다.',
  'Mock issuer is disabled': '모의 발급 서버가 비활성화되어 있습니다. (BE MOCK_ISSUER_ENABLED)',
  'Internal server error': '서버 내부 오류가 발생했습니다.',
};

function translate(message: string): string {
  return KNOWN_ERROR_MESSAGES[message] ?? message;
}

const FIELD_LABELS: Record<string, string> = {
  name: '이름',
  email: '이메일',
  password: '비밀번호',
  templateId: '템플릿 ID',
  holderDid: 'DID',
  token: '토큰',
  type: '타입',
};

/** FastAPI 422 의 detail 배열을 한 줄 메시지로 합친다. */
function formatValidationDetail(detail: ValidationErrorItem[]): string {
  const messages = detail
    .map((item) => {
      const field = item.loc
        ?.filter((part) => part !== 'body')
        .map((part) => FIELD_LABELS[String(part)] ?? String(part))
        .join('.');
      return field && item.msg ? `${field}: ${item.msg}` : item.msg;
    })
    .filter((msg): msg is string => Boolean(msg));
  return messages.length > 0
    ? `입력값을 확인해주세요. (${messages.join(', ')})`
    : '입력값을 확인해주세요.';
}

export function getApiErrorMessage(error: unknown): string {
  if (getApiErrorKind(error) === 'network') {
    return 'BE 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인해주세요.';
  }
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<ApiErrorBody>;
    const body = axiosError.response?.data;
    const detail = body?.detail;
    if (Array.isArray(detail)) return formatValidationDetail(detail);
    if (typeof detail === 'string') return translate(detail);
    return body?.message ?? body?.error ?? axiosError.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return '알 수 없는 오류가 발생했습니다.';
}

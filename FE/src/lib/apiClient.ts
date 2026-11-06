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

// 로그인 토큰이 있으면 모든 요청에 싣는다. BE는 토큰의 DID로 본인 데이터만 허용한다.
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token) config.headers.set('Authorization', `Bearer ${token}`);
  return config;
});

/** axios 에러의 HTTP 상태 코드 (네트워크 오류 등 응답이 없으면 undefined) */
export function getApiErrorStatus(error: unknown): number | undefined {
  return axios.isAxiosError(error) ? error.response?.status : undefined;
}

/**
 * 화면 분기용 에러 종류.
 * - network: BE에 연결 불가 (서버 미실행 / vite 프록시가 연결 실패 시 본문 없이 5xx 반환)
 * - unauthorized: 401 (로그인 실패 — 비밀번호 오류와 미가입 이메일을 BE가 구분하지 않음)
 * - conflict: 400 (이미 가입된 이메일 등 요청 거부)
 * - notFound: 404 (DID에 해당하는 사용자 없음)
 * - validation: 422 (요청 본문 검증 실패)
 * - server: BE가 detail과 함께 반환한 5xx (DB/DXWorks 오류 등)
 */
export type ApiErrorKind =
  | 'network'
  | 'unauthorized'
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
      return 'conflict';
    case 401:
      return 'unauthorized';
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
  'Authentication required': '로그인이 필요합니다. 다시 로그인해주세요.',
  'Invalid token': '로그인 정보가 올바르지 않습니다. 다시 로그인해주세요.',
  'Token expired': '로그인이 만료되었습니다. 다시 로그인해주세요.',
  'AI service temporarily unavailable': 'AI 서버에 연결할 수 없습니다. AI 서버가 실행 중인지 확인해주세요.',
  'AI service is not configured': 'BE에 AI 서버 주소·토큰이 설정되지 않았습니다 (AI_SERVICE_URL, AI_SERVICE_TOKEN).',
  'AI service authentication failed': 'BE와 AI 서버의 AI_SERVICE_TOKEN이 서로 다릅니다.',
  'AI service response failed contract checks':
    'AI 응답이 BE 검사를 통과하지 못했습니다. BE와 AI의 공유 형식(ai_contract)을 맞춰주세요.',
  'No eligible reviewed job sources; import sources first':
    '비교에 쓸 수 있는 최근 공고가 없습니다. 연구자가 공고를 먼저 반입해야 합니다.',
  'Target posting is stale, closed, synthetic or outside filters':
    '선택한 공고는 마감되었거나 수집 후 7일이 지나 비교에 쓸 수 없습니다.',
  'Career analysis expired; create a new analysis first':
    '직무 비교 결과의 유효시간(5분)이 지났습니다. 다시 비교한 뒤 계획을 만들어주세요.',
  'External LLM access is not enabled for this account': '이 계정은 LLM 모드를 사용할 수 없습니다.',
  'LEARNING_CHOICE_IS_OUTSIDE_ACTIONABLE_GAPS': '선택한 학습 항목이 비교 결과의 보완 대상이 아닙니다.',
  LLM_CONTEXT_TOO_LARGE: '계획 옵션이 너무 많아 LLM에 보낼 수 없습니다. 템플릿 모드를 사용해주세요.',
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

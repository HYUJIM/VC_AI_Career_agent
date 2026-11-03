import axios, { type AxiosError } from 'axios';
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

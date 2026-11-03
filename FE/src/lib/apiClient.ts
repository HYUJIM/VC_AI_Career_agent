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

/** BE가 반환하는 알려진 영문 에러 메시지 → 사용자용 한국어 메시지 */
const KNOWN_ERROR_MESSAGES: Record<string, string> = {
  'Invalid email or password': '이메일 또는 비밀번호가 올바르지 않습니다.',
  'Email already registered': '이미 가입된 이메일입니다.',
  'User not found or not verified yet.': '사용자 정보를 찾을 수 없습니다. 다시 로그인해주세요.',
};

function translate(message: string): string {
  return KNOWN_ERROR_MESSAGES[message] ?? message;
}

/** FastAPI 422 의 detail 배열을 한 줄 메시지로 합친다. */
function formatValidationDetail(detail: ValidationErrorItem[]): string {
  const messages = detail
    .map((item) => {
      const field = item.loc?.filter((part) => part !== 'body').join('.');
      return field && item.msg ? `${field}: ${item.msg}` : item.msg;
    })
    .filter((msg): msg is string => Boolean(msg));
  return messages.length > 0
    ? `입력값을 확인해주세요. (${messages.join(', ')})`
    : '입력값을 확인해주세요.';
}

export function getApiErrorMessage(error: unknown): string {
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

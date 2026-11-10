import axios, { type AxiosError } from 'axios';
import { useAuthStore } from '@/store/authStore';
import type { ApiErrorBody, ValidationErrorItem } from '@/types/api';

/**
 * BE FastAPI 서버 베이스 URL.
 * 개발 중에는 vite.config.ts 의 /api 프록시(-> http://localhost:3000)를 사용한다.
 * 운영 빌드에서는 VITE_API_BASE_URL 환경변수로 실제 BE 주소를 주입한다.
 * - 빌드(npm run build) 때 번들에 박히는 값이라 바꾸면 다시 빌드해야 한다.
 * - BE 라우터는 모두 /api 아래에 있으므로 /api 까지 넣는다. 예: https://api.example.com/api
 * - 지정하지 않았거나 비어 있으면(공백만 있어도) /api 를 쓴다.
 */
const baseURL = import.meta.env.VITE_API_BASE_URL?.trim() || '/api';

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

/** 로그인·가입 요청의 401은 "이메일 또는 비밀번호 오류"이므로 세션 만료로 보지 않는다. */
const CREDENTIAL_ENDPOINTS = new Set(['/v1/users/login', '/v1/users/register']);

// 로그인 상태에서 BE가 401(토큰 만료·무효, 토큰 없는 이전 세션)을 주면 로그아웃한다.
// ProtectedRoute 가 /login 으로 보내고, 로그인 화면이 logoutReason 으로 만료 안내 배너를 띄운다.
// 에러는 그대로 넘기므로 각 화면의 onError 도 지금처럼 실행된다.
apiClient.interceptors.response.use(undefined, (error: unknown) => {
  if (axios.isAxiosError(error) && error.response?.status === 401) {
    const config = error.config;
    const { isAuthenticated, accessToken, logout } = useAuthStore.getState();
    // 요청에 실린 토큰이 지금 토큰과 같을 때만 처리한다. 다시 로그인해 토큰이 바뀐 뒤 늦게 도착한
    // 이전 토큰의 401은 무시한다. 둘 다 없으면(토큰 없는 이전 세션) 같다고 보고 로그아웃한다.
    const sentAuthorization = config?.headers?.get('Authorization') ?? null;
    const currentAuthorization = accessToken ? `Bearer ${accessToken}` : null;
    if (
      isAuthenticated &&
      !CREDENTIAL_ENDPOINTS.has(config?.url ?? '') &&
      sentAuthorization === currentAuthorization
    ) {
      logout('tokenExpired');
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

/** 시도 횟수 초과(429) 안내. 번역표에 없는 429(운영 WAF·로드밸런서의 HTML·{"message": ...} 본문 등)에도 쓴다. */
const TOO_MANY_ATTEMPTS_MESSAGE = '시도가 너무 많습니다. 잠시 후 다시 시도해주세요.';

/** BE가 반환하는 알려진 영문 에러 메시지 → 사용자용 한국어 메시지 */
const KNOWN_ERROR_MESSAGES: Record<string, string> = {
  'Invalid email or password': '이메일 또는 비밀번호가 올바르지 않습니다.',
  'Email already registered': '이미 가입된 이메일입니다.',
  'User not found or not verified yet.': '사용자 정보를 찾을 수 없습니다. 다시 로그인해주세요.',
  'Authentication required': '로그인이 필요합니다. 다시 로그인해주세요.',
  'Invalid token': '로그인 정보가 올바르지 않습니다. 다시 로그인해주세요.',
  'Token expired': '로그인이 만료되었습니다. 다시 로그인해주세요.',
  // 같은 이메일로 로그인을 너무 많이 틀림 (BE 429)
  'Too many attempts. Try again later.': TOO_MANY_ATTEMPTS_MESSAGE,
  // 모의 발급 (403 / 429 / 404)
  'Template not allowed': '허용되지 않은 배지 템플릿입니다.',
  'Mock issuance limit reached; try again later': '모의 발급 한도(24시간)를 넘었습니다. 나중에 다시 시도해주세요.',
  'Mock issuer is disabled': '이 서버에서는 모의 발급을 사용할 수 없습니다.',
  // 발급·검증 협력사 오류 (502 / 503). 협력사 메시지는 BE 로그에만 남는다.
  'Credential service error': '배지 발급·검증 서비스에서 오류가 발생했습니다.',
  'Credential service is not configured': '배지 발급·검증 서비스를 지금 사용할 수 없습니다.',
  'AI service temporarily unavailable': 'AI 서버에 연결할 수 없습니다. AI 서버가 실행 중인지 확인해주세요.',
  'No eligible reviewed job sources; import sources first':
    '비교에 쓸 수 있는 최근 공고가 없습니다. 연구자가 공고를 먼저 반입해야 합니다.',
  'Target posting is stale, closed, synthetic or outside filters':
    '선택한 공고는 마감되었거나 수집 후 7일이 지나 비교에 쓸 수 없습니다.',
  'Career analysis expired; create a new analysis first':
    '직무 비교 결과의 유효시간(5분)이 지났습니다. 다시 비교한 뒤 계획을 만들어주세요.',
  'External LLM access is not enabled for this account': '이 계정은 LLM 모드를 사용할 수 없습니다.',
  'LEARNING_CHOICE_IS_OUTSIDE_ACTIONABLE_GAPS': '선택한 학습 항목이 비교 결과의 보완 대상이 아닙니다.',
  LLM_CONTEXT_TOO_LARGE: '계획 옵션이 너무 많아 LLM에 보낼 수 없습니다. 템플릿 모드를 사용해주세요.',
  // 서버 설정 이름이 들어간 운영자용 안내는 개발 서버(npm run dev)에서만 쓴다. 운영 빌드에서는 번들에서 빠지고
  // (import.meta.env.DEV 가 false 로 바뀌어 이 분기가 사라짐), 여기 없는 5xx는 getApiErrorMessage가 공통 문구로 바꾼다.
  ...(import.meta.env.DEV
    ? {
        'AI service is not configured':
          'BE에 AI 서버 주소·토큰이 설정되지 않았습니다 (AI_SERVICE_URL, AI_SERVICE_TOKEN).',
        'AI service authentication failed': 'BE와 AI 서버의 AI_SERVICE_TOKEN이 서로 다릅니다.',
        'AI service response failed contract checks':
          'AI 응답이 BE 검사를 통과하지 못했습니다. BE와 AI의 공유 형식(ai_contract)을 맞춰주세요.',
        'Credential service error':
          '발급·검증(DXWorks) 호출에 실패했습니다. 원인은 BE 로그의 "Provider error" 줄에서 확인해주세요.',
        'Credential service is not configured':
          'BE에 발급·검증 설정이 없습니다 (DXWORKS_API_HOST, DXWORKS_API_KEY, DXWORKS_BADGE_TEMPLATE_ID).',
      }
    : {}),
};

/** 운영 빌드에서 개발자용 안내(로컬 주소·서버 로그) 대신 붙이는 공통 안내 */
export const RETRY_LATER_MESSAGE = '잠시 후 다시 시도해주세요. 문제가 계속되면 관리자에게 문의해주세요.';

/** 운영 빌드에서 번역되지 않은 5xx 본문 대신 보여 주는 문구 (외부 서비스·서버 내부 문구를 화면에 싣지 않는다) */
const SERVER_ERROR_MESSAGE = '서버에서 일시적인 오류가 발생했습니다.';

/**
 * BE에 연결할 수 없을 때(서버 미실행, 프록시·로드밸런서의 본문 없는 5xx) 화면에 붙이는 안내.
 * 로컬 주소는 개발 서버에서만 보이고 운영 빌드에서는 번들에서 빠진다.
 */
export function getConnectionHint(): string {
  return import.meta.env.DEV
    ? 'BE 서버(http://localhost:3000)가 실행 중인지 확인한 뒤 다시 시도해주세요.'
    : RETRY_LATER_MESSAGE;
}

/** 번역표에 있는 메시지인지 (Object.prototype 의 이름은 제외) */
function isKnownMessage(message: unknown): message is string {
  return typeof message === 'string' && Object.hasOwn(KNOWN_ERROR_MESSAGES, message);
}

function translate(message: string): string {
  return isKnownMessage(message) ? KNOWN_ERROR_MESSAGES[message] : message;
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
    return import.meta.env.DEV
      ? 'BE 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인해주세요.'
      : '서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.';
  }
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<ApiErrorBody>;
    const body = axiosError.response?.data;
    const detail = body?.detail;
    // 번역표에 없는 429(본문 없음·HTML·{"message": ...} 등 BE가 아닌 WAF·로드밸런서의 거절)도 같은 한국어 안내로 보여 준다.
    if (axiosError.response?.status === 429 && !isKnownMessage(detail)) return TOO_MANY_ATTEMPTS_MESSAGE;
    if (Array.isArray(detail)) return formatValidationDetail(detail);
    // 운영 빌드: 번역하지 않은 5xx 본문(외부 서비스·서버 내부 문구일 수 있음)은 화면에 그대로 싣지 않는다.
    if (!import.meta.env.DEV && !isKnownMessage(detail) && (axiosError.response?.status ?? 0) >= 500) {
      return SERVER_ERROR_MESSAGE;
    }
    if (typeof detail === 'string') return translate(detail);
    return body?.message ?? body?.error ?? axiosError.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return '알 수 없는 오류가 발생했습니다.';
}

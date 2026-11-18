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

/**
 * 토큰이 필요 없는 공개 엔드포인트. 여기에는 토큰을 싣지 않는다(열람자·로그인 전 사용자의 토큰을 보낼 이유가 없음).
 * 여기서 나는 401은 "이메일 또는 비밀번호 오류" 등 요청 자체의 실패이지 세션 만료가 아니다.
 * - 로그인·회원가입
 * - 소셜 로그인 시작·콜백 (`/v1/users/oauth/{provider}/...`)
 * - 기업용 공유 링크 조회 (v0.6.0)
 */
const PUBLIC_PATHS: ReadonlySet<string> = new Set(['/v1/users/login', '/v1/users/register']);
const PUBLIC_PREFIXES: readonly string[] = ['/v1/users/oauth/', '/v1/public/'];

function isPublicPath(url: string): boolean {
  return PUBLIC_PATHS.has(url) || PUBLIC_PREFIXES.some((prefix) => url.startsWith(prefix));
}

// 로그인 토큰이 있으면 공개 API를 뺀 모든 요청에 싣는다. BE는 토큰의 DID로 본인 데이터만 허용한다.
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().accessToken;
  if (token && !isPublicPath(config.url ?? '')) {
    config.headers.set('Authorization', `Bearer ${token}`);
  }
  return config;
});

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
    if (isAuthenticated && !isPublicPath(config?.url ?? '') && sentAuthorization === currentAuthorization) {
      logout('tokenExpired');
    }
  }
  return Promise.reject(error);
});

/** axios 에러의 HTTP 상태 코드 (네트워크 오류 등 응답이 없으면 undefined) */
export function getApiErrorStatus(error: unknown): number | undefined {
  return axios.isAxiosError(error) ? error.response?.status : undefined;
}

/** BE가 보낸 detail 원문 (문자열일 때만). 같은 상태 코드 안에서 화면을 나눠야 할 때 쓴다. */
export function getApiErrorDetail(error: unknown): string | undefined {
  if (!axios.isAxiosError(error)) return undefined;
  const detail = (error.response?.data as ApiErrorBody | undefined)?.detail;
  return typeof detail === 'string' ? detail : undefined;
}

/** BE의 응답을 전혀 받지 못한 요청인지 (연결 실패·시간 초과). 본문 없는 5xx 처럼 응답이 하나라도 왔으면 false */
export function isNoResponseError(error: unknown): boolean {
  return axios.isAxiosError(error) && !error.response;
}

/**
 * 화면 분기용 에러 종류.
 * - network: BE에 연결 불가 (서버 미실행 / vite 프록시가 연결 실패 시 본문 없이 5xx 반환)
 * - unauthorized: 401 (로그인 실패 — 비밀번호 오류와 미가입 이메일을 BE가 구분하지 않음,
 *   또는 토큰 없음/만료/위조)
 * - forbidden: 403 (남의 DID에 접근 — BE v0.4.0)
 * - conflict: 400 (이미 가입된 이메일 등 요청 거부), 409 (다른 사용자에게 이미 등록된 자격증명)
 * - notFound: 404 (DID에 해당하는 사용자 없음 / 가입되지 않은 DID로 발급·검증 / 없는 자격증명·포트폴리오,
 *   없거나 철회·만료된 공유 링크)
 * - validation: 422 (요청 본문 검증 실패 / 포트폴리오에 쓸 검증된 자격증명 없음)
 * - server: BE가 detail과 함께 반환한 5xx (발급·검증 API 오류 502, 설정 누락 503, 내부 오류 500)
 */
export type ApiErrorKind =
  | 'network'
  | 'unauthorized'
  | 'forbidden'
  | 'conflict'
  | 'notFound'
  | 'validation'
  | 'rateLimited'
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
    case 429:
      return 'rateLimited';
    default:
      if (response.status >= 500) return hasDetail ? 'server' : 'network';
      return 'unknown';
  }
}

/** 시도 횟수 초과(429) 안내. 번역표에 없는 429(BE가 아닌 프록시·로드밸런서의 HTML·{"message": ...} 본문 등)에도 쓴다. */
const TOO_MANY_ATTEMPTS_MESSAGE = '시도가 너무 많습니다. 잠시 후 다시 시도해주세요.';

/**
 * 소셜 로그인 콜백의 409 detail 중 "같은 이메일이 이미 이메일·비밀번호 계정으로 가입돼 있음".
 * 같은 409 인 "같은 제공자의 다른 계정이 이미 연동됨"과 화면을 나누기 위해 콜백 화면이 detail 원문과 비교한다.
 */
export const OAUTH_PASSWORD_ACCOUNT_DETAIL =
  'This email is registered with a password. Log in with your email and password.';

/**
 * 소셜 로그인 콜백의 409 detail 중 "같은 이메일이 이미 다른 방법(다른 소셜 로그인 등)으로 가입돼 있음".
 * BE는 이메일 인증을 보장하는 제공자(Google·GitHub)의 인증된 이메일로만 기존 계정에 연동한다 (네이버 이메일은 연동하지 않음).
 */
export const OAUTH_OTHER_METHOD_ACCOUNT_DETAIL =
  'This email is already used by another sign-in method. Log in with that method.';

/** 소셜 로그인 콜백의 409 detail 중 "이 이메일 계정에 같은 제공자의 다른 계정이 이미 연동됨" */
export const OAUTH_ALREADY_LINKED_DETAIL = 'User is already linked to another account for this provider';

/**
 * 소셜 로그인 시작·콜백의 400 detail: FE가 보낸 콜백 주소가 BE의 `*_REDIRECT_URI`와 다름 (배포 설정 문제).
 * 다시 시도해도 같으므로 화면이 "잠시 후 다시 시도" 안내를 덧붙이지 않는다 (번역 문구가 이미 관리자 문의로 끝남).
 */
export const OAUTH_INVALID_REDIRECT_DETAIL = 'Invalid redirect_uri';

/** 소셜 로그인 콜백의 400 detail: 제공자가 이메일을 주지 않음 (사용자가 이메일 제공에 동의하지 않음 등) */
export const OAUTH_EMAIL_REQUIRED_DETAIL = 'OAuth email is required';

/** 소셜 로그인 콜백의 400 detail: 제공자가 이메일을 인증되지 않았다고 알림 (Google·GitHub) */
export const OAUTH_EMAIL_NOT_VERIFIED_DETAIL = 'OAuth email is not verified';

/** BE가 반환하는 알려진 영문 에러 메시지 → 사용자용 한국어 메시지 */
const KNOWN_ERROR_MESSAGES: Record<string, string> = {
  'Invalid email or password': '이메일 또는 비밀번호가 올바르지 않습니다.',
  'Email already registered': '이미 가입된 이메일입니다.',
  'User not found or not verified yet.': '사용자 정보를 찾을 수 없습니다. 다시 로그인해주세요.',
  'User not found': '사용자 정보를 찾을 수 없습니다. 다시 로그인해주세요.',
  'Authentication required': '로그인이 필요합니다. 다시 로그인해주세요.',
  'Invalid token': '로그인 정보가 올바르지 않습니다. 다시 로그인해주세요.',
  'Token expired': '로그인이 만료되었습니다. 다시 로그인해주세요.',
  // 같은 이메일로 로그인을 너무 많이 틀림 (BE 429)
  'Too many attempts. Try again later.': TOO_MANY_ATTEMPTS_MESSAGE,
  // DB 연결 한도·쿼리 시간 초과로 지금 처리할 수 없음 (BE 503 + Retry-After). 모든 API에서 날 수 있다
  'Service is busy. Try again later.': '요청이 많아 처리가 지연되고 있습니다. 잠시 후 다시 시도해주세요.',
  // 권한 (403)
  'Forbidden: not your DID': '본인 DID의 정보만 조회할 수 있습니다.',
  'Forbidden: can only issue to your own DID': '본인 DID로만 배지를 발급받을 수 있습니다.',
  'Forbidden: credential was issued to a different DID': '다른 DID로 발급된 배지는 저장할 수 없습니다.',
  'Forbidden: presentation holder is not you': '본인의 신원 증명만 검증할 수 있습니다.',
  // 발급·검증
  'Holder DID is not registered': '가입되지 않은 DID입니다. 다시 로그인해주세요.',
  'Credential already registered to another user': '이미 다른 사용자의 지갑에 등록된 자격증명입니다.',
  'Internal server error': '서버 내부 오류가 발생했습니다.',
  // 모의 발급 (403 / 429 / 404)
  'Template not allowed': '허용되지 않은 배지 템플릿입니다.',
  'Mock issuance limit reached; try again later': '모의 발급 한도(24시간)를 넘었습니다. 나중에 다시 시도해주세요.',
  // 사용자별 한도가 아니라 서버 전체의 모의 발급 한도(24시간)에 도달함 (BE 429). 발급을 적게 한 사용자도 받을 수 있다
  'Mock issuance service limit reached; try again later.':
    '전체 모의 발급 한도(24시간)에 도달했습니다. 잠시 후 다시 시도하거나 관리자에게 문의해주세요.',
  // 발급 서버(협력사) 오류가 이어져 BE가 모의 발급을 잠시 멈춤 (BE 429 + Retry-After). BE가 약 1분마다 한 건씩 보내 보고 성공하면 풀린다
  'Mock issuance is paused after repeated failures. Try again shortly.':
    '발급 서버 오류가 이어져 모의 발급을 잠시 멈췄습니다. 1분쯤 뒤에 다시 시도해주세요.',
  'Mock issuer is disabled': '이 서버에서는 모의 발급을 사용할 수 없습니다.',
  // 발급·검증 협력사 오류 (502 / 503). 협력사 메시지는 BE 로그에만 남는다.
  'Credential service error': '배지 발급·검증 서비스에서 오류가 발생했습니다.',
  'Credential service is not configured': '배지 발급·검증 서비스를 지금 사용할 수 없습니다.',
  // 로그인 사용자의 검증 호출 한도 초과 (BE 429 + Retry-After): 검증·재검증·포트폴리오 저장·직무 비교
  'Too many verification requests. Try again later.': '검증 요청이 너무 많습니다. 잠시 후 다시 시도해주세요.',
  // 재검증·포트폴리오·선택 공개 (v0.6.0)
  'Credential not found': '자격증명을 찾을 수 없습니다. 목록을 새로고침해주세요.',
  'No verified credentials selected': '선택한 자격증명 중 지금 검증을 통과한 것이 없습니다.',
  'Portfolio not found': '포트폴리오를 찾을 수 없습니다.',
  'Nothing to update': '수정할 내용이 없습니다.',
  'Title must not be blank': '제목을 입력해주세요.',
  'Status must not be null': '게시 상태를 선택해주세요.',
  'Share not found': '공유 링크를 찾을 수 없습니다.',
  'Share link not found or expired': '링크가 없거나 철회·만료되었습니다.',
  'Share verification is busy. Try again later.': '현재 검증 요청이 많습니다. 잠시 후 다시 열어주세요.',
  // 사용자별 개수 한도 (409)
  'Share link limit reached. Revoke unused links first.':
    '공유 링크를 더 만들 수 없습니다. 사용하지 않는 링크를 먼저 폐기해주세요.',
  'Portfolio limit reached.': '포트폴리오를 더 만들 수 없습니다.',
  // 직무 비교·학습 계획의 사용자별 한도 (BE 429 + Retry-After). 공고 조회·직무 비교·학습 계획이 같은 몫을 쓴다
  'Too many analysis requests. Try again later.': '분석 요청이 너무 많습니다. 잠시 후 다시 시도해주세요.',
  'Daily career analysis limit reached. Try again later.':
    '오늘 만들 수 있는 직무 비교 수를 모두 썼습니다. 내일 다시 시도해주세요.',
  'Daily curriculum limit reached. Try again later.': '오늘 만들 수 있는 학습 계획 수를 모두 썼습니다. 내일 다시 시도해주세요.',
  'Daily LLM curriculum limit reached. Use the template mode or try again later.':
    '오늘 만들 수 있는 LLM 학습 계획 수를 모두 썼습니다. 템플릿으로 만들거나 내일 다시 시도해주세요.',
  // 모든 계정을 합친 LLM 학습 계획 하루 한도 (BE 429 + Retry-After, CURRICULUM_LLM_GLOBAL_DAILY_LIMIT)
  'Daily LLM curriculum service limit reached. Use the template mode or try again later.':
    '오늘 서비스 전체의 LLM 학습 계획 한도에 도달했습니다. 템플릿으로 만들거나 내일 다시 시도해주세요.',
  // 같은 계정의 LLM 모드 계획은 한 번에 한 건 (BE 429 + Retry-After)
  'An LLM curriculum is already being generated for this account. Try again later.':
    '이 계정의 LLM 학습 계획을 이미 만들고 있습니다. 끝난 뒤 다시 시도해주세요.',
  // 소셜 로그인 (Google·GitHub·네이버 OAuth 2.0). 400 / 409 / 502 / 503
  // 콜백 화면은 아래 이메일·연동 안내 중 일부를 제공자 이름을 넣은 문구로 바꿔 보여 준다 (pages/OAuthCallbackPage.tsx).
  'Invalid or expired OAuth state': '로그인 요청이 만료되었거나 올바르지 않습니다. 처음부터 다시 시도해주세요.',
  'Invalid or expired OAuth authorization code': '로그인 인증이 만료되었습니다. 처음부터 다시 시도해주세요.',
  'Invalid OAuth access token': '소셜 계정 정보를 확인하지 못했습니다. 처음부터 다시 시도해주세요.',
  [OAUTH_EMAIL_NOT_VERIFIED_DETAIL]: '소셜 계정의 이메일이 인증되지 않아 로그인할 수 없습니다.',
  // 제공자가 이메일을 주지 않음 (400). 네이버는 사용자가 이메일 제공 동의를 거절할 수 있고,
  // GitHub 은 이메일 권한(user:email)을 주지 않았거나 기본(primary) 이메일을 찾지 못한 경우다
  [OAUTH_EMAIL_REQUIRED_DETAIL]:
    '소셜 계정의 이메일 제공에 동의해야 로그인할 수 있어요. 동의 항목을 확인한 뒤 다시 시도해주세요.',
  'Missing OAuth code or state': '로그인 응답이 올바르지 않습니다. 처음부터 다시 시도해주세요.',
  'redirect_uri does not match OAuth state': '로그인 요청이 올바르지 않습니다. 처음부터 다시 시도해주세요.',
  'Invalid code_challenge': '로그인 요청이 올바르지 않습니다. 처음부터 다시 시도해주세요.',
  'Only S256 code_challenge_method is supported': '로그인 요청이 올바르지 않습니다. 처음부터 다시 시도해주세요.',
  'Invalid redirect_uri': '허용되지 않은 로그인 경로입니다. 관리자에게 문의해주세요.',
  [OAUTH_ALREADY_LINKED_DETAIL]:
    '이 이메일 계정에는 이미 같은 서비스의 다른 소셜 계정이 연동되어 있습니다. 연동된 계정으로 로그인해주세요.',
  // 같은 이메일이 이미 이메일·비밀번호 계정으로 가입돼 있다 (409). BE는 비밀번호가 있는 계정에 소셜 계정을 붙이지 않는다
  [OAUTH_PASSWORD_ACCOUNT_DETAIL]: '이미 이메일·비밀번호로 가입된 계정입니다. 이메일과 비밀번호로 로그인해주세요.',
  // 같은 이메일이 이미 다른 소셜 로그인 등 다른 방법으로 가입돼 있다 (409). 인증되지 않은 이메일로는 기존 계정에 연동하지 않는다
  [OAUTH_OTHER_METHOD_ACCOUNT_DETAIL]:
    '이미 다른 방법(이메일 또는 다른 소셜 로그인)으로 가입한 이메일입니다. 처음 가입한 방법으로 로그인해주세요.',
  'Google OAuth is not configured': 'Google 로그인을 지금 사용할 수 없습니다.',
  'Google OAuth redirect_uri is not configured': 'Google 로그인을 지금 사용할 수 없습니다.',
  'GitHub OAuth is not configured': 'GitHub 로그인을 지금 사용할 수 없습니다.',
  'GitHub OAuth redirect_uri is not configured': 'GitHub 로그인을 지금 사용할 수 없습니다.',
  'Naver OAuth is not configured': '네이버 로그인을 지금 사용할 수 없습니다.',
  'Naver OAuth redirect_uri is not configured': '네이버 로그인을 지금 사용할 수 없습니다.',
  // 제공자 쪽 응답 실패 (502). 원인은 BE 로그의 "Provider error" 줄에만 남는다
  'Google OAuth error': 'Google 로그인 서비스에 일시적인 문제가 있습니다.',
  'GitHub OAuth error': 'GitHub 로그인 서비스에 일시적인 문제가 있습니다.',
  'Naver OAuth error': '네이버 로그인 서비스에 일시적인 문제가 있습니다.',
  // 직무별 요구 역량 통계 (404 / 409)
  'Market snapshot not found': '이 직무의 통계가 아직 없습니다.',
  'Market snapshot integrity check failed': '저장된 통계 자료가 손상되어 보여 줄 수 없습니다. 관리자에게 문의해주세요.',
  // 직무 비교·커리큘럼 (AI)
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
  // 공고·학습 자료가 지원 규모를 넘음 (422 / 413). 화면에 지역·경력 필터가 없어 사용자가 줄일 방법은 없다
  'Too many eligible postings; narrow region or seniority':
    '비교에 쓸 수 있는 공고가 너무 많아 지금은 처리할 수 없습니다. 관리자에게 문의해주세요.',
  'Local corpus is too large; narrow the job sources':
    '등록된 공고가 너무 많아 지금은 처리할 수 없습니다. 관리자에게 문의해주세요.',
  'Request too large for the AI service':
    'AI 서버로 보낼 자료가 너무 커서 지금은 처리할 수 없습니다. 관리자에게 문의해주세요.',
  'Learning catalog exceeds supported corpus size':
    '등록된 학습 자료가 너무 많아 지금은 계획을 만들 수 없습니다. 관리자에게 문의해주세요.',
  SELECT_TARGET_POSTING_OR_NARROW_COMPARISON:
    '비교한 요구사항이 너무 많아 계획을 만들 수 없습니다. 공고 하나를 골라 다시 비교해주세요.',
  // 비교·계획 도중 자료가 바뀌었거나 유효시간이 지남 (409)
  'Job source changed during analysis; retry': '비교하는 동안 공고 내용이 바뀌었습니다. 다시 비교해주세요.',
  'Learning source changed; create the plan again':
    '계획을 만드는 동안 학습 자료가 바뀌었습니다. 계획을 다시 만들어주세요.',
  'Evidence expired during generation; create a new analysis':
    '계획을 만드는 동안 직무 비교 결과의 유효시간이 지났습니다. 다시 비교한 뒤 계획을 만들어주세요.',
  'Create a fresh full-profile career analysis first':
    '직무 비교 결과의 유효시간(5분)이 지났습니다. 다시 비교한 뒤 계획을 만들어주세요.',
  'Verification snapshot is stale or invalid; verify again':
    '자격증명 검증 결과의 유효시간이 지났습니다. 다시 비교해주세요.',
  'Idempotency key was used for a different request':
    '같은 요청 식별자로 다른 내용의 요청이 이미 처리되었습니다. 계획을 다시 만들어주세요.',
  // 요청이 저장된 공고·비교 결과와 맞지 않음 (404 / 409 / 422)
  'Job posting not found': '선택한 공고를 찾을 수 없습니다. 화면을 새로고침한 뒤 다시 골라주세요.',
  'Analysis not found': '직무 비교 결과를 찾을 수 없습니다. 다시 비교해주세요.',
  'Target posting does not match the request': '선택한 공고가 요청과 맞지 않습니다. 다시 비교해주세요.',
  'Curriculum request does not match the analysis':
    '계획 요청이 직무 비교 결과와 맞지 않습니다. 다시 비교한 뒤 계획을 만들어주세요.',
  INVALID_ALTERNATIVE_CHOICE: '선택한 대안 기술이 비교 결과와 맞지 않습니다. 다시 비교한 뒤 골라주세요.',
  UNUSED_OR_UNKNOWN_ALTERNATIVE_CHOICE: '선택한 대안 기술이 비교 결과와 맞지 않습니다. 다시 비교한 뒤 골라주세요.',
  // 저장된 자료의 무결성 검사 실패 (409)
  'Job source integrity check failed': '저장된 공고 자료가 손상되어 비교에 쓸 수 없습니다. 관리자에게 문의해주세요.',
  'Learning source integrity check failed':
    '저장된 학습 자료가 손상되어 계획에 쓸 수 없습니다. 관리자에게 문의해주세요.',
  'Stored analysis integrity check failed': '저장된 직무 비교 결과가 손상되었습니다. 다시 비교해주세요.',
  'Stored curriculum integrity check failed': '저장된 학습 계획이 손상되었습니다. 계획을 다시 만들어주세요.',
  // 계획 생성 실패 (422). LLM 응답을 쓸 수 없거나 계획 자료에 문제가 있다
  LLM_PROVIDER_REJECTED: 'LLM이 계획을 만들지 못했습니다. 템플릿 모드를 사용해주세요.',
  LLM_RESPONSE_TOO_LARGE: 'LLM이 계획을 만들지 못했습니다. 템플릿 모드를 사용해주세요.',
  LLM_MALFORMED_RESPONSE: 'LLM이 계획을 만들지 못했습니다. 템플릿 모드를 사용해주세요.',
  CYCLIC_RESOURCE_PREREQUISITES:
    '학습 자료의 선수 조건이 서로 얽혀 있어 계획을 만들 수 없습니다. 관리자에게 문의해주세요.',
  UNTRUSTED_OR_UNSUPPORTED_JOB_INPUT: '학습 계획을 만들지 못했습니다. 다시 비교한 뒤 계획을 만들어주세요.',
  INTERNAL_GENERATION_ERROR: '학습 계획을 만들지 못했습니다. 다시 비교한 뒤 계획을 만들어주세요.',
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
        'Google OAuth is not configured': 'BE에 Google OAuth 설정이 없습니다 (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET).',
        'Google OAuth redirect_uri is not configured': 'BE에 GOOGLE_REDIRECT_URI가 설정되지 않았습니다.',
        'GitHub OAuth is not configured': 'BE에 GitHub OAuth 설정이 없습니다 (GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET).',
        'GitHub OAuth redirect_uri is not configured': 'BE에 GITHUB_REDIRECT_URI가 설정되지 않았습니다.',
        'Naver OAuth is not configured': 'BE에 네이버 OAuth 설정이 없습니다 (NAVER_CLIENT_ID, NAVER_CLIENT_SECRET).',
        'Naver OAuth redirect_uri is not configured': 'BE에 NAVER_REDIRECT_URI가 설정되지 않았습니다.',
        'Invalid redirect_uri':
          'BE가 허용하지 않은 콜백 주소입니다. FE 콜백 주소를 Google은 GOOGLE_REDIRECT_URI / GOOGLE_ALLOWED_REDIRECT_URIS에 넣고, GitHub·네이버는 GITHUB_REDIRECT_URI / NAVER_REDIRECT_URI와 똑같이 맞춰주세요.',
      }
    : {}),
};

/**
 * 본문의 기계용 `code` → 사용자용 한국어 메시지. detail 문구가 따로 정해지지 않은 응답에 쓰며 detail 번역표보다 먼저 본다.
 */
const KNOWN_ERROR_CODES: Record<string, string> = {
  // BE→AI 동시 호출 한도 초과 (503 + Retry-After). 공고 조회·직무 비교·학습 계획
  AI_SERVICE_BUSY: '분석 요청이 많습니다. 잠시 후 다시 시도해주세요.',
  // AI 서비스의 동시 LLM 생성 한도 초과 (BE 503 + Retry-After). LLM 모드 학습 계획
  LLM_BUSY: 'LLM 학습 계획 요청이 많습니다. 잠시 후 다시 시도해주세요.',
  // LLM(Claude)에 연결하지 못했거나 응답이 늦음 (BE 503 + Retry-After)
  LLM_PROVIDER_UNAVAILABLE: 'LLM 서비스에 연결하지 못했습니다. 템플릿 모드를 쓰거나 잠시 후 다시 시도해주세요.',
  // AI 서비스에 LLM 설정이 없거나 잘못됨 (BE 503). 템플릿 모드는 그대로 쓸 수 있다
  GENERATOR_CONFIGURATION_REQUIRED: '지금은 LLM 모드를 사용할 수 없습니다. 템플릿 모드를 사용해주세요.',
  // 배포 중 AI 설정이 바뀌어 다시 요청해야 함 (BE 503 + Retry-After)
  GENERATOR_CONFIGURATION_CHANGED: 'LLM 설정이 바뀌는 중입니다. 잠시 후 다시 시도해주세요.',
};

/**
 * 다시 시도하면 될 수 있는 실패(503 + Retry-After)일 때 KNOWN_ERROR_CODES 대신 쓰는 문구.
 * LLM(Claude) 429·529·5xx(LLM_PROVIDER_REJECTED)는 같은 코드라도 503이면 기다렸다 다시 하면 되고, 422면 다시 해도 같다.
 */
const RETRYABLE_ERROR_CODES: Record<string, string> = {
  LLM_PROVIDER_REJECTED: 'LLM 서비스가 지금 요청을 받지 못했습니다. 템플릿 모드를 쓰거나 잠시 후 다시 시도해주세요.',
};

/** 운영 빌드에서 개발자용 안내(로컬 주소·서버 로그) 대신 붙이는 공통 안내 */
export const RETRY_LATER_MESSAGE = '잠시 후 다시 시도해주세요. 문제가 계속되면 관리자에게 문의해주세요.';

/**
 * 화면이 오류 문구 뒤에 "잠시 후 다시 시도" 안내(RETRY_LATER_MESSAGE·개발용 안내)를 덧붙여도 되는지.
 * 429 와 혼잡 안내(검증 요청 한도, DB·AI 혼잡 503)는 문구가 이미 같은 안내로 끝나므로 덧붙이지 않는다.
 */
export function needsRetryHint(status: number | undefined, message: string): boolean {
  return status !== 429 && !message.endsWith('잠시 후 다시 시도해주세요.');
}

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
  title: '제목',
  target_role: '희망 직무',
  status: '게시 상태',
  record_ids: '자격증명',
  source: '근거 출처',
  label: '링크 이름',
  expires_in_days: '유효 기간',
  code: '인가 코드',
  state: '인증 상태값',
  redirect_uri: '콜백 주소',
  code_verifier: 'PKCE 검증값',
  target_posting_id: '비교 대상 공고',
  analysis_id: '직무 비교 ID',
  idempotency_key: '요청 식별자',
  weeks: '기간(주)',
  hours_per_week: '주당 학습 시간',
  budget_krw: '예산(원)',
  mode: '생성 모드',
  allow_external_processing: '외부 모델 전달 동의',
  alternative_choices: '대안 기술 선택',
  learning_choices: '학습 진행 방식',
};

/** BE 입력 검사기(app/core/text.py)의 422 메시지 → 한국어 */
const VALIDATION_MESSAGES: Record<string, string> = {
  'Value error, must be a single line without control characters':
    '줄바꿈·제어 문자·보이지 않는 글자 없이, 보이는 글자로 입력해주세요',
  'Value error, must not contain NUL or unpaired surrogate characters': '저장할 수 없는 문자가 들어 있습니다',
};

/** FastAPI 422 의 detail 배열을 한 줄 메시지로 합친다. */
function formatValidationDetail(detail: ValidationErrorItem[]): string {
  const messages = detail
    .map((item) => {
      const field = item.loc
        ?.filter((part) => part !== 'body')
        .map((part) => (Object.hasOwn(FIELD_LABELS, String(part)) ? FIELD_LABELS[String(part)] : String(part)))
        .join('.');
      const msg = item.msg && Object.hasOwn(VALIDATION_MESSAGES, item.msg) ? VALIDATION_MESSAGES[item.msg] : item.msg;
      return field && msg ? `${field}: ${msg}` : msg;
    })
    .filter((msg): msg is string => Boolean(msg));
  return messages.length > 0
    ? `입력값을 확인해주세요. (${messages.join(', ')})`
    : '입력값을 확인해주세요.';
}

export function getApiErrorMessage(error: unknown): string {
  const code = axios.isAxiosError(error) ? (error.response?.data as ApiErrorBody | undefined)?.code : undefined;
  if (
    typeof code === 'string' &&
    Object.hasOwn(RETRYABLE_ERROR_CODES, code) &&
    axios.isAxiosError(error) &&
    error.response?.status === 503
  ) {
    return RETRYABLE_ERROR_CODES[code];
  }
  if (typeof code === 'string' && Object.hasOwn(KNOWN_ERROR_CODES, code)) return KNOWN_ERROR_CODES[code];
  if (getApiErrorKind(error) === 'network') {
    return import.meta.env.DEV
      ? 'BE 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인해주세요.'
      : '서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요.';
  }
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<ApiErrorBody>;
    const body = axiosError.response?.data;
    const detail = body?.detail;
    // 번역표에 없는 429(본문 없음·HTML·{"message": ...} 등 BE가 아닌 프록시·로드밸런서의 거절)도 같은 한국어 안내로 보여 준다.
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

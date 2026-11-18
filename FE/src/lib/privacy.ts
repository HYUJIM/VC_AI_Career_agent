/**
 * 개인정보 처리 안내와 가입 전 동의.
 *
 * 내용을 바꾸면 PRIVACY_POLICY_VERSION을 올린다: 이 브라우저에 남은 예전 버전 동의는 무효가 되어 다시 묻는다.
 * 동의 기록은 이 브라우저(localStorage)에만 남는다. 서버에는 저장하지 않는다.
 */

export const PRIVACY_POLICY_VERSION = '2026-11-18';
export const PRIVACY_POLICY_PATH = '/privacy';

const STORAGE_KEY = 'vc-privacy-consent';

/** 이 브라우저에서 현재 버전 안내에 동의했는지 */
export function hasPrivacyConsent(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === PRIVACY_POLICY_VERSION;
  } catch {
    return false;
  }
}

export function savePrivacyConsent(agreed: boolean): void {
  try {
    if (agreed) window.localStorage.setItem(STORAGE_KEY, PRIVACY_POLICY_VERSION);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // 저장소를 쓸 수 없는 브라우저(사생활 보호 모드 등): 이 화면에서만 동의 상태를 유지한다.
  }
}

export interface PrivacyRow {
  label: string;
  items: string;
  purpose: string;
  retention: string;
}

/** 수집·이용 항목 (가입 동의 화면과 /privacy가 같은 표를 쓴다) */
export const COLLECTION_ROWS: PrivacyRow[] = [
  {
    label: '회원 가입 (필수)',
    items: '이름, 이메일, 비밀번호 (되돌릴 수 없는 해시로만 저장)',
    purpose: '회원 식별, 로그인',
    retention: '탈퇴 또는 서비스 종료 시까지',
  },
  {
    label: '지갑 (가입 때 자동 생성)',
    items: 'DID(탈중앙 식별자), 지갑 서명 키 (서버가 암호화해 보관)',
    purpose: '자격증명 발급·보관·제시',
    retention: '탈퇴 또는 서비스 종료 시까지',
  },
  {
    label: '서비스 이용',
    items: '발급·저장한 자격증명(VC) 원본과 검증 결과, 직무 비교 결과, 학습 계획, 포트폴리오, 공유 링크 기록',
    purpose: '자격증명 검증·보관, 직무 비교·학습 계획·포트폴리오·공유 링크 제공',
    retention: '사용자가 지울 때 또는 탈퇴·서비스 종료 시까지',
  },
  {
    label: '소셜 로그인 (선택)',
    items: 'Google·GitHub·네이버 계정 식별자, 이메일, 이름 (제공자가 주는 범위)',
    purpose: '소셜 계정으로 가입·로그인',
    retention: '탈퇴 또는 서비스 종료 시까지',
  },
  {
    label: '자동 수집',
    items: '접속 IP, 요청 시각·경로',
    purpose: '보안, 부정 이용·과도한 요청 방지',
    retention: '서버 로그 용량 한도에 따라 오래된 것부터 삭제',
  },
];

export interface TransferRow {
  to: string;
  task: string;
  data: string;
}

/** 처리 위탁·외부 전송 */
export const TRANSFER_ROWS: TransferRow[] = [
  {
    to: 'DXWorks (협력사)',
    task: '자격증명 발급·검증',
    data: '자격증명 원본, DID',
  },
  {
    to: '네이버클라우드 (국내)',
    task: '서버·데이터베이스 운영',
    data: '이 서비스에 저장되는 모든 정보 (보관)',
  },
  {
    to: 'Anthropic (미국, 선택)',
    task: 'LLM 학습 계획·AI 포트폴리오 문장 작성 (각 화면에서 따로 동의할 때만)',
    data: '학습 계획 옵션, 자격증명 이름·발급 기관·발급일·설명·성취 기준, 희망 직무, 작성 방향 (이름·이메일·DID 제외)',
  },
  {
    to: 'Google·GitHub·네이버 (선택)',
    task: '소셜 로그인 인증',
    data: '로그인 요청 (이 서비스로 돌아올 주소)',
  },
];

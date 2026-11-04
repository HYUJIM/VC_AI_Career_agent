import { useAuthStore } from '@/store/authStore';
import type {
  CareersResponse,
  IssueMockBadgeResponse,
  UserProfile,
  VerifyResponse,
} from '@/types/api';

/**
 * 개발 모드(`npm run dev`) 전용 미리보기. BE 없이 화면 디자인을 확인하기 위한 가짜 데이터.
 * 프로덕션 빌드에서는 import.meta.env.DEV 가 false 이므로 동작하지 않는다.
 */
export const PREVIEW_ENABLED = import.meta.env.DEV;

export const PREVIEW_USER: UserProfile = {
  id: 'preview-user',
  did: 'did:preview:z6MkhaXgBZDvotDkL5257faiztiGiC2QtKLGpbnnEGta2doK',
  email: 'preview@test.com',
  name: '미리보기 사용자',
};

export function isPreviewSession(): boolean {
  return PREVIEW_ENABLED && useAuthStore.getState().user?.id === PREVIEW_USER.id;
}

const delay = (ms = 500) => new Promise((r) => setTimeout(r, ms));

export async function previewCareers(): Promise<CareersResponse> {
  await delay(300);
  const now = Date.now();
  const mk = (i: number, issuer: string, name: string, status: string) => ({
    record_id: `rec-0000-${i}a7f`,
    issuer,
    achievement_name: name,
    status,
    issued_at: new Date(now - i * 86400000 * 30).toISOString(),
    raw_data: {
      type: ['VerifiableCredential', 'OpenBadgeCredential'],
      issuer: { name: issuer },
      credentialSubject: { id: PREVIEW_USER.did, achievement: { name } },
    },
  });
  return {
    user_profile: PREVIEW_USER,
    verified_credentials: [
      mk(1, '한국산업인력공단 (HRDK)', '정보처리기사', 'verified'),
      mk(2, 'AMAZON WEB SERVICES', 'AWS Solutions Architect - Associate', 'verified'),
      mk(3, 'GOOGLE AI & TENSORFLOW', 'TensorFlow Developer', 'verified'),
      mk(4, '한국데이터산업진흥원 (K-DATA)', 'SQLD SQL 개발자', 'expired'),
      mk(5, 'ETS GLOBAL', 'TOEIC Speaking AL', 'pending'),
    ],
  };
}

export async function previewIssue(): Promise<IssueMockBadgeResponse> {
  await delay();
  return { success: true, message: 'preview', vcJwt: 'eyJhbGciOiJFZERTQSJ9.preview.signature'.repeat(3) };
}

export async function previewVerify(): Promise<VerifyResponse> {
  await delay();
  // BE openapi.yaml 의 badgeVerified 예시와 같은 형태
  return {
    success: true,
    message: 'Badge verified and saved.',
    data: {
      valid: true,
      badge: { name: '정보처리기사', issuedAt: new Date().toISOString() },
      checks: {
        issuer: { issuerName: '한국산업인력공단' },
        issuerTrust: { trusted: true },
        credentialId: { id: 'urn:uuid:preview', credentialStatus: 'VALID' },
        recipient: { subjectId: PREVIEW_USER.did },
      },
    },
  };
}

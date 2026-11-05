import { useAuthStore } from '@/store/authStore';
import type {
  CareersResponse,
  Evidence,
  ExcludedEvidence,
  IssueMockBadgeResponse,
  MeResponse,
  Portfolio,
  PortfolioCreateRequest,
  PortfolioListResponse,
  PortfolioResponse,
  PortfolioUpdateRequest,
  ReverifyResponse,
  ShareCreateRequest,
  ShareCreateResponse,
  ShareListResponse,
  ShareSummary,
  SimpleSuccess,
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
      record_id: '00000000-0000-4000-8000-000000000001',
      status: 'verified',
      valid: true,
      achievement_name: '정보처리기사',
      issuer_name: '한국산업인력공단',
      issuer_trusted: true,
      recipient_did: PREVIEW_USER.did,
      issued_at: new Date().toISOString(),
      expires_at: null,
      checks: [],
    },
  };
}

export async function previewMe(): Promise<MeResponse> {
  await delay(200);
  return { success: true, message: 'OK', data: PREVIEW_USER, wallet: { custody: 'managed', key_type: 'Ed25519' } };
}

/** 미리보기 careers 데이터로 BE `Evidence` 형식을 흉내 낸다 (verified 만 재검증 통과). */
async function previewEvidence(recordIds: string[]): Promise<Evidence[]> {
  const { verified_credentials } = await previewCareers();
  const now = new Date().toISOString();
  return verified_credentials
    .filter((c) => recordIds.includes(c.record_id))
    .map((c) => ({
      record_id: c.record_id,
      status: c.status,
      verified: c.status === 'verified',
      achievement_name: c.achievement_name,
      achievement_description: null,
      criteria: null,
      issuer_name: c.issuer,
      issued_at: c.issued_at,
      expires_at: null,
      verified_at: c.status === 'pending' ? null : now,
      checks: [],
    }));
}

export async function previewReverify(recordId: string): Promise<ReverifyResponse> {
  const [evidence] = await previewEvidence([recordId]);
  if (!evidence) throw new Error('미리보기 데이터에 없는 자격증명입니다.');
  return {
    success: evidence.verified,
    message: evidence.verified ? 'Credential re-verified.' : `Re-verification failed: ${evidence.status}`,
    data: evidence,
  };
}

// ── 미리보기용 포트폴리오·공유 링크 (메모리에만 보관, 새로고침하면 사라짐) ──
const previewPortfolios: Portfolio[] = [];
const previewShares: ShareSummary[] = [];

const previewId = () => `preview-${Math.random().toString(36).slice(2, 10)}`;

export async function previewListPortfolios(): Promise<PortfolioListResponse> {
  await delay(200);
  return {
    success: true,
    data: previewPortfolios.map(
      ({ id, title, target_role, status, credential_count, created_at, updated_at }) => ({
        id,
        title,
        target_role,
        status,
        credential_count,
        created_at,
        updated_at,
      }),
    ),
  };
}

export async function previewGetPortfolio(id: string): Promise<PortfolioResponse> {
  await delay(200);
  const found = previewPortfolios.find((p) => p.id === id);
  if (!found) throw new Error('포트폴리오를 찾을 수 없습니다.');
  return { success: true, data: found };
}

export async function previewCreatePortfolio(req: PortfolioCreateRequest): Promise<PortfolioResponse> {
  await delay(700);
  const all = await previewEvidence(req.record_ids);
  const evidence = all.filter((e) => e.verified);
  const excluded: ExcludedEvidence[] = all
    .filter((e) => !e.verified)
    .map((e) => ({ record_id: e.record_id, status: e.status, reason: e.status }));
  if (evidence.length === 0) throw new Error('검증을 통과한 자격증명이 없습니다.');
  const issuers = new Set(evidence.map((e) => e.issuer_name));
  const content = [
    `# ${req.title}`,
    '',
    `- 이름: ${PREVIEW_USER.name}`,
    ...(req.target_role ? [`- 희망 직무: ${req.target_role}`] : []),
    '',
    '## 요약',
    '',
    `${issuers.size}개 기관에서 발급한 ${evidence.length}개의 검증된 자격증명을 근거로 역량을 증명합니다.`,
    '',
    '## 검증된 자격증명',
    '',
    ...evidence.flatMap((e) => [
      `### ${e.achievement_name}`,
      `- 발급 기관: ${e.issuer_name}`,
      `- 발급일: ${e.issued_at.slice(0, 10)}`,
      `- 검증: 서명·발급기관·폐기 여부 확인 완료 (${(e.verified_at ?? '').slice(0, 10)} 기준)`,
      '',
    ]),
  ].join('\n');
  const now = new Date().toISOString();
  const portfolio: Portfolio = {
    id: previewId(),
    title: req.title,
    target_role: req.target_role ?? null,
    status: 'draft',
    credential_count: evidence.length,
    created_at: now,
    updated_at: now,
    source: 'VC_ONLY',
    generator: 'template',
    content_markdown: content,
    sections: evidence,
    referenced_record_ids: evidence.map((e) => e.record_id),
    excluded,
  };
  previewPortfolios.unshift(portfolio);
  return { success: true, message: 'Portfolio created.', data: portfolio };
}

const ROLE_PREFIX = '- 희망 직무: ';

/** BE `rewrite_header` 와 같은 동작: 첫 `## ` 앞의 제목·희망 직무 줄만 바꾸고 근거 섹션은 그대로 둔다. */
function previewRewriteHeader(content: string, title: string, targetRole: string | null): string {
  const lines = content.split('\n');
  const endIdx = lines.findIndex((l) => l.startsWith('## '));
  const end = endIdx === -1 ? lines.length : endIdx;
  const head = lines.slice(0, end).filter((l) => !l.startsWith(ROLE_PREFIX));
  let titleIdx = head.findIndex((l) => l.startsWith('# '));
  if (titleIdx === -1) {
    head.unshift(`# ${title}`, '');
    titleIdx = 0;
  } else {
    head[titleIdx] = `# ${title}`;
  }
  if (targetRole) {
    const anchor = head.findIndex((l) => l.startsWith('- 이름: '));
    if (anchor === -1) head.splice(titleIdx + 1, 0, '', `${ROLE_PREFIX}${targetRole}`);
    else head.splice(anchor + 1, 0, `${ROLE_PREFIX}${targetRole}`);
  }
  return [...head, ...lines.slice(end)].join('\n');
}

export async function previewUpdatePortfolio(
  id: string,
  fields: PortfolioUpdateRequest,
): Promise<PortfolioResponse> {
  await delay(300);
  const found = previewPortfolios.find((p) => p.id === id);
  if (!found) throw new Error('포트폴리오를 찾을 수 없습니다.');
  Object.assign(found, fields, { updated_at: new Date().toISOString() });
  if ('title' in fields || 'target_role' in fields) {
    found.content_markdown = previewRewriteHeader(found.content_markdown, found.title, found.target_role);
  }
  return { success: true, message: 'Portfolio updated.', data: found };
}

export async function previewDeletePortfolio(id: string): Promise<SimpleSuccess> {
  await delay(200);
  const idx = previewPortfolios.findIndex((p) => p.id === id);
  if (idx >= 0) previewPortfolios.splice(idx, 1);
  return { success: true, message: 'Portfolio deleted.' };
}

export async function previewListShares(): Promise<ShareListResponse> {
  await delay(200);
  return { success: true, data: [...previewShares] };
}

export async function previewCreateShare(req: ShareCreateRequest): Promise<ShareCreateResponse> {
  await delay(400);
  const now = Date.now();
  const share: ShareSummary = {
    id: previewId(),
    label: req.label ?? null,
    record_ids: req.record_ids,
    expires_at: new Date(now + (req.expires_in_days ?? 14) * 86400000).toISOString(),
    revoked: false,
    view_count: 0,
    last_viewed_at: null,
    created_at: new Date(now).toISOString(),
  };
  previewShares.unshift(share);
  const token = `preview-${Math.random().toString(36).slice(2)}`;
  return {
    success: true,
    message: 'Share link created. The token is shown only once.',
    data: { ...share, token, share_path: `/share/${token}` },
  };
}

export async function previewRevokeShare(id: string): Promise<SimpleSuccess> {
  await delay(200);
  const found = previewShares.find((s) => s.id === id);
  if (found) found.revoked = true;
  return { success: true, message: 'Share link revoked.' };
}

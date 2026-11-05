import { apiClient } from '@/lib/apiClient';
import { isPreviewSession, previewCareers, previewIssue, previewVerify } from '@/lib/preview';
import type {
  CareersResponse,
  IssueMockBadgeResponse,
  VerifyRequest,
  VerifyResponse,
} from '@/types/api';

/** GET /api/v1/subjects/{did}/careers */
export async function getCareersByDid(did: string): Promise<CareersResponse> {
  if (isPreviewSession()) return previewCareers();
  const { data } = await apiClient.get<CareersResponse>(
    `/v1/subjects/${encodeURIComponent(did)}/careers`,
  );
  return data;
}

/**
 * POST /api/mock-issuer/issue (논문/테스트용 모의 배지 발급)
 * templateId를 비우면 BE가 서버 설정의 기본 템플릿을 쓴다.
 */
export async function issueMockBadge(
  templateId: string | undefined,
  holderDid: string,
): Promise<IssueMockBadgeResponse> {
  if (isPreviewSession()) return previewIssue();
  const trimmed = templateId?.trim();
  const { data } = await apiClient.post<IssueMockBadgeResponse>('/mock-issuer/issue', {
    holderDid,
    ...(trimmed ? { templateId: trimmed } : {}),
  });
  return data;
}

/** POST /api/v1/verify */
export async function verifyCredential(payload: VerifyRequest): Promise<VerifyResponse> {
  if (isPreviewSession()) return previewVerify();
  const { data } = await apiClient.post<VerifyResponse>('/v1/verify', payload);
  return data;
}

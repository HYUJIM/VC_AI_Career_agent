import { apiClient } from '@/lib/apiClient';
import type {
  CareersResponse,
  IssueMockBadgeResponse,
  VerifyRequest,
  VerifyResponse,
} from '@/types/api';

/** GET /api/v1/subjects/{did}/careers */
export async function getCareersByDid(did: string): Promise<CareersResponse> {
  const { data } = await apiClient.get<CareersResponse>(
    `/v1/subjects/${encodeURIComponent(did)}/careers`,
  );
  return data;
}

/** POST /api/mock-issuer/issue (논문/테스트용 모의 배지 발급) */
export async function issueMockBadge(
  templateId: string,
  holderDid: string,
): Promise<IssueMockBadgeResponse> {
  const { data } = await apiClient.post<IssueMockBadgeResponse>('/mock-issuer/issue', {
    templateId,
    holderDid,
  });
  return data;
}

/** POST /api/v1/verify */
export async function verifyCredential(payload: VerifyRequest): Promise<VerifyResponse> {
  const { data } = await apiClient.post<VerifyResponse>('/v1/verify', payload);
  return data;
}

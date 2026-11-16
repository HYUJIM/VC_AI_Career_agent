import { apiClient } from '@/lib/apiClient';
import { cleanEvidenceText } from '@/lib/text';
import {
  isPreviewSession,
  previewCareers,
  previewIssue,
  previewReverify,
  previewVerify,
} from '@/lib/preview';
import type {
  CareersResponse,
  IssueMockBadgeResponse,
  ReverifyResponse,
  VerifyRequest,
  VerifyResponse,
} from '@/types/api';

/** GET /api/v1/subjects/{did}/careers */
export async function getCareersByDid(did: string): Promise<CareersResponse> {
  if (import.meta.env.DEV && isPreviewSession()) return previewCareers();
  const { data } = await apiClient.get<CareersResponse>(
    `/v1/subjects/${encodeURIComponent(did)}/careers`,
  );
  // 배지 이름·발급기관은 발급기관이 서명한 문구 그대로라 보이지 않는 글자(방향 제어 등)를 지워 보여 준다 (lib/text.ts).
  if (!Array.isArray(data?.verified_credentials)) return data;
  return { ...data, verified_credentials: data.verified_credentials.map(cleanEvidenceText) };
}

/**
 * POST /api/mock-issuer/issue (논문/테스트용 모의 배지 발급)
 * templateId를 비우면 BE가 서버 설정의 기본 템플릿을 쓴다.
 */
export async function issueMockBadge(
  templateId: string | undefined,
  holderDid: string,
): Promise<IssueMockBadgeResponse> {
  if (import.meta.env.DEV && isPreviewSession()) return previewIssue();
  const trimmed = templateId?.trim();
  const { data } = await apiClient.post<IssueMockBadgeResponse>('/mock-issuer/issue', {
    holderDid,
    ...(trimmed ? { templateId: trimmed } : {}),
  });
  return data;
}

/** POST /api/v1/verify */
export async function verifyCredential(payload: VerifyRequest): Promise<VerifyResponse> {
  if (import.meta.env.DEV && isPreviewSession()) return previewVerify();
  const { data } = await apiClient.post<VerifyResponse>('/v1/verify', payload);
  return typeof data?.data === 'object' && data.data !== null && 'achievement_name' in data.data
    ? { ...data, data: cleanEvidenceText(data.data) }
    : data;
}

/**
 * POST /api/v1/credentials/{record_id}/reverify (v0.6.0 실행 시점 재검증)
 * 저장된 서명 원본을 지금 다시 검증하고 DB 상태를 갱신한다. 서명 원본이 없으면 status: unverifiable.
 */
export async function reverifyCredential(recordId: string): Promise<ReverifyResponse> {
  if (import.meta.env.DEV && isPreviewSession()) return previewReverify(recordId);
  const { data } = await apiClient.post<ReverifyResponse>(
    `/v1/credentials/${encodeURIComponent(recordId)}/reverify`,
  );
  return data?.data ? { ...data, data: cleanEvidenceText(data.data) } : data;
}

/**
 * 재검증 응답이 "검증 서버가 이 자격증명 하나를 확인하지 못함"(BE reason verifier_error)인지.
 * BE는 이때 200 + success:false, message `Re-verification failed: verifier_error` 로 답하고 저장된 상태는 바꾸지 않는다
 * (검증 서버 장애는 502/503). data 는 status 'unverifiable'·verified_at null 이라 '서명 원본 없음' 응답과 data 만으로는
 * 구분되지 않으므로 message 원문과 비교한다.
 */
export function isVerifierErrorResult(res: ReverifyResponse): boolean {
  return res.success === false && res.message === 'Re-verification failed: verifier_error';
}

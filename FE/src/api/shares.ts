import { apiClient } from '@/lib/apiClient';
import {
  isPreviewSession,
  previewCreateShare,
  previewListShares,
  previewRevokeShare,
} from '@/lib/preview';
import type {
  PublicShare,
  ShareCreateRequest,
  ShareCreateResponse,
  ShareListResponse,
  SimpleSuccess,
} from '@/types/api';

/**
 * 선택 공개 API (BE v0.6.0).
 * 학생이 고른 자격증명만 기업에 보여 주는 링크. 토큰은 생성 응답에서 한 번만 내려오고 서버에는 해시만 저장된다.
 */

/** GET /api/v1/shares (최근 생성순) */
export async function listShares(): Promise<ShareListResponse> {
  if (isPreviewSession()) return previewListShares();
  const { data } = await apiClient.get<ShareListResponse>('/v1/shares');
  return data;
}

/** POST /api/v1/shares */
export async function createShare(payload: ShareCreateRequest): Promise<ShareCreateResponse> {
  const body: ShareCreateRequest = {
    record_ids: payload.record_ids,
    label: payload.label?.trim() || null,
    ...(payload.expires_in_days ? { expires_in_days: payload.expires_in_days } : {}),
  };
  if (isPreviewSession()) return previewCreateShare(body);
  const { data } = await apiClient.post<ShareCreateResponse>('/v1/shares', body);
  return data;
}

/** DELETE /api/v1/shares/{id} — 철회한 링크는 기업용 조회에서 바로 404 */
export async function revokeShare(id: string): Promise<SimpleSuccess> {
  if (isPreviewSession()) return previewRevokeShare(id);
  const { data } = await apiClient.delete<SimpleSuccess>(`/v1/shares/${encodeURIComponent(id)}`);
  return data;
}

/**
 * GET /api/v1/public/shares/{token} — 기업용 조회 (로그인 불필요).
 * 열 때마다 BE가 서명 원본을 다시 검증한다. 없는·철회된·만료된 링크는 모두 같은 404.
 */
export async function getPublicShare(token: string): Promise<PublicShare> {
  const { data } = await apiClient.get<PublicShare>(`/v1/public/shares/${encodeURIComponent(token)}`);
  return data;
}

/** BE가 준 share_path(/share/{token})로 기업에 전달할 전체 URL을 만든다. */
export function buildShareUrl(sharePath: string): string {
  return new URL(sharePath, window.location.origin).toString();
}

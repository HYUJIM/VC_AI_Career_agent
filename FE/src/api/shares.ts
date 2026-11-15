import { apiClient } from '@/lib/apiClient';
import {
  isPreviewSession,
  previewCreateShare,
  previewListShares,
  previewRevokeShare,
} from '@/lib/preview';
import { sanitizeSingleLine } from '@/lib/text';
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
  if (import.meta.env.DEV && isPreviewSession()) return previewListShares();
  const { data } = await apiClient.get<ShareListResponse>('/v1/shares');
  return data;
}

/** POST /api/v1/shares */
export async function createShare(payload: ShareCreateRequest): Promise<ShareCreateResponse> {
  const cleanedLabel = payload.label ? sanitizeSingleLine(payload.label) : '';
  const body: ShareCreateRequest = {
    record_ids: payload.record_ids,
    label: cleanedLabel || null,
    ...(payload.expires_in_days ? { expires_in_days: payload.expires_in_days } : {}),
  };
  if (import.meta.env.DEV && isPreviewSession()) return previewCreateShare(body);
  const { data } = await apiClient.post<ShareCreateResponse>('/v1/shares', body);
  return data;
}

/** DELETE /api/v1/shares/{id} — 철회한 링크는 기업용 조회에서 바로 404 */
export async function revokeShare(id: string): Promise<SimpleSuccess> {
  if (import.meta.env.DEV && isPreviewSession()) return previewRevokeShare(id);
  const { data } = await apiClient.delete<SimpleSuccess>(`/v1/shares/${encodeURIComponent(id)}`);
  return data;
}

/**
 * GET /api/v1/public/shares/{token} — 기업용 조회 (로그인 불필요).
 * 열 때마다 BE가 서명 원본을 다시 검증한다. 없는·철회된·만료된 링크는 모두 같은 404.
 */
export async function getPublicShare(token: string): Promise<PublicShare> {
  const { data } = await apiClient.get<PublicShare>(`/v1/public/shares/${encodeURIComponent(token)}`);
  // 200 이지만 공유 응답이 아닌 본문(예: /api 요청이 SPA 의 index.html 로 넘어가는 배포 설정)은 오류로 처리한다.
  // 그대로 넘기면 화면이 credentials 를 읽다가 흰 화면이 된다. 오류로 넘기면 "불러오지 못했습니다" 안내가 나온다.
  if (!Array.isArray(data?.credentials)) {
    throw new Error('서버 응답이 올바르지 않습니다.');
  }
  return data;
}

/**
 * BE가 준 share_path(/share/{token})로 기업에 전달할 전체 URL을 만든다.
 * 다른 출처(//evil.com 등)가 섞이지 않도록 반드시 현재 origin의 `/share/` 경로만 허용한다.
 */
export function buildShareUrl(sharePath: string): string {
  const trimmed = sharePath.trim();
  if (!trimmed.startsWith('/share/') || trimmed.startsWith('//')) {
    throw new Error('공유 링크 경로 형식이 올바르지 않습니다.');
  }
  const resolved = new URL(trimmed, window.location.origin);
  if (resolved.origin !== window.location.origin) {
    throw new Error('공유 링크 출처가 올바르지 않습니다.');
  }
  return resolved.toString();
}

import { apiClient } from '@/lib/apiClient';
import {
  isPreviewSession,
  previewCreatePortfolio,
  previewDeletePortfolio,
  previewGetPortfolio,
  previewListPortfolios,
  previewUpdatePortfolio,
} from '@/lib/preview';
import type {
  PortfolioCreateRequest,
  PortfolioListResponse,
  PortfolioResponse,
  PortfolioUpdateRequest,
  SimpleSuccess,
} from '@/types/api';

/**
 * 포트폴리오 API (BE v0.6.0, VC_ONLY).
 * 자유 텍스트 본문은 보낼 수 없고 자격증명 ID 목록만 보낸다. BE가 생성 시점에 서명 원본을 다시 검증해
 * 통과한 것만 본문에 넣고 나머지는 `excluded` 로 돌려준다. 통과한 것이 하나도 없으면 422.
 */

/** GET /api/v1/portfolios (최근 수정순) */
export async function listPortfolios(): Promise<PortfolioListResponse> {
  if (isPreviewSession()) return previewListPortfolios();
  const { data } = await apiClient.get<PortfolioListResponse>('/v1/portfolios');
  return data;
}

/** GET /api/v1/portfolios/{id} */
export async function getPortfolio(id: string): Promise<PortfolioResponse> {
  if (isPreviewSession()) return previewGetPortfolio(id);
  const { data } = await apiClient.get<PortfolioResponse>(`/v1/portfolios/${encodeURIComponent(id)}`);
  return data;
}

/** POST /api/v1/portfolios */
export async function createPortfolio(
  payload: Omit<PortfolioCreateRequest, 'source'>,
): Promise<PortfolioResponse> {
  const body: PortfolioCreateRequest = {
    title: payload.title.trim(),
    target_role: payload.target_role?.trim() || null,
    record_ids: payload.record_ids,
    source: 'VC_ONLY',
  };
  if (isPreviewSession()) return previewCreatePortfolio(body);
  const { data } = await apiClient.post<PortfolioResponse>('/v1/portfolios', body);
  return data;
}

/** PATCH /api/v1/portfolios/{id} — 제목·희망 직무·상태만 수정 가능 (본문 수정 불가) */
export async function updatePortfolio(id: string, payload: PortfolioUpdateRequest): Promise<PortfolioResponse> {
  if (isPreviewSession()) return previewUpdatePortfolio(id, payload);
  const { data } = await apiClient.patch<PortfolioResponse>(`/v1/portfolios/${encodeURIComponent(id)}`, payload);
  return data;
}

/** DELETE /api/v1/portfolios/{id} */
export async function deletePortfolio(id: string): Promise<SimpleSuccess> {
  if (isPreviewSession()) return previewDeletePortfolio(id);
  const { data } = await apiClient.delete<SimpleSuccess>(`/v1/portfolios/${encodeURIComponent(id)}`);
  return data;
}

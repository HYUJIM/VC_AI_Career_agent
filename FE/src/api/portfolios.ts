import { apiClient } from '@/lib/apiClient';
import {
  isPreviewSession,
  previewCreatePortfolio,
  previewDeletePortfolio,
  previewGetPortfolio,
  previewListPortfolios,
  previewUpdatePortfolio,
} from '@/lib/preview';
import { cleanEvidenceText, sanitizeSingleLine } from '@/lib/text';
import type {
  PortfolioCreateRequest,
  PortfolioListResponse,
  PortfolioRegenerateRequest,
  PortfolioResponse,
  PortfolioUpdateRequest,
  SimpleSuccess,
} from '@/types/api';

/**
 * 포트폴리오 API (BE v0.6.0, VC_ONLY).
 * 자유 텍스트 본문은 보낼 수 없고 자격증명 ID 목록만 보낸다. BE가 생성 시점에 서명 원본을 다시 검증해
 * 통과한 것만 본문에 넣고 나머지는 `excluded` 로 돌려준다. 통과한 것이 하나도 없으면 422.
 */

/** 본문에 들어간 근거(sections)의 배지 문구에서 보이지 않는 글자를 지운다 (화면 표시용, lib/text.ts cleanEvidenceText) */
function cleanPortfolio(response: PortfolioResponse): PortfolioResponse {
  const sections = response?.data?.sections;
  if (!Array.isArray(sections)) return response;
  return { ...response, data: { ...response.data, sections: sections.map(cleanEvidenceText) } };
}

/** GET /api/v1/portfolios (최근 수정순) */
export async function listPortfolios(): Promise<PortfolioListResponse> {
  if (import.meta.env.DEV && isPreviewSession()) return previewListPortfolios();
  const { data } = await apiClient.get<PortfolioListResponse>('/v1/portfolios');
  return data;
}

/** GET /api/v1/portfolios/{id} */
export async function getPortfolio(id: string): Promise<PortfolioResponse> {
  if (import.meta.env.DEV && isPreviewSession()) return previewGetPortfolio(id);
  const { data } = await apiClient.get<PortfolioResponse>(`/v1/portfolios/${encodeURIComponent(id)}`);
  return cleanPortfolio(data);
}

/** POST /api/v1/portfolios */
export async function createPortfolio(
  payload: Omit<PortfolioCreateRequest, 'source'>,
): Promise<PortfolioResponse> {
  const body: PortfolioCreateRequest = {
    title: sanitizeSingleLine(payload.title),
    target_role: payload.target_role ? sanitizeSingleLine(payload.target_role) || null : null,
    record_ids: payload.record_ids,
    source: 'VC_ONLY',
    ...llmFields(payload),
  };
  if (import.meta.env.DEV && isPreviewSession()) return previewCreatePortfolio(body);
  const { data } = await apiClient.post<PortfolioResponse>('/v1/portfolios', body);
  return cleanPortfolio(data);
}

/** AI(LLM) 모드 필드: llm일 때만 동의와 작성 방향(한 줄)을 붙인다 */
function llmFields(payload: Pick<PortfolioCreateRequest, 'mode' | 'allow_external_processing' | 'direction'>) {
  if (payload.mode !== 'llm') return {};
  const direction = payload.direction ? sanitizeSingleLine(payload.direction).slice(0, 200) || null : null;
  return { mode: 'llm' as const, allow_external_processing: Boolean(payload.allow_external_processing), direction };
}

/** POST /api/v1/portfolios/{id}/regenerate — 지금 다시 검증해 같은 포트폴리오의 본문을 새로 만든다 */
export async function regeneratePortfolio(
  id: string,
  payload: Omit<PortfolioRegenerateRequest, 'source'>,
): Promise<PortfolioResponse> {
  const body: PortfolioRegenerateRequest = { record_ids: payload.record_ids, source: 'VC_ONLY', ...llmFields(payload) };
  if (import.meta.env.DEV && isPreviewSession()) return previewGetPortfolio(id);
  const { data } = await apiClient.post<PortfolioResponse>(
    `/v1/portfolios/${encodeURIComponent(id)}/regenerate`,
    body,
  );
  return cleanPortfolio(data);
}

/** PATCH /api/v1/portfolios/{id} — 제목·희망 직무·상태만 수정 가능 (본문 수정 불가) */
export async function updatePortfolio(id: string, payload: PortfolioUpdateRequest): Promise<PortfolioResponse> {
  const body: PortfolioUpdateRequest = {
    ...payload,
    ...(payload.title !== undefined ? { title: sanitizeSingleLine(payload.title) } : {}),
    ...(payload.target_role !== undefined
      ? { target_role: payload.target_role ? sanitizeSingleLine(payload.target_role) || null : null }
      : {}),
  };
  if (import.meta.env.DEV && isPreviewSession()) return previewUpdatePortfolio(id, body);
  const { data } = await apiClient.patch<PortfolioResponse>(`/v1/portfolios/${encodeURIComponent(id)}`, body);
  return cleanPortfolio(data);
}

/** DELETE /api/v1/portfolios/{id} */
export async function deletePortfolio(id: string): Promise<SimpleSuccess> {
  if (import.meta.env.DEV && isPreviewSession()) return previewDeletePortfolio(id);
  const { data } = await apiClient.delete<SimpleSuccess>(`/v1/portfolios/${encodeURIComponent(id)}`);
  return data;
}

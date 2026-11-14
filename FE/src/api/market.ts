import { apiClient } from '@/lib/apiClient';
import type { ApiEnvelope } from '@/types/career';
import type { MarketRoleId, MarketSnapshot, MarketSnapshotSummary } from '@/types/market';

/** GET /api/v1/market/snapshots — 통계가 있는 직무 목록과 상위 기술 요약 (로그인 필요) */
export async function getMarketSnapshots(): Promise<MarketSnapshotSummary[]> {
  const { data } = await apiClient.get<ApiEnvelope<MarketSnapshotSummary[]>>('/v1/market/snapshots');
  return data.data;
}

/** GET /api/v1/market/snapshots/{role_id} — 직무 하나의 최신 통계 전체 */
export async function getMarketSnapshot(roleId: MarketRoleId): Promise<MarketSnapshot> {
  const { data } = await apiClient.get<ApiEnvelope<MarketSnapshot>>(
    `/v1/market/snapshots/${encodeURIComponent(roleId)}`,
  );
  return data.data;
}

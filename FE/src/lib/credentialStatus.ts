import type { CredentialStatus } from '@/types/api';

/** 대시보드 필터 탭 단위. 세부 status를 사용자가 이해하기 쉬운 3개 그룹으로 묶는다. */
export type StatusGroup = 'verified' | 'attention' | 'pending';
export type StatusFilter = 'all' | StatusGroup;

export function getStatusGroup(status: CredentialStatus): StatusGroup {
  if (status === 'verified') return 'verified';
  if (status === 'pending') return 'pending';
  // invalid / revoked / expired / unverifiable / 비표준 상태 → 사용자 확인이 필요한 항목
  return 'attention';
}

export const STATUS_FILTER_TABS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: '전체' },
  { key: 'verified', label: '검증됨' },
  { key: 'attention', label: '확인 필요' },
  { key: 'pending', label: '대기 중' },
];

const STATUS_DESCRIPTION: Record<string, string> = {
  verified: '발급 기관의 서명과 상태 검증을 모두 통과한 자격증명이에요. 포트폴리오의 근거로 사용할 수 있어요.',
  invalid: '서명 또는 형식 검증에 실패했어요. 발급 기관에 재발급을 요청해주세요.',
  revoked: '발급 기관이 자격증명을 취소했어요. 포트폴리오에는 사용할 수 없어요.',
  expired: '유효기간이 지난 자격증명이에요. 갱신 후 다시 발급받아주세요.',
  unverifiable: '현재 발급 기관 정보를 확인할 수 없어 검증하지 못했어요. 잠시 후 다시 확인해주세요.',
  pending: '검증이 진행 중이에요. 완료되면 상태가 자동으로 바뀌어요.',
};

export function getStatusDescription(status: CredentialStatus): string {
  return STATUS_DESCRIPTION[status] ?? `알 수 없는 상태(${status})예요.`;
}

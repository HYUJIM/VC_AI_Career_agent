import type { AnchorVerifierPort } from "../ports.js";

/**
 * DX Ledger Builder(원장 앵커링) API 문서가 아직 없어 논문 기간에는 항상 skip을 반환한다.
 * 실연동 시 rawHash를 원장에서 조회한 anchored_hash와 비교하는 DxLedgerAnchorVerifier로 교체한다.
 * checks[] 배열의 자리(anchor_match)는 그대로 유지되므로 다른 팀 코드는 수정할 필요가 없다.
 */
export class FixtureAnchorVerifier implements AnchorVerifierPort {
  async checkAnchor(_rawHash: string): Promise<{ checked: boolean; match: boolean | null; detail: string }> {
    return {
      checked: false,
      match: null,
      detail: "DX Ledger Builder API 미연동 (논문 기간 skip, 논문 이후 실구현 예정)"
    };
  }
}

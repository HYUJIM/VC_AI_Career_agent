import type { Container } from "../container.js";
import { verifyAndNormalize } from "../verify/pipeline.js";
import type { CredentialRecord } from "../types/credential.js";

/**
 * 서버 시작 시 모든 fixture 자격증명을 한 번 검증 파이프라인에 돌려 인메모리에 캐싱한다.
 * 요청마다 다시 검증하지 않는 이유: 논문 기간 실험에서 같은 fixture를 수백~수천 번 반복 조회하므로
 * 매번 서명 검증을 다시 하면 느리고, 결과도 결정적이라 캐싱해도 손해가 없다.
 */
export class WalletStore {
  private records: CredentialRecord[] = [];
  private ready = false;

  constructor(private readonly container: Container) {}

  async init(): Promise<void> {
    const refs = await this.container.credentialSource.listAll();
    const results: CredentialRecord[] = [];
    for (const ref of refs) {
      try {
        const record = await verifyAndNormalize(ref, ref.provider, {
          issuerTrust: this.container.issuerTrust,
          revocationChecker: this.container.revocationChecker,
          anchorVerifier: this.container.anchorVerifier
        });
        results.push(record);
      } catch (err) {
        console.error(`[wallet-store] ${ref.externalId} 처리 중 오류:`, (err as Error).message);
      }
    }
    this.records = results;
    this.ready = true;
  }

  isReady(): boolean {
    return this.ready;
  }

  listAll(): CredentialRecord[] {
    return this.records;
  }

  listBySubject(subjectDid: string): CredentialRecord[] {
    return this.records.filter((r) => r.subject_did === subjectDid);
  }

  getById(recordId: string): CredentialRecord | undefined {
    return this.records.find((r) => r.record_id === recordId);
  }
}

import type { DIDResolver, DIDResolutionResult, ParsedDID, Resolvable } from "did-resolver";
import { getDidDocument } from "./didDocumentStore.js";

/**
 * did:fixture:<slug> 형태의 테스트 전용 DID method 드라이버.
 * 실제 DX Ledger DID API가 준비되면 이 파일과 동일한 시그니처의 드라이버(DxLedgerDidDriver)를
 * 새로 만들어 resolver.ts의 registry에 등록만 바꿔주면 된다. 상위 검증 파이프라인 코드는
 * `Resolver.resolve(did)`만 호출하므로 전혀 수정할 필요가 없다.
 */
export const fixtureDidDriver: DIDResolver = async (
  did: string,
  _parsed: ParsedDID,
  _resolver: Resolvable
): Promise<DIDResolutionResult> => {
  const document = getDidDocument(did);

  if (!document) {
    return {
      didResolutionMetadata: { error: "notFound", message: `fixture DID Document를 찾을 수 없습니다: ${did}` },
      didDocument: null,
      didDocumentMetadata: {}
    };
  }

  return {
    didResolutionMetadata: { contentType: "application/did+json" },
    didDocument: document,
    didDocumentMetadata: {}
  };
};

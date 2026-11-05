/**
 * jsonld.js의 canonize()는 기본적으로 @context URL을 네트워크로 fetch하려고 한다.
 * 로컬/CI 환경에서 네트워크 없이 결정적으로 동작해야 하므로, 이 프로젝트에서 쓰는
 * @context URL은 전부 여기 로컬 캐시로만 해석되게 한다(오프라인 documentLoader).
 *
 * 실제 DX Ledger 연동 시에는 W3C VC 2.0 / 1EdTech OB 3.0의 진짜 컨텍스트 파일을
 * 별도로 내려받아 캐시에 추가하고, 이 documentLoader가 그 캐시를 우선 참조하도록 확장하면 된다.
 * (운영 환경에서도 매 검증마다 네트워크로 context를 fetch하는 것은 성능·보안상 권장되지 않는다.)
 */

export const FIXTURE_VC_CONTEXT_URL = "https://wallet-core.local/contexts/vc-fixture-v1.json";

const VC_FIXTURE_CONTEXT_DOCUMENT = {
  "@context": {
    "@version": 1.1,
    id: "@id",
    type: "@type",
    wc: "https://wallet-core.local/vocab#",
    VerifiableCredential: "wc:VerifiableCredential",
    OpenBadgeCredential: "wc:OpenBadgeCredential",
    Profile: "wc:Profile",
    AchievementSubject: "wc:AchievementSubject",
    Achievement: "wc:Achievement",
    issuer: { "@id": "wc:issuer", "@type": "@id" },
    issuanceDate: { "@id": "wc:issuanceDate", "@type": "http://www.w3.org/2001/XMLSchema#dateTime" },
    expirationDate: { "@id": "wc:expirationDate", "@type": "http://www.w3.org/2001/XMLSchema#dateTime" },
    credentialSubject: { "@id": "wc:credentialSubject" },
    achievement: { "@id": "wc:achievement" },
    name: "wc:name",
    description: "wc:description",
    url: { "@id": "wc:url", "@type": "@id" },
    criteria: { "@id": "wc:criteria" },
    narrative: "wc:narrative",
    evidence: { "@id": "wc:evidence" },
    alignment: { "@id": "wc:alignment" },
    targetName: "wc:targetName",
    targetUrl: { "@id": "wc:targetUrl", "@type": "@id" },
    targetFramework: "wc:targetFramework",
    targetCode: "wc:targetCode"
  }
};

/**
 * fixture 자격증명은 실제 OB3.0 문서처럼 보이도록 이 두 실제 URL을 @context에 그대로 쓴다.
 * 다만 네트워크로 진짜 스펙을 받아오는 대신, 위 VC_FIXTURE_CONTEXT_DOCUMENT로 해석되도록 별칭 처리한다.
 * (실제 1EdTech/W3C 컨텍스트와 의미론적으로 동일하지는 않으므로, 실연동 시에는 진짜 컨텍스트 파일로 교체해야 한다.)
 */
const REAL_CONTEXT_URL_ALIASES = [
  "https://www.w3.org/ns/credentials/v2",
  "https://purl.imsglobal.org/spec/ob/v3p0/context.json"
];

const LOCAL_CONTEXTS: Record<string, object> = {
  [FIXTURE_VC_CONTEXT_URL]: VC_FIXTURE_CONTEXT_DOCUMENT,
  ...Object.fromEntries(REAL_CONTEXT_URL_ALIASES.map((url) => [url, VC_FIXTURE_CONTEXT_DOCUMENT]))
};

export interface JsonLdRemoteDocument {
  contextUrl: string | null;
  document: object;
  documentUrl: string;
}

/**
 * jsonld.js `documentLoader` 옵션에 그대로 꽂아 쓰는 오프라인 로더.
 * 캐시에 없는 URL을 요청하면 (네트워크로 새어나가는 대신) 즉시 에러를 던진다.
 */
export async function offlineDocumentLoader(url: string): Promise<JsonLdRemoteDocument> {
  const document = LOCAL_CONTEXTS[url];
  if (!document) {
    throw new Error(
      `오프라인 모드: 알 수 없는 JSON-LD @context URL(${url})입니다. src/crypto/contexts.ts 의 LOCAL_CONTEXTS에 추가하세요.`
    );
  }
  return { contextUrl: null, document, documentUrl: url };
}

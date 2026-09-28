import { FixtureAnchorVerifier } from "./adapters/fixture/fixtureAnchorVerifier.js";
import { FixtureCredentialSource } from "./adapters/fixture/fixtureCredentialSource.js";
import { FixtureIssuerTrustRegistry } from "./adapters/fixture/fixtureIssuerTrustRegistry.js";
import { FixtureRevocationChecker } from "./adapters/fixture/fixtureRevocationChecker.js";
import { env } from "./config/env.js";
import type { AnchorVerifierPort, CredentialSourcePort, IssuerTrustPort, RevocationCheckerPort } from "./adapters/ports.js";

export interface Container {
  credentialSource: CredentialSourcePort;
  issuerTrust: IssuerTrustPort;
  revocationChecker: RevocationCheckerPort;
  anchorVerifier: AnchorVerifierPort;
}

/**
 * 논문 기간 동안은 VC_SOURCE_MODE가 항상 "fixture"다.
 * 논문 이후 DX Ledger 연동 시, 이 함수의 "dxledger" 분기에 실제 어댑터들을 연결하면 되고
 * 나머지 코드(파서/검증 파이프라인/Mock 서버 라우트)는 전혀 수정할 필요가 없다.
 */
export function buildContainer(): Container {
  if (env.sourceMode === "dxledger") {
    throw new Error(
      "VC_SOURCE_MODE=dxledger는 아직 구현되지 않았습니다. 논문 기간에는 fixture 모드만 지원합니다."
    );
  }

  return {
    credentialSource: new FixtureCredentialSource(),
    issuerTrust: new FixtureIssuerTrustRegistry(),
    revocationChecker: new FixtureRevocationChecker(),
    anchorVerifier: new FixtureAnchorVerifier()
  };
}

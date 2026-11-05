import { Resolver } from "did-resolver";
import { fixtureDidDriver } from "./fixtureDidDriver.js";

/**
 * 벤더 격리 지점: 지금은 `fixture` method만 등록되어 있다.
 * 논문 이후 DX Ledger DID API가 붙으면 `{ fixture: fixtureDidDriver, <dx-method>: dxLedgerDidDriver }`
 * 형태로 registry에 한 줄만 추가하면 된다. 검증 파이프라인은 이 Resolver의 resolve()만 호출하므로
 * 어댑터 교체가 상위 로직에 영향을 주지 않는다.
 */
export const didResolver = new Resolver({
  fixture: fixtureDidDriver
});

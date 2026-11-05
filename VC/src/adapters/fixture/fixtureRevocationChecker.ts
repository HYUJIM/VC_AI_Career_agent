import { readFileSync } from "node:fs";
import path from "node:path";
import { env } from "../../config/env.js";
import type { RevocationCheckerPort } from "../ports.js";

interface RevocationList {
  revoked_external_ids: string[];
}

let cache: RevocationList | null = null;

function load(): RevocationList {
  if (cache) return cache;
  const filePath = path.join(env.fixturesDir, "registry", "revocation-list.json");
  cache = JSON.parse(readFileSync(filePath, "utf8")) as RevocationList;
  return cache;
}

export function clearRevocationCache(): void {
  cache = null;
}

/**
 * 실제 DX Ledger 연동 시에는 W3C Bitstring Status List 또는 DX Ledger 전용 REST 폐기 확인
 * API를 호출하는 DxLedgerRevocationChecker로 교체한다. 인터페이스(RevocationCheckerPort)는 동일하다.
 */
export class FixtureRevocationChecker implements RevocationCheckerPort {
  async isRevoked(credentialExternalId: string): Promise<boolean> {
    return load().revoked_external_ids.includes(credentialExternalId);
  }
}

import { readFileSync } from "node:fs";
import path from "node:path";
import { env } from "../../config/env.js";
import type { IssuerTrustLevel } from "../../types/credential.js";
import type { IssuerTrustPort } from "../ports.js";

interface TrustRegistryEntry {
  did: string;
  name: string;
  trust_level: IssuerTrustLevel;
}

let cache: TrustRegistryEntry[] | null = null;

function load(): TrustRegistryEntry[] {
  if (cache) return cache;
  const filePath = path.join(env.fixturesDir, "registry", "trust-registry.json");
  cache = JSON.parse(readFileSync(filePath, "utf8")) as TrustRegistryEntry[];
  return cache;
}

export function clearTrustRegistryCache(): void {
  cache = null;
}

export class FixtureIssuerTrustRegistry implements IssuerTrustPort {
  async getTrustLevel(issuerDid: string): Promise<IssuerTrustLevel> {
    const entry = load().find((e) => e.did === issuerDid);
    return entry?.trust_level ?? "unknown";
  }
}

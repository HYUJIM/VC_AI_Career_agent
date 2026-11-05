import { readFileSync } from "node:fs";
import path from "node:path";
import { env } from "../config/env.js";
import type { DIDDocument } from "did-resolver";

let cache: Record<string, DIDDocument> | null = null;

function load(): Record<string, DIDDocument> {
  if (cache) return cache;
  const filePath = path.join(env.fixturesDir, "dids", "did-documents.json");
  const raw = readFileSync(filePath, "utf8");
  cache = JSON.parse(raw) as Record<string, DIDDocument>;
  return cache;
}

export function getDidDocument(did: string): DIDDocument | undefined {
  return load()[did];
}

/** 테스트에서 매번 새 fixture를 반영하고 싶을 때 캐시를 지운다. */
export function clearDidDocumentCache(): void {
  cache = null;
}

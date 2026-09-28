/**
 * fixture 발급자 3곳의 Ed25519 키쌍과 DID Document를 생성한다.
 * 실행: npm run generate:keys
 * 주의: 실행할 때마다 키가 새로 생성되므로, generate-fixtures.ts도 함께 다시 돌려야 서명이 맞는다.
 * (둘 다 한 번에 하려면 npm run setup:fixtures)
 */
import { mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { env } from "../src/config/env.js";
import { generateFixtureKeyPair } from "../src/crypto/keys.js";

interface IssuerSpec {
  slug: string;
  name: string;
}

interface FixtureDidDocument {
  "@context": string[];
  id: string;
  verificationMethod: Array<{ id: string; type: string; controller: string; publicKeyJwk: Record<string, unknown> }>;
  assertionMethod: string[];
  authentication: string[];
}

const ISSUERS: IssuerSpec[] = [
  { slug: "acme-university", name: "ACME University" },
  { slug: "globex-academy", name: "Globex Academy" },
  { slug: "shady-academy", name: "Shady Academy" }
];

function ensureDir(dir: string): void {
  mkdirSync(dir, { recursive: true });
}

function main(): void {
  const keysDir = path.join(env.fixturesDir, "keys");
  const didsDir = path.join(env.fixturesDir, "dids");
  ensureDir(keysDir);
  ensureDir(didsDir);

  const didDocuments: Record<string, FixtureDidDocument> = {};

  for (const issuer of ISSUERS) {
    const did = `did:fixture:${issuer.slug}`;
    const verificationMethodId = `${did}#key-1`;
    const { publicJwk, privateJwk } = generateFixtureKeyPair();

    writeFileSync(
      path.join(keysDir, `${issuer.slug}.json`),
      JSON.stringify({ did, verificationMethodId, name: issuer.name, publicJwk, privateJwk }, null, 2) + "\n",
      "utf8"
    );

    didDocuments[did] = {
      "@context": ["https://www.w3.org/ns/did/v1"],
      id: did,
      verificationMethod: [
        {
          id: verificationMethodId,
          type: "JsonWebKey2020",
          controller: did,
          publicKeyJwk: publicJwk as unknown as Record<string, unknown>
        }
      ],
      assertionMethod: [verificationMethodId],
      authentication: [verificationMethodId]
    };

    console.log(`[keys] ${did} 키/서명 생성 완료`);
  }

  writeFileSync(path.join(didsDir, "did-documents.json"), JSON.stringify(didDocuments, null, 2) + "\n", "utf8");
  console.log(`[keys] DID Document 저장 완료 -> ${path.join(didsDir, "did-documents.json")}`);
}

main();

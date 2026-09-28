/**
 * generate-fixture-keys.ts가 만든 키로 실제 서명이 들어간 자격증명 fixture 9건(정상 4 + 비정상 5)을 만든다.
 * 실행: npm run generate:fixtures (또는 npm run setup:fixtures 로 키 생성부터 한 번에)
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { env } from "../src/config/env.js";
import { addDataIntegrityProof } from "../src/crypto/dataIntegrity.js";
import { signCompactJws } from "../src/crypto/jws.js";
import { privateKeyFromJwk, type Ed25519JwkPrivate } from "../src/crypto/keys.js";

interface IssuerKeyFile {
  did: string;
  verificationMethodId: string;
  name: string;
  privateJwk: Ed25519JwkPrivate;
}

interface FixtureCredentialFileOut {
  externalId: string;
  provider: string;
  subjectDid: string;
  expectedStatus: string;
  note?: string;
  document?: Record<string, unknown>;
  compactJws?: string;
}

const SUBJECT_DID = "did:fixture:student-001";

function ensureDir(dir: string): void {
  mkdirSync(dir, { recursive: true });
}

function loadIssuerKey(slug: string): IssuerKeyFile {
  const filePath = path.join(env.fixturesDir, "keys", `${slug}.json`);
  return JSON.parse(readFileSync(filePath, "utf8")) as IssuerKeyFile;
}

function ob2Assertion(opts: {
  externalId: string;
  issuer: IssuerKeyFile;
  badgeName: string;
  description: string;
  narrative: string;
  issuedOn: string;
  expires?: string | null;
  alignment?: { targetFramework: string; targetCode: string; targetName: string };
}): Record<string, unknown> {
  return {
    id: `https://wallet-core.local/assertions/${opts.externalId}`,
    type: "Assertion",
    recipient: { identity: SUBJECT_DID, type: "did" },
    badge: {
      id: `https://wallet-core.local/badges/${opts.externalId}`,
      name: opts.badgeName,
      description: opts.description,
      criteria: { narrative: opts.narrative },
      issuer: { id: opts.issuer.did, name: opts.issuer.name, url: `https://${opts.issuer.did.split(":")[2]}.example.org` },
      alignment: opts.alignment ? [opts.alignment] : []
    },
    issuedOn: opts.issuedOn,
    expires: opts.expires ?? null,
    evidence: [{ id: "https://example.org/evidence/portfolio", name: "포트폴리오 저장소" }]
  };
}

function ob3Credential(opts: {
  externalId: string;
  issuer: IssuerKeyFile;
  achievementName: string;
  description: string;
  narrative: string;
  issuanceDate: string;
  expirationDate?: string | null;
  alignment?: { targetFramework: string; targetCode: string; targetName: string };
  omitAchievementName?: boolean;
}): Record<string, unknown> {
  const achievement: Record<string, unknown> = {
    id: `https://wallet-core.local/achievements/${opts.externalId}`,
    description: opts.description,
    criteria: { narrative: opts.narrative },
    alignment: opts.alignment ? [opts.alignment] : []
  };
  if (!opts.omitAchievementName) {
    achievement.name = opts.achievementName;
  }

  return {
    "@context": ["https://www.w3.org/ns/credentials/v2", "https://purl.imsglobal.org/spec/ob/v3p0/context.json"],
    id: `https://wallet-core.local/credentials/${opts.externalId}`,
    type: ["VerifiableCredential", "OpenBadgeCredential"],
    issuer: {
      id: opts.issuer.did,
      type: "Profile",
      name: opts.issuer.name,
      url: `https://${opts.issuer.did.split(":")[2]}.example.org`
    },
    issuanceDate: opts.issuanceDate,
    expirationDate: opts.expirationDate ?? null,
    credentialSubject: { id: SUBJECT_DID, type: "AchievementSubject", achievement },
    evidence: [{ id: "https://example.org/evidence/capstone", name: "캡스톤 프로젝트" }]
  };
}

function tamperSignature(jws: string): string {
  const parts = jws.split(".");
  const sig = parts[2]!;
  const lastChar = sig.charAt(sig.length - 1);
  const replacement = lastChar === "A" ? "B" : "A";
  const tampered = sig.slice(0, -1) + replacement;
  return `${parts[0]}.${parts[1]}.${tampered}`;
}

async function main(): Promise<void> {
  const acme = loadIssuerKey("acme-university");
  const globex = loadIssuerKey("globex-academy");
  const shady = loadIssuerKey("shady-academy");

  const acmePrivateKey = privateKeyFromJwk(acme.privateJwk);
  const globexPrivateKey = privateKeyFromJwk(globex.privateJwk);

  const fixtures: FixtureCredentialFileOut[] = [];

  // 1. OB2.0 hosted (서명 없음, 호스팅 신뢰) - 정상
  fixtures.push({
    externalId: "cred-ob2-hosted-001",
    provider: "dx_ledger_badge",
    subjectDid: SUBJECT_DID,
    expectedStatus: "verified",
    note: "OB2.0 hosted assertion, 정상 케이스 (signature 체크는 skip)",
    document: ob2Assertion({
      externalId: "cred-ob2-hosted-001",
      issuer: acme,
      badgeName: "백엔드 개발 기초",
      description: "백엔드 개발 기초 과정을 이수했습니다.",
      narrative: "REST API 설계 및 구현 과제를 완료해야 합니다.",
      issuedOn: "2026-03-01T00:00:00Z",
      alignment: { targetFramework: "NCS", targetCode: "2001010401", targetName: "백엔드 개발" }
    })
  });

  // 2. OB2.0 signed (compact JWS) - 정상
  const ob2SignedPayload = ob2Assertion({
    externalId: "cred-ob2-signed-002",
    issuer: acme,
    badgeName: "데이터베이스 설계 인증",
    description: "관계형 데이터베이스 설계 역량을 인증합니다.",
    narrative: "정규화 및 인덱스 설계 실습을 완료해야 합니다.",
    issuedOn: "2026-03-15T00:00:00Z",
    alignment: { targetFramework: "NCS", targetCode: "2001010305", targetName: "데이터베이스 설계" }
  });
  fixtures.push({
    externalId: "cred-ob2-signed-002",
    provider: "dx_ledger_badge",
    subjectDid: SUBJECT_DID,
    expectedStatus: "verified",
    note: "OB2.0 signed assertion (compact JWS), 정상 케이스",
    compactJws: signCompactJws(ob2SignedPayload, acmePrivateKey, acme.verificationMethodId)
  });

  // 3. OB3.0 VC-JWT - 정상 (globex-academy, trust=known)
  const ob3VcJwtPayload = ob3Credential({
    externalId: "cred-ob3-vcjwt-003",
    issuer: globex,
    achievementName: "머신러닝 프로젝트 수료",
    description: "머신러닝 프로젝트 과정을 수료했습니다.",
    narrative: "지도학습 모델을 학습·평가하는 프로젝트를 완료해야 합니다.",
    issuanceDate: "2026-04-01T00:00:00Z",
    alignment: { targetFramework: "ESCO", targetCode: "S1.2.1", targetName: "머신러닝" }
  });
  fixtures.push({
    externalId: "cred-ob3-vcjwt-003",
    provider: "dx_ledger_wallet",
    subjectDid: SUBJECT_DID,
    expectedStatus: "verified",
    note: "OB3.0 VC-JWT, 정상 케이스 (발급자 trust_level=known)",
    compactJws: signCompactJws(ob3VcJwtPayload, globexPrivateKey, globex.verificationMethodId)
  });

  // 4. OB3.0 Data Integrity - 정상
  const ob3DiPayload = ob3Credential({
    externalId: "cred-ob3-di-004",
    issuer: acme,
    achievementName: "블록체인 응용 캡스톤",
    description: "블록체인 기반 응용 서비스를 개발했습니다.",
    narrative: "DID/VC 연동 캡스톤 프로젝트를 완료해야 합니다.",
    issuanceDate: "2026-05-01T00:00:00Z",
    alignment: { targetFramework: "NCS", targetCode: "2003020104", targetName: "블록체인 응용" }
  });
  fixtures.push({
    externalId: "cred-ob3-di-004",
    provider: "dx_ledger_wallet",
    subjectDid: SUBJECT_DID,
    expectedStatus: "verified",
    note: "OB3.0 Data Integrity proof, 정상 케이스",
    document: await addDataIntegrityProof(ob3DiPayload, acmePrivateKey, acme.verificationMethodId)
  });

  // 5. OB2.0 signed - 위조 서명 (서명 값 일부 변조)
  const forgedPayload = ob2Assertion({
    externalId: "cred-ob2-signed-forged-005",
    issuer: acme,
    badgeName: "위조 테스트 배지",
    description: "서명 위조 탐지 테스트용 fixture입니다.",
    narrative: "-",
    issuedOn: "2026-03-20T00:00:00Z"
  });
  const forgedJws = signCompactJws(forgedPayload, acmePrivateKey, acme.verificationMethodId);
  fixtures.push({
    externalId: "cred-ob2-signed-forged-005",
    provider: "dx_ledger_badge",
    subjectDid: SUBJECT_DID,
    expectedStatus: "invalid",
    note: "서명을 의도적으로 변조함 (signature 체크 fail -> invalid)",
    compactJws: tamperSignature(forgedJws)
  });

  // 6. OB3.0 Data Integrity - 폐기됨
  const revokedPayload = ob3Credential({
    externalId: "cred-ob3-di-revoked-006",
    issuer: acme,
    achievementName: "폐기된 자격증명 테스트",
    description: "폐기 확인 테스트용 fixture입니다.",
    narrative: "-",
    issuanceDate: "2026-02-01T00:00:00Z"
  });
  fixtures.push({
    externalId: "cred-ob3-di-revoked-006",
    provider: "dx_ledger_wallet",
    subjectDid: SUBJECT_DID,
    expectedStatus: "revoked",
    note: "revocation-list.json에 등록된 폐기 자격증명",
    document: await addDataIntegrityProof(revokedPayload, acmePrivateKey, acme.verificationMethodId)
  });

  // 7. OB3.0 VC-JWT - 만료됨
  const expiredPayload = ob3Credential({
    externalId: "cred-ob3-vcjwt-expired-007",
    issuer: globex,
    achievementName: "만료된 자격증명 테스트",
    description: "만료 확인 테스트용 fixture입니다.",
    narrative: "-",
    issuanceDate: "2019-01-01T00:00:00Z",
    expirationDate: "2020-01-01T00:00:00Z"
  });
  fixtures.push({
    externalId: "cred-ob3-vcjwt-expired-007",
    provider: "dx_ledger_wallet",
    subjectDid: SUBJECT_DID,
    expectedStatus: "expired",
    note: "expirationDate가 과거 (expiration 체크 fail -> expired)",
    compactJws: signCompactJws(expiredPayload, globexPrivateKey, globex.verificationMethodId)
  });

  // 8. OB2.0 hosted - 미상(비신뢰) 발급자
  fixtures.push({
    externalId: "cred-ob2-hosted-untrusted-008",
    provider: "dx_ledger_badge",
    subjectDid: SUBJECT_DID,
    expectedStatus: "unverifiable",
    note: "shady-academy는 trust-registry.json에 없음 (issuer_trust 체크 fail -> unverifiable)",
    document: ob2Assertion({
      externalId: "cred-ob2-hosted-untrusted-008",
      issuer: shady,
      badgeName: "출처 불명 배지",
      description: "신뢰 레지스트리에 없는 발급자가 발급한 배지입니다.",
      narrative: "-",
      issuedOn: "2026-03-25T00:00:00Z"
    })
  });

  // 9. OB3.0 Data Integrity - 스키마 위반 (achievement.name 누락)
  const schemaInvalidPayload = ob3Credential({
    externalId: "cred-ob3-di-schema-invalid-009",
    issuer: acme,
    achievementName: "(생략됨)",
    description: "스키마 검증 테스트용 fixture입니다. achievement.name 필드가 누락되어 있습니다.",
    narrative: "-",
    issuanceDate: "2026-03-30T00:00:00Z",
    omitAchievementName: true
  });
  fixtures.push({
    externalId: "cred-ob3-di-schema-invalid-009",
    provider: "dx_ledger_wallet",
    subjectDid: SUBJECT_DID,
    expectedStatus: "invalid",
    note: "credentialSubject.achievement.name 누락 (schema 체크 fail -> invalid)",
    document: await addDataIntegrityProof(schemaInvalidPayload, acmePrivateKey, acme.verificationMethodId)
  });

  const credentialsDir = path.join(env.fixturesDir, "credentials");
  const registryDir = path.join(env.fixturesDir, "registry");
  ensureDir(credentialsDir);
  ensureDir(registryDir);

  for (const fixture of fixtures) {
    writeFileSync(path.join(credentialsDir, `${fixture.externalId}.json`), JSON.stringify(fixture, null, 2) + "\n", "utf8");
  }
  console.log(`[fixtures] 자격증명 fixture ${fixtures.length}건 생성 완료 -> ${credentialsDir}`);

  writeFileSync(
    path.join(registryDir, "trust-registry.json"),
    JSON.stringify(
      [
        { did: acme.did, name: acme.name, trust_level: "accredited" },
        { did: globex.did, name: globex.name, trust_level: "known" }
      ],
      null,
      2
    ) + "\n",
    "utf8"
  );
  console.log("[fixtures] trust-registry.json 생성 완료 (shady-academy는 의도적으로 미등록)");

  writeFileSync(
    path.join(registryDir, "revocation-list.json"),
    JSON.stringify({ revoked_external_ids: ["cred-ob3-di-revoked-006"] }, null, 2) + "\n",
    "utf8"
  );
  console.log("[fixtures] revocation-list.json 생성 완료");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

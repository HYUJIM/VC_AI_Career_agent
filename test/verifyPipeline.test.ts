import { describe, expect, it } from "vitest";
import { loadFixtureCredentialFiles } from "../src/adapters/fixture/fixtureCredentialSource.js";
import { buildContainer } from "../src/container.js";
import { verifyAndNormalize } from "../src/verify/pipeline.js";
import type { VerificationStatus } from "../src/types/credential.js";

describe("verifyAndNormalize (fixture 9건)", () => {
  const container = buildContainer();

  it("fixture가 최소 9건 존재한다 (정상 4 + 비정상 5)", () => {
    const files = loadFixtureCredentialFiles();
    expect(files.length).toBeGreaterThanOrEqual(9);
  });

  it.each(loadFixtureCredentialFiles())(
    "$externalId -> expectedStatus=$expectedStatus",
    async (fixture) => {
      const ref = {
        externalId: fixture.externalId,
        provider: fixture.provider,
        document: fixture.document,
        compactJws: fixture.compactJws
      };
      const record = await verifyAndNormalize(ref, fixture.provider, {
        issuerTrust: container.issuerTrust,
        revocationChecker: container.revocationChecker,
        anchorVerifier: container.anchorVerifier
      });

      expect(record.verification.status).toBe(fixture.expectedStatus as VerificationStatus);
      // 7개 체크는 항상 전부 기록되어야 한다 (스킵되더라도 배열에서 빠지면 안 됨).
      expect(record.verification.checks).toHaveLength(7);
      // record_id는 실행할 때마다 같아야 한다 (externalId 기반 결정적 UUID).
      expect(record.record_id).toMatch(/^[0-9a-f-]{36}$/);
    }
  );

  it("anchor_match 체크는 논문 기간 동안 항상 skip이다", async () => {
    const [fixture] = loadFixtureCredentialFiles();
    if (!fixture) throw new Error("fixture가 없습니다");
    const ref = { externalId: fixture.externalId, provider: fixture.provider, document: fixture.document, compactJws: fixture.compactJws };
    const record = await verifyAndNormalize(ref, fixture.provider, {
      issuerTrust: container.issuerTrust,
      revocationChecker: container.revocationChecker,
      anchorVerifier: container.anchorVerifier
    });
    const anchorCheck = record.verification.checks.find((c) => c.check === "anchor_match");
    expect(anchorCheck?.result).toBe("skip");
  });

  it("같은 fixture를 두 번 검증해도 raw_hash와 record_id가 동일하다 (결정성)", async () => {
    const [fixture] = loadFixtureCredentialFiles();
    if (!fixture) throw new Error("fixture가 없습니다");
    const ref = { externalId: fixture.externalId, provider: fixture.provider, document: fixture.document, compactJws: fixture.compactJws };
    const deps = {
      issuerTrust: container.issuerTrust,
      revocationChecker: container.revocationChecker,
      anchorVerifier: container.anchorVerifier
    };
    const first = await verifyAndNormalize(ref, fixture.provider, deps);
    const second = await verifyAndNormalize(ref, fixture.provider, deps);
    expect(first.raw_hash).toBe(second.raw_hash);
    expect(first.record_id).toBe(second.record_id);
  });
});

import { beforeAll, describe, expect, it } from "vitest";
import request from "supertest";
import { buildApp } from "../src/server/app.js";
import { buildContainer } from "../src/container.js";
import type { Express } from "express";

const SUBJECT_DID = "did:fixture:student-001";

describe("VC 지갑 코어 Mock API", () => {
  let app: Express;

  beforeAll(async () => {
    const container = buildContainer();
    const built = buildApp(container);
    await built.store.init();
    app = built.app;
  });

  it("GET /health -> ok, 9건 로드", async () => {
    const res = await request(app).get("/health");
    expect(res.status).toBe(200);
    expect(res.body.status).toBe("ok");
    expect(res.body.recordCount).toBeGreaterThanOrEqual(9);
  });

  it("GET /v1/subjects/:did/credentials 는 기본적으로 verified만 반환한다", async () => {
    const res = await request(app).get(`/v1/subjects/${SUBJECT_DID}/credentials`);
    expect(res.status).toBe(200);
    expect(res.body.status_filter).toEqual(["verified"]);
    expect(res.body.count).toBeGreaterThan(0);
    for (const record of res.body.records) {
      expect(record.status).toBe("verified");
    }
  });

  it("GET /v1/subjects/:did/credentials?status=all 은 비검증 데이터도 포함한다", async () => {
    const res = await request(app).get(`/v1/subjects/${SUBJECT_DID}/credentials?status=all`);
    expect(res.status).toBe(200);
    const statuses = new Set(res.body.records.map((r: { status: string }) => r.status));
    expect(statuses.size).toBeGreaterThan(1);
  });

  it("GET /v1/records/:id 는 존재하지 않으면 404를 반환한다", async () => {
    const res = await request(app).get("/v1/records/does-not-exist");
    expect(res.status).toBe(404);
  });

  it("GET /v1/records/:id/verification 은 checks 7개를 반환한다", async () => {
    const listRes = await request(app).get(`/v1/subjects/${SUBJECT_DID}/credentials?status=all`);
    const firstId = listRes.body.records[0].record_id;
    const res = await request(app).get(`/v1/records/${firstId}/verification`);
    expect(res.status).toBe(200);
    expect(res.body.checks).toHaveLength(7);
  });

  it("GET /v1/subjects/:did/skills 는 verified 레코드의 스킬만 반환한다", async () => {
    const res = await request(app).get(`/v1/subjects/${SUBJECT_DID}/skills`);
    expect(res.status).toBe(200);
    expect(Array.isArray(res.body.skills)).toBe(true);
    for (const skill of res.body.skills) {
      expect(skill.source_record_ids.length).toBeGreaterThan(0);
    }
  });

  it("POST /v1/verify 는 existing external_id를 즉시 재검증한다", async () => {
    const res = await request(app).post("/v1/verify").send({ external_id: "cred-ob2-hosted-001" });
    expect(res.status).toBe(200);
    expect(res.body.verification.status).toBe("verified");
  });

  it("POST /v1/verify 는 없는 external_id에 404를 반환한다", async () => {
    const res = await request(app).post("/v1/verify").send({ external_id: "no-such-id" });
    expect(res.status).toBe(404);
  });

  it("GET /openapi.json 은 구현된 엔드포인트를 모두 문서화한다", async () => {
    const res = await request(app).get("/openapi.json");
    expect(res.status).toBe(200);
    expect(res.body.paths).toHaveProperty("/v1/subjects/{did}/credentials");
    expect(res.body.paths).toHaveProperty("/v1/verify");
  });

  it("GET /docs 는 Swagger UI HTML을 반환한다", async () => {
    const res = await request(app).get("/docs/");
    expect(res.status).toBe(200);
    expect(res.text).toContain("swagger-ui");
  });
});

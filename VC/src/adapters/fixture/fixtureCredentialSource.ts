import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { env } from "../../config/env.js";
import type { CredentialProvider } from "../../types/credential.js";
import type { CredentialSourcePort, RawCredentialRef } from "../ports.js";

export interface FixtureCredentialFile {
  externalId: string;
  provider: CredentialProvider;
  subjectDid: string;
  /** 이 fixture를 검증 파이프라인에 돌렸을 때 기대하는 최종 status. 테스트/검증 스크립트가 사용한다. */
  expectedStatus: string;
  /** 이 fixture가 어떤 케이스를 재현하는지 사람이 읽기 위한 설명 (선택) */
  note?: string;
  /** JSON/JSON-LD 형태 원본 (hosted OB2.0, Data Integrity OB3.0) */
  document?: Record<string, unknown>;
  /** Compact JWS 형태 원본 (signed OB2.0, VC-JWT OB3.0) */
  compactJws?: string;
}

let cache: FixtureCredentialFile[] | null = null;

function credentialsDir(): string {
  return path.join(env.fixturesDir, "credentials");
}

function loadAll(): FixtureCredentialFile[] {
  if (cache) return cache;
  const dir = credentialsDir();
  const files = readdirSync(dir).filter((f) => f.endsWith(".json"));
  cache = files.map((file) => {
    const raw = readFileSync(path.join(dir, file), "utf8");
    return JSON.parse(raw) as FixtureCredentialFile;
  });
  return cache;
}

export function clearFixtureCredentialCache(): void {
  cache = null;
}

function toRawRef(entry: FixtureCredentialFile): RawCredentialRef {
  return {
    externalId: entry.externalId,
    provider: entry.provider,
    document: entry.document,
    compactJws: entry.compactJws
  };
}

export class FixtureCredentialSource implements CredentialSourcePort {
  async listBySubject(subjectDid: string): Promise<RawCredentialRef[]> {
    return loadAll()
      .filter((entry) => entry.subjectDid === subjectDid)
      .map(toRawRef);
  }

  async getByExternalId(externalId: string): Promise<RawCredentialRef | undefined> {
    const found = loadAll().find((entry) => entry.externalId === externalId);
    return found ? toRawRef(found) : undefined;
  }

  async listAll(): Promise<RawCredentialRef[]> {
    return loadAll().map(toRawRef);
  }
}

/** 테스트/검증 스크립트 전용: expectedStatus 등 fixture 메타데이터까지 그대로 노출 */
export function loadFixtureCredentialFiles(): FixtureCredentialFile[] {
  return loadAll();
}

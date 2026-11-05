import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parse } from "yaml";

const currentDir = path.dirname(fileURLToPath(import.meta.url));

/**
 * docs/openapi.yaml을 파싱해서 반환한다. BE/AI 팀은 /openapi.json으로 이 결과를 그대로 받아
 * 코드 생성기(openapi-generator, orval 등)에 넣을 수 있고, /docs에서는 Swagger UI로 바로 탐색할 수 있다.
 */
export function loadOpenApiDocument(): Record<string, unknown> {
  const filePath = path.join(currentDir, "../../docs/openapi.yaml");
  const raw = readFileSync(filePath, "utf8");
  return parse(raw) as Record<string, unknown>;
}

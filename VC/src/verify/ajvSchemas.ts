import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
// 우리 스키마들이 "$schema": ".../draft/2020-12/schema"를 쓰므로, 이 draft를 아는 Ajv2020 빌드가 필요하다.
// 일반 "ajv" 패키지의 기본 Ajv는 draft-07까지만 알고 2020-12를 모른다.
import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

const currentDir = path.dirname(fileURLToPath(import.meta.url));

const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);

function loadSchema(relativePath: string): object {
  const fullPath = path.join(currentDir, relativePath);
  return JSON.parse(readFileSync(fullPath, "utf8"));
}

export const validateOb2: ValidateFunction = ajv.compile(loadSchema("../schemas/raw/ob2-assertion.schema.json"));
export const validateOb3: ValidateFunction = ajv.compile(loadSchema("../schemas/raw/ob3-credential.schema.json"));

export function formatAjvErrors(errors: ErrorObject[] | null | undefined): string {
  if (!errors || errors.length === 0) return "스키마 오류";
  return errors.map((e) => `${e.instancePath || "(root)"} ${e.message ?? ""}`).join("; ");
}

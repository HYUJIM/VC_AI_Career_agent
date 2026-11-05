import type { RawCredentialRef } from "../adapters/ports.js";
import type { ParsedCredential } from "../types/credential.js";
import { detectFormat } from "./detectFormat.js";
import { parseOb2 } from "./ob2Parser.js";
import { parseOb3 } from "./ob3Parser.js";

export function parseCredential(ref: RawCredentialRef): ParsedCredential {
  const format = detectFormat(ref);
  switch (format) {
    case "OB2.0":
      return parseOb2(ref);
    case "OB3.0":
      return parseOb3(ref);
    default:
      throw new Error(`아직 지원하지 않는 포맷입니다: ${format}`);
  }
}

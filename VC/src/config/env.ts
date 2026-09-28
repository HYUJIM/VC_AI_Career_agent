import "dotenv/config";
import path from "node:path";

function readEnv(key: string, fallback: string): string {
  const value = process.env[key];
  return value === undefined || value === "" ? fallback : value;
}

export const env = {
  port: Number(readEnv("PORT", "4000")),
  sourceMode: readEnv("VC_SOURCE_MODE", "fixture") as "fixture" | "dxledger",
  fixturesDir: path.resolve(process.cwd(), readEnv("FIXTURES_DIR", "./fixtures")),
  corsOrigin: readEnv("CORS_ORIGIN", "http://localhost:3000").split(",").map((s) => s.trim()),
  logLevel: readEnv("LOG_LEVEL", "info")
};

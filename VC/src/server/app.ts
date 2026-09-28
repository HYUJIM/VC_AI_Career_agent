import cors from "cors";
import express, { type Express } from "express";
import swaggerUi from "swagger-ui-express";
import { env } from "../config/env.js";
import type { Container } from "../container.js";
import { loadOpenApiDocument } from "./openapi.js";
import { credentialsRouter } from "./routes/credentials.js";
import { healthRouter } from "./routes/health.js";
import { skillsRouter } from "./routes/skills.js";
import { verifyRouter } from "./routes/verify.js";
import { WalletStore } from "./walletStore.js";

export interface BuiltApp {
  app: Express;
  store: WalletStore;
}

export function buildApp(container: Container): BuiltApp {
  const app = express();
  const store = new WalletStore(container);

  app.use(cors({ origin: env.corsOrigin }));
  app.use(express.json());

  // BE/AI 팀이 실제 구현과 명세가 어긋나는지 바로 확인할 수 있도록,
  // docs/openapi.yaml을 그대로 로드해 /docs(Swagger UI)와 /openapi.json(원시 스펙)으로 노출한다.
  const openApiDocument = loadOpenApiDocument();
  app.get("/openapi.json", (_req, res) => res.json(openApiDocument));
  app.use("/docs", swaggerUi.serve, swaggerUi.setup(openApiDocument));

  app.use(healthRouter(store));
  app.use("/v1", credentialsRouter(store));
  app.use("/v1", skillsRouter(store));
  app.use("/v1", verifyRouter(container));

  app.use((_req, res) => {
    res.status(404).json({ error: "not_found", message: "일치하는 라우트가 없습니다" });
  });

  return { app, store };
}

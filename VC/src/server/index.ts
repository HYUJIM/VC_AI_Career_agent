import { env } from "../config/env.js";
import { buildContainer } from "../container.js";
import { buildApp } from "./app.js";

async function main(): Promise<void> {
  const container = buildContainer();
  const { app, store } = buildApp(container);

  console.log("[vc-mock-server] fixture 자격증명 검증 중...");
  await store.init();
  console.log(`[vc-mock-server] ${store.listAll().length}건 로드 완료 (mode=${env.sourceMode})`);

  app.listen(env.port, () => {
    console.log(`[vc-mock-server] http://localhost:${env.port} 에서 대기 중`);
    console.log(`[vc-mock-server] 헬스체크: GET http://localhost:${env.port}/health`);
  });
}

main().catch((err) => {
  console.error("[vc-mock-server] 시작 실패:", err);
  process.exit(1);
});

import { Router } from "express";
import type { Container } from "../../container.js";
import { verifyAndNormalize } from "../../verify/pipeline.js";

/**
 * 캐시된 결과가 아니라 그 자리에서 파이프라인을 다시 돌려 보여주는 데모/디버그용 엔드포인트.
 * MCP `wallet.verify_credential` 툴이 감쌀 대상이기도 하다.
 */
export function verifyRouter(container: Container): Router {
  const router = Router();

  router.post("/verify", async (req, res) => {
    const externalId = req.body?.external_id;
    if (typeof externalId !== "string" || externalId.length === 0) {
      res.status(400).json({ error: "bad_request", message: "body.external_id(string)가 필요합니다" });
      return;
    }

    const ref = await container.credentialSource.getByExternalId(externalId);
    if (!ref) {
      res.status(404).json({ error: "not_found", message: `external_id를 찾을 수 없습니다: ${externalId}` });
      return;
    }

    try {
      const record = await verifyAndNormalize(ref, ref.provider, {
        issuerTrust: container.issuerTrust,
        revocationChecker: container.revocationChecker,
        anchorVerifier: container.anchorVerifier
      });
      res.json(record);
    } catch (err) {
      res.status(422).json({ error: "verification_failed", message: (err as Error).message });
    }
  });

  return router;
}

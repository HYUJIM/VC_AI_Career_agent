import { Router } from "express";
import type { WalletStore } from "../walletStore.js";

export function healthRouter(store: WalletStore): Router {
  const router = Router();

  router.get("/health", (_req, res) => {
    res.json({
      status: store.isReady() ? "ok" : "starting",
      mode: "fixture",
      recordCount: store.listAll().length
    });
  });

  return router;
}

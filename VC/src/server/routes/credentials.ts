import { Router } from "express";
import type { WalletStore } from "../walletStore.js";
import type { CredentialRecord, VerificationStatus } from "../../types/credential.js";

const ALL_STATUSES: VerificationStatus[] = ["verified", "invalid", "revoked", "expired", "unverifiable", "pending"];

/**
 * "근거 없는 스펙 기재 원천 차단" 원칙을 API 기본값으로 강제하는 지점.
 * ?status 파라미터를 생략하면 항상 verified만 내려간다. 미검증 데이터를 받으려면
 * ?status=all 또는 ?status=invalid,revoked 처럼 명시적으로 요청해야 한다.
 */
function resolveStatusFilter(statusParam: unknown): VerificationStatus[] {
  if (typeof statusParam !== "string" || statusParam.length === 0) {
    return ["verified"];
  }
  if (statusParam === "all") {
    return ALL_STATUSES;
  }
  const requested = statusParam.split(",").map((s) => s.trim());
  return requested.filter((s): s is VerificationStatus => (ALL_STATUSES as string[]).includes(s));
}

function toListItem(record: CredentialRecord) {
  return {
    record_id: record.record_id,
    format: record.format,
    achievement_name: record.achievement.name,
    issuer_name: record.issuer.name,
    status: record.verification.status,
    issued_at: record.issued_at
  };
}

export function credentialsRouter(store: WalletStore): Router {
  const router = Router();

  router.get("/subjects/:did/credentials", (req, res) => {
    const statuses = resolveStatusFilter(req.query.status);
    const records = store.listBySubject(req.params.did).filter((r) => statuses.includes(r.verification.status));
    res.json({ subject_did: req.params.did, status_filter: statuses, count: records.length, records: records.map(toListItem) });
  });

  router.get("/records/:id", (req, res) => {
    const record = store.getById(req.params.id);
    if (!record) {
      res.status(404).json({ error: "not_found", message: `record_id를 찾을 수 없습니다: ${req.params.id}` });
      return;
    }
    res.json(record);
  });

  router.get("/records/:id/verification", (req, res) => {
    const record = store.getById(req.params.id);
    if (!record) {
      res.status(404).json({ error: "not_found", message: `record_id를 찾을 수 없습니다: ${req.params.id}` });
      return;
    }
    res.json(record.verification);
  });

  return router;
}

import { Router } from "express";
import type { WalletStore } from "../walletStore.js";
import type { SkillClaim } from "../../types/credential.js";

function dedupeBySkillId(claims: SkillClaim[]): SkillClaim[] {
  const bySkillId = new Map<string, SkillClaim>();
  for (const claim of claims) {
    const existing = bySkillId.get(claim.skill_id);
    if (!existing) {
      bySkillId.set(claim.skill_id, { ...claim, source_record_ids: [...claim.source_record_ids] });
      continue;
    }
    // 같은 스킬이 여러 자격증명에서 나오면 근거(source_record_ids)를 합치고 confidence는 더 높은 쪽을 채택한다.
    existing.source_record_ids.push(...claim.source_record_ids);
    if (claim.confidence > existing.confidence) {
      existing.confidence = claim.confidence;
      existing.mapping_method = claim.mapping_method;
    }
  }
  return [...bySkillId.values()];
}

export function skillsRouter(store: WalletStore): Router {
  const router = Router();

  // AI 커리어 플래너 팀 계약: GET /v1/subjects/{did}/skills -> 정규화된 SkillClaim[]
  // verified 상태 레코드에서 나온 스킬만 집계한다 (근거 없는 스펙 기재 차단 원칙 적용).
  router.get("/subjects/:did/skills", (req, res) => {
    const verifiedRecords = store.listBySubject(req.params.did).filter((r) => r.verification.status === "verified");
    const skills = dedupeBySkillId(verifiedRecords.flatMap((r) => r.skills));
    res.json({ subject_did: req.params.did, count: skills.length, skills });
  });

  return router;
}

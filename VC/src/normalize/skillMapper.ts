import { readFileSync } from "node:fs";
import path from "node:path";
import { env } from "../config/env.js";
import type { Alignment, SkillClaim, SkillTaxonomyRef } from "../types/credential.js";

interface AliasDictEntry {
  match_code: string;
  skill_id: string;
  label_ko: string;
  label_en: string;
  taxonomy: SkillTaxonomyRef;
}

let cache: AliasDictEntry[] | null = null;

function loadAliasDict(): AliasDictEntry[] {
  if (cache) return cache;
  const filePath = path.join(env.fixturesDir, "registry", "skill-alias-dict.json");
  cache = JSON.parse(readFileSync(filePath, "utf8")) as AliasDictEntry[];
  return cache;
}

export function clearAliasDictCache(): void {
  cache = null;
}

/**
 * 매핑 3단계 폴백 중 1~2단계(정확 매칭 + 별칭 사전)만 구현한다.
 * 3단계(embedding_knn)는 AI 파트가 같은 SkillClaim 형태로 얹는 책임을 진다 (계획 문서 참고).
 */
export function mapAlignmentsToSkillClaims(alignments: Alignment[], sourceRecordId: string): SkillClaim[] {
  const dict = loadAliasDict();
  const claims: SkillClaim[] = [];

  for (const alignment of alignments) {
    if (!alignment.framework || !alignment.code) continue;
    const key = `${alignment.framework}-${alignment.code}`;
    const entry = dict.find((d) => d.match_code === key);
    if (!entry) continue; // 별칭 사전에 없으면 이번 단계에서는 드롭한다 (AI 파트의 embedding_knn 대상).

    claims.push({
      skill_id: entry.skill_id,
      label_ko: entry.label_ko,
      label_en: entry.label_en,
      taxonomy: entry.taxonomy,
      level: null,
      level_basis: "completion",
      confidence: 1,
      mapping_method: "alias_dict",
      source_record_ids: [sourceRecordId]
    });
  }

  return claims;
}

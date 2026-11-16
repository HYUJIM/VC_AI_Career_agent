/** 직무별 채용 시장 통계 (BE GET /api/v1/market/snapshots). 공고 원문은 오지 않는다. */
export type MarketRoleId =
  | 'bigdata_engineer'
  | 'privacy_specialist'
  | 'backend_developer'
  | 'frontend_developer'
  | 'infra_engineer';

export type MarketSkillCategory =
  | 'language'
  | 'framework'
  | 'database'
  | 'data'
  | 'cloud'
  | 'devops'
  | 'security'
  | 'privacy'
  | 'certification'
  | 'practice';

export interface MarketSkillStatistic {
  rank: number;
  skill_id: string;
  label: string;
  category: MarketSkillCategory;
  /** 이 기술을 언급한 공고 수 (공고당 1회) */
  mentioned_postings: number;
  required_postings: number;
  stack_postings: number;
  preferred_postings: number;
  /** 0~1 */
  mention_rate: number;
  mention_rate_low: number;
  mention_rate_high: number;
  required_ratio: number;
  /** 0~1. 자격요건 > 기술스택 > 우대사항 > 업무 언급 순으로 가중한 점수 */
  importance: number;
}

export interface MarketSnapshotSummary {
  role_id: MarketRoleId;
  role_label: string;
  as_of: string;
  sample_size: number;
  companies: number;
  warnings: string[];
  top_skills: MarketSkillStatistic[];
}

export interface MarketSnapshot {
  role_id: MarketRoleId;
  role_label: string;
  catalog_version: string;
  extractor_version: string;
  as_of: string;
  sample_size: number;
  duplicates_removed: number;
  companies: number;
  sources: { source: string; postings: number }[];
  experience: { bucket: 'entry' | 'junior' | 'mid' | 'senior' | 'unspecified'; postings: number }[];
  skills: MarketSkillStatistic[];
  pairs: { skill_a: string; skill_b: string; postings: number; lift: number }[];
  warnings: string[];
  content_hash: string;
}

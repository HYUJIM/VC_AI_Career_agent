/**
 * 직무 비교·커리큘럼 API 타입 (BE /api/v1/job-postings, /career-analyses, /curricula).
 * 원본 정의: BE/app/ai_contract (AI 서비스와 공유하는 형식). 화면에서 쓰는 필드만 옮겼다.
 */

export type SkillId =
  | 'sql'
  | 'python'
  | 'scala'
  | 'spark'
  | 'kafka'
  | 'airflow'
  | 'etl'
  | 'data_modeling'
  | 'git'
  | 'linux'
  | 'aws'
  | 'docker';

export type EvidenceType = 'alignment' | 'completion' | 'assessment' | 'project' | 'experience';

export interface ApiEnvelope<T> {
  success: boolean;
  message?: string;
  data: T;
}

export interface JobReference {
  posting_id: string;
  version_id: string;
  source_url: string;
  company: string;
  title: string;
  posted_at: string | null;
  captured_at: string;
  closes_at: string | null;
  is_target: boolean;
}

export interface DemandStatistic {
  skill_id: SkillId;
  mentioned_postings: number;
  required_postings: number;
  preferred_postings: number;
  denominator: number;
}

export interface JobSearchResult {
  as_of: string;
  lookback_days: 30 | 90;
  posting_count: number;
  duplicate_posting_count: number;
  sources: JobReference[];
  market_demand: DemandStatistic[];
  warnings: string[];
}

export interface Requirement {
  key: string;
  kind: 'skill' | 'experience' | 'education' | 'certification' | 'portfolio' | 'other';
  importance: 'required' | 'preferred';
  quote: string;
  skill_ids: SkillId[];
  match: 'all' | 'any' | 'unspecified';
  minimum_evidence: EvidenceType;
}

export type ComparisonStatus = 'supported' | 'partially_supported' | 'not_evidenced' | 'unknown';

export interface RequirementComparison {
  posting_id: string;
  version_id: string;
  requirement: Requirement;
  status: ComparisonStatus;
  reason_code: string;
  evidence_ids: string[];
  priority: 'high' | 'medium' | 'none';
  next_action: 'none' | 'confirm_or_provide_evidence' | 'manual_assessment';
}

export interface SkillEvidence {
  evidence_id: string;
  skill_id: SkillId;
  record_id: string;
  evidence_type: EvidenceType;
  source_fragments: { pointer: string; quote: string }[];
}

export interface CareerAnalysis {
  analysis_id: string;
  as_of: string;
  valid_until: string;
  comparison_scope: 'target_posting' | 'market_postings';
  market_posting_count: number;
  lookback_days: 30 | 90;
  sources: JobReference[];
  skill_evidence: SkillEvidence[];
  comparisons: RequirementComparison[];
  market_demand: DemandStatistic[];
  unmapped_record_ids: string[];
  excluded_record_ids: string[];
  warnings: string[];
}

export interface LearningChoice {
  skill_id: SkillId;
  action: 'learn' | 'collect_evidence' | 'assess';
}

export interface AlternativeChoice {
  comparison_ref: string;
  skill_id: SkillId;
}

export interface CurriculumRequest {
  analysis_id: string;
  idempotency_key: string;
  weeks: number;
  hours_per_week: number;
  budget_krw?: number | null;
  mode: 'template' | 'llm';
  allow_external_processing: boolean;
  learning_choices: LearningChoice[];
  alternative_choices: AlternativeChoice[];
}

export interface LearningResourceVersion {
  resource_id: string;
  version_id: string;
  resource: {
    source_url: string;
    title: string;
    provider: string;
    level: string;
    minimum_study_hours: number;
    cost_krw: number | null;
  };
}

export interface CurriculumStep {
  step_id: string;
  skill_id: SkillId;
  kind: 'self_assessment' | 'practice' | 'collect_evidence';
  depends_on: string[];
  conditional: boolean;
  hours: number;
  purpose: 'gap_followup' | 'resource_prerequisite';
  allocations: { week: number; hours: number }[];
  goal: string;
  activity: string;
  completion_criterion: string;
  resource: LearningResourceVersion | null;
}

export interface PlanningQuestion {
  comparison_ref: string;
  code: string;
  skill_options: SkillId[];
}

export interface CurriculumResult {
  job_id: string;
  analysis_id: string;
  generation_method: 'template' | 'llm' | 'llm_fallback';
  model: string | null;
  status: 'ready' | 'partial';
  steps: CurriculumStep[];
  total_hours: number;
  known_cost_krw: number;
  has_unknown_costs: boolean;
  questions: PlanningQuestion[];
  warnings: string[];
  validation_errors: string[];
}

export interface CurriculumView {
  curriculum_id: string;
  result: CurriculumResult;
  created_at: string;
}

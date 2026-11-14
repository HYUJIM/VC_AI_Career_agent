import { apiClient } from '@/lib/apiClient';
import type {
  ApiEnvelope,
  CareerAnalysis,
  CurriculumRequest,
  CurriculumView,
  JobSearchResult,
  SkillId,
} from '@/types/career';

export interface SkillCatalog {
  catalog_version: string;
  skills: { skill_id: SkillId; label: string; aliases: string[] }[];
}

/** GET /api/v1/skills — AI 서비스의 기술 카탈로그 (화면 표시용 이름) */
export async function getSkillCatalog(): Promise<SkillCatalog> {
  const { data } = await apiClient.get<ApiEnvelope<SkillCatalog>>('/v1/skills');
  return data.data;
}

/** GET /api/v1/job-postings — 분석에 쓸 수 있는 검토된 최근 공고 (로그인 필요) */
export async function getJobPostings(): Promise<JobSearchResult> {
  const { data } = await apiClient.get<ApiEnvelope<JobSearchResult>>('/v1/job-postings');
  return data.data;
}

/**
 * POST /api/v1/career-analyses
 * BE가 모든 자격증명을 지금 다시 검증한 뒤 통과한 것만 근거로 AI가 공고 요구사항과 비교한다.
 */
export async function createCareerAnalysis(targetPostingId: string | null): Promise<CareerAnalysis> {
  const { data } = await apiClient.post<ApiEnvelope<CareerAnalysis>>(
    '/v1/career-analyses',
    targetPostingId ? { target_posting_id: targetPostingId } : {},
  );
  return data.data;
}

/** POST /api/v1/curricula — 분석 결과(5분 이내)로 학습 계획을 만든다. 템플릿은 즉시 응답 */
export async function createCurriculum(request: CurriculumRequest): Promise<CurriculumView> {
  const { data } = await apiClient.post<ApiEnvelope<CurriculumView>>('/v1/curricula', request, {
    // LLM 모드는 AI 생성에 최대 90초가 걸릴 수 있다.
    timeout: request.mode === 'llm' ? 120_000 : 30_000,
  });
  return data.data;
}

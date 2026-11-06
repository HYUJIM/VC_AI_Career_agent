import { useMutation, useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { createCareerAnalysis, createCurriculum, getJobPostings, getSkillCatalog } from '@/api/career';
import { Alert } from '@/components/ui/Alert';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { getApiErrorKind, getApiErrorMessage } from '@/lib/apiClient';
import { isPreviewSession } from '@/lib/preview';
import { useAuthStore } from '@/store/authStore';
import type {
  CareerAnalysis,
  ComparisonStatus,
  CurriculumView,
  LearningChoice,
  RequirementComparison,
  SkillId,
} from '@/types/career';

const STATUS_LABEL: Record<ComparisonStatus, { label: string; className: string }> = {
  supported: { label: '근거 있음', className: 'bg-[#e9f8f1] text-[#029359]' },
  partially_supported: { label: '일부 근거', className: 'bg-[#fff6e6] text-[#c77700]' },
  not_evidenced: { label: '근거 없음', className: 'bg-[var(--color-primary-weak)] text-[var(--color-primary)]' },
  unknown: { label: '판단 보류', className: 'bg-[var(--color-surface)] text-[var(--color-text-sub)]' },
};

/** BE·AI가 주는 사유/경고 코드 → 사용자 문장. 모르는 코드는 그대로 보여준다. */
const CODE_TEXT: Record<string, string> = {
  EVIDENCE_SUPPORTS_THIS_ANNOTATED_SCOPE_ONLY: '검증된 자격증명이 이 요구사항을 뒷받침해요. (요구 범위 안에서만)',
  EVIDENCE_SCOPE_OR_ALTERNATIVES_INCOMPLETE: '관련 근거는 있지만 요구 수준(예: 실무 경험)이나 조건을 다 채우지는 못해요.',
  NO_EVIDENCE_NOT_A_PROOF_OF_NO_SKILL: '검증된 근거가 아직 없어요. 역량이 없다는 뜻은 아니에요.',
  EXCLUDED_CREDENTIALS_MAY_CONTAIN_EVIDENCE: '검증을 통과하지 못한 자격증명이 있어 판단을 보류했어요.',
  NON_SKILL_OR_DURATION_REQUIRES_MANUAL_ASSESSMENT: '경력 기간·학력 같은 조건은 직접 확인이 필요해요.',
  AMBIGUOUS_REQUIREMENT_LOGIC: '요구 조건이 "그리고/또는" 중 무엇인지 모호해 판단을 보류했어요.',
  LOCAL_REVIEWED_CORPUS_NOT_THE_WHOLE_MARKET: '연구자가 검토한 일부 공고 기준이며 채용 시장 전체가 아니에요.',
  MANUAL_CAPTURE_NOT_A_LIVE_CRAWL: '공고 내용은 연구자가 수집한 시점 기준이에요.',
  MENTION_FREQUENCY_IS_NOT_A_UNIVERSAL_MANDATORY_REQUIREMENT: '자주 언급된 기술이라도 모든 공고의 필수 조건은 아니에요.',
  LOOKBACK_EXPANDED_FROM_30_TO_90_DAYS: '최근 30일 공고가 적어 90일까지 넓혀 봤어요.',
  SMALL_OR_EMPTY_MARKET_SAMPLE: '비교에 쓴 공고 수가 적어요.',
  DUPLICATE_SOURCES_REMOVED: '중복 공고는 한 번만 셌어요.',
  TARGET_POSTING_DATE_UNKNOWN_NOT_COUNTED_AS_RECENT: '게시일을 모르는 공고라 최근 공고 통계에는 넣지 않았어요.',
  COMPARISON_IS_TARGET_SPECIFIC_MARKET_STATS_ARE_CONTEXT_ONLY: '선택한 공고 기준 비교예요. 다른 공고 통계는 참고용이에요.',
  EXCLUDED_CREDENTIALS_LIMIT_PROFILE_COVERAGE: '검증을 통과하지 못한 자격증명은 근거에서 뺐어요.',
  UNMAPPED_CREDENTIALS_REQUIRE_CURATED_RULE_REVIEW: '일부 자격증명은 아직 기술 매핑 규칙이 없어 근거로 쓰지 못했어요.',
  GENERAL_EXERCISE_NO_REVIEWED_COURSE: '검토된 강의가 없는 항목은 특정 강의가 아닌 일반 실습 과제로 제안했어요.',
  CONFIRM_EXPERIENCE_BEFORE_CONDITIONAL_LEARNING: '"경험 확인" 단계 뒤의 학습은 필요할 때만 진행하세요.',
  LEARNING_PROJECTS_DO_NOT_PROVE_PROFESSIONAL_EXPERIENCE: '학습 프로젝트는 실무 경력을 증명하지 않아요.',
  STUDY_HOURS_ARE_PLANNING_ESTIMATES: '학습 시간은 계획용 추정치예요.',
  SOURCE_PRICES_AND_AVAILABILITY_REQUIRE_RECHECK_BEFORE_ENROLLMENT: '수강 전에 가격과 모집 여부를 다시 확인하세요.',
  RESOURCE_COSTS_PARTLY_UNKNOWN: '일부 자료는 비용을 알 수 없어요.',
  UNTRUSTED_MODEL_OUTPUT_DISCARDED: 'LLM 초안이 검사를 통과하지 못해 기본 계획으로 대체했어요.',
  NO_ACTIONABLE_LEARNING_GAPS_IN_THIS_ANALYSIS: '이 비교 결과에서는 보완할 학습 항목이 없어요.',
  SELECT_ONE_ALTERNATIVE: '"또는" 조건이에요. 하나를 골라야 계획에 넣을 수 있어요.',
  MANUAL_ASSESSMENT_REQUIRED: '직접 확인이 필요한 조건이라 계획에 넣지 않았어요.',
  TIME_BUDGET_EXCEEDED: '시간 예산이 부족해 이 항목은 계획에서 뺐어요.',
  COST_BUDGET_EXCEEDED: '비용 예산을 넘어 이 항목은 계획에서 뺐어요.',
  UNKNOWN_PRICE_WITH_STRICT_BUDGET: '예산을 정하면 가격을 모르는 자료는 쓰지 않아요.',
  CYCLIC_RESOURCE_PREREQUISITES: '자료의 선수 조건이 서로 얽혀 있어 계획에 넣지 못했어요.',
};

const ACTION_LABEL: Record<LearningChoice['action'], string> = {
  assess: '경험 먼저 확인',
  learn: '바로 학습',
  collect_evidence: '기존 증빙 정리',
};

const text = (code: string) => CODE_TEXT[code] ?? code;
const refOf = (c: RequirementComparison) => `${c.version_id}:${c.requirement.key}`;
const formatTime = (iso: string) =>
  new Date(iso).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });

/** "또는" 조건 중 아직 근거가 없어 하나를 골라야 하는 요구사항 */
function alternativeComparisons(analysis: CareerAnalysis): RequirementComparison[] {
  return analysis.comparisons.filter(
    (c) =>
      c.requirement.kind === 'skill' &&
      c.requirement.match === 'any' &&
      c.requirement.skill_ids.length > 1 &&
      (c.status === 'partially_supported' || c.status === 'not_evidenced'),
  );
}

/** 계획에 들어갈 보완 기술 (AI 계획기와 같은 규칙: 요구 수준의 근거가 없는 기술) */
function gapSkills(analysis: CareerAnalysis, alternatives: Record<string, SkillId>): SkillId[] {
  const gaps = new Set<SkillId>();
  for (const c of analysis.comparisons) {
    const r = c.requirement;
    if (r.kind !== 'skill' || c.status === 'supported' || c.status === 'unknown') continue;
    let selected = r.skill_ids;
    if (r.match === 'any' && selected.length > 1) {
      const choice = alternatives[refOf(c)];
      if (!choice) continue;
      selected = [choice];
    }
    for (const skill of selected) {
      const covered = analysis.skill_evidence.some(
        (e) =>
          e.skill_id === skill &&
          (e.evidence_type === r.minimum_evidence || r.minimum_evidence === 'alignment'),
      );
      if (!covered) gaps.add(skill);
    }
  }
  return [...gaps].sort();
}

function ErrorAlert({ error, title }: { error: unknown; title: string }) {
  const logout = useAuthStore((state) => state.logout);
  const navigate = useNavigate();
  const unauthorized = getApiErrorKind(error) === 'unauthorized';
  return (
    <Alert
      title={title}
      actions={
        unauthorized ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              logout('sessionExpired');
              navigate('/login');
            }}
          >
            다시 로그인
          </Button>
        ) : undefined
      }
    >
      {getApiErrorMessage(error)}
    </Alert>
  );
}

function WarningList({ codes }: { codes: string[] }) {
  if (codes.length === 0) return null;
  return (
    <ul className="flex list-disc flex-col gap-1 pl-5 text-[13px] text-[var(--color-text-sub)]">
      {codes.map((code) => (
        <li key={code}>{text(code)}</li>
      ))}
    </ul>
  );
}

export function CareerPlanPage() {
  const preview = isPreviewSession();
  const [target, setTarget] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<CareerAnalysis | null>(null);
  const [alternatives, setAlternatives] = useState<Record<string, SkillId>>({});
  const [actions, setActions] = useState<Record<string, LearningChoice['action']>>({});
  const [weeks, setWeeks] = useState(8);
  const [hoursPerWeek, setHoursPerWeek] = useState(6);
  const [budget, setBudget] = useState('');
  const [useLlm, setUseLlm] = useState(false);
  const [plan, setPlan] = useState<CurriculumView | null>(null);

  const postings = useQuery({ queryKey: ['job-postings'], queryFn: getJobPostings, enabled: !preview });
  const catalog = useQuery({ queryKey: ['skills'], queryFn: getSkillCatalog, enabled: !preview });
  const labels = useMemo(
    () => Object.fromEntries((catalog.data?.skills ?? []).map((s) => [s.skill_id, s.label])),
    [catalog.data],
  );
  const label = (skill: SkillId) => labels[skill] ?? skill;

  const analyze = useMutation({
    mutationFn: () => createCareerAnalysis(target),
    onSuccess: (result) => {
      setAnalysis(result);
      setAlternatives({});
      setActions({});
      setPlan(null);
    },
  });

  const generate = useMutation({
    mutationFn: () => {
      const gaps = analysis ? gapSkills(analysis, alternatives) : [];
      return createCurriculum({
        analysis_id: analysis!.analysis_id,
        idempotency_key: crypto.randomUUID(),
        weeks,
        hours_per_week: hoursPerWeek,
        budget_krw: budget.trim() === '' ? null : Number(budget),
        mode: useLlm ? 'llm' : 'template',
        allow_external_processing: useLlm,
        alternative_choices: Object.entries(alternatives).map(([comparison_ref, skill_id]) => ({
          comparison_ref,
          skill_id,
        })),
        learning_choices: gaps
          .filter((skill) => actions[skill] && actions[skill] !== 'assess')
          .map((skill) => ({ skill_id: skill, action: actions[skill] })),
      });
    },
    onSuccess: setPlan,
  });

  if (preview) {
    return (
      <Alert variant="info" title="미리보기 세션에서는 사용할 수 없어요">
        직무 비교·커리큘럼은 BE와 AI 서버가 실제 자격증명을 다시 검증해야 해요. 가입한 계정으로 로그인해 주세요.
      </Alert>
    );
  }

  const sources = postings.data?.sources ?? [];
  const evidenceRecords = new Set(analysis?.skill_evidence.map((e) => e.record_id) ?? []);
  const gaps = analysis ? gapSkills(analysis, alternatives) : [];

  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col gap-2 px-1">
        <h1 className="text-[26px] font-bold leading-snug tracking-tight">직무 비교·커리큘럼</h1>
        <p className="text-[15px] text-[var(--color-text-sub)]">
          검증된 자격증명과 검토된 채용공고를 비교하고, 근거가 부족한 부분을 채울 학습 계획을 만들어요.
        </p>
      </section>

      <Card className="flex flex-col gap-4">
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="text-[17px] font-bold">1. 비교할 공고</h2>
          {postings.data && (
            <span className="text-[13px] text-[var(--color-text-muted)]">
              최근 {postings.data.lookback_days}일 · {postings.data.posting_count}건
            </span>
          )}
        </div>
        {postings.isLoading && <p className="text-[13px] text-[var(--color-text-muted)]">불러오는 중...</p>}
        {postings.isError && <ErrorAlert error={postings.error} title="공고를 불러오지 못했어요" />}
        {postings.data && sources.length === 0 && (
          <p className="text-[14px] text-[var(--color-text-sub)]">
            아직 비교에 쓸 수 있는 공고가 없어요. 연구자가 원문을 검토해 공고를 반입하면 여기에 나타나요.
          </p>
        )}
        {sources.length > 0 && (
          <ul className="flex flex-col gap-1">
            <li>
              <label className="flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2.5 hover:bg-[var(--color-card-hover)]">
                <input
                  type="radio"
                  name="target"
                  checked={target === null}
                  onChange={() => setTarget(null)}
                  className="h-5 w-5 accent-[var(--color-primary)]"
                />
                <span className="text-[15px] font-semibold">전체 공고 기준으로 비교</span>
              </label>
            </li>
            {sources.map((s) => (
              <li key={s.posting_id}>
                <label className="flex cursor-pointer items-center gap-3 rounded-xl px-3 py-2.5 hover:bg-[var(--color-card-hover)]">
                  <input
                    type="radio"
                    name="target"
                    checked={target === s.posting_id}
                    onChange={() => setTarget(s.posting_id)}
                    className="h-5 w-5 shrink-0 accent-[var(--color-primary)]"
                  />
                  <span className="min-w-0">
                    <span className="block truncate text-[15px] font-semibold">
                      {s.company} · {s.title}
                    </span>
                    <span className="block text-[13px] text-[var(--color-text-muted)]">
                      {s.posted_at ? `${new Date(s.posted_at).toLocaleDateString('ko-KR')} 게시` : '게시일 미상'}
                      {' · '}
                      <a href={s.source_url} target="_blank" rel="noreferrer" className="hover:underline">
                        원문
                      </a>
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
        {postings.data && <WarningList codes={postings.data.warnings} />}
        <div className="flex flex-wrap items-center gap-3">
          <Button onClick={() => analyze.mutate()} isLoading={analyze.isPending} disabled={sources.length === 0}>
            검증하고 비교하기
          </Button>
          <span className="text-[13px] text-[var(--color-text-muted)]">
            비교할 때마다 모든 자격증명의 서명 원본을 다시 검증해요.
          </span>
        </div>
        {analyze.isError && <ErrorAlert error={analyze.error} title="비교하지 못했어요" />}
      </Card>

      {analysis && (
        <Card className="flex flex-col gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-[17px] font-bold">2. 비교 결과</h2>
            <span className="text-[13px] text-[var(--color-text-muted)]">
              근거로 쓴 자격증명 {evidenceRecords.size}개 · 제외 {analysis.excluded_record_ids.length}개 ·{' '}
              {formatTime(analysis.valid_until)}까지 계획 생성 가능
            </span>
          </div>
          {analysis.excluded_record_ids.length > 0 && (
            <Alert variant="warning" title="검증을 통과하지 못한 자격증명은 근거에서 뺐어요">
              폐기·만료되었거나 서명 원본으로 다시 검증할 수 없는 자격증명이에요.{' '}
              <Link to="/dashboard" className="font-semibold hover:underline">
                지갑에서 확인
              </Link>
            </Alert>
          )}
          <ul className="flex flex-col divide-y divide-[var(--color-border)]">
            {analysis.comparisons.map((c) => {
              const status = STATUS_LABEL[c.status];
              return (
                <li key={refOf(c)} className="flex flex-col gap-1 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${status.className}`}>
                      {status.label}
                    </span>
                    <span className="text-[12px] text-[var(--color-text-muted)]">
                      {c.requirement.importance === 'required' ? '필수' : '우대'}
                    </span>
                    <span className="text-[15px] font-semibold">“{c.requirement.quote}”</span>
                  </div>
                  <p className="text-[13px] text-[var(--color-text-sub)]">{text(c.reason_code)}</p>
                </li>
              );
            })}
          </ul>
          <WarningList codes={analysis.warnings} />
        </Card>
      )}

      {analysis && (
        <Card className="flex flex-col gap-4">
          <h2 className="text-[17px] font-bold">3. 학습 계획 만들기</h2>
          {alternativeComparisons(analysis).map((c) => (
            <label key={refOf(c)} className="flex flex-col gap-1 text-[14px]">
              <span>
                “{c.requirement.quote}” — 어느 쪽으로 준비할까요?
              </span>
              <select
                value={alternatives[refOf(c)] ?? ''}
                onChange={(e) =>
                  setAlternatives((prev) => {
                    const next = { ...prev };
                    if (e.target.value) next[refOf(c)] = e.target.value as SkillId;
                    else delete next[refOf(c)];
                    return next;
                  })
                }
                className="h-10 rounded-xl bg-[var(--color-surface)] px-3"
              >
                <option value="">선택 안 함 (확인 질문으로 남김)</option>
                {c.requirement.skill_ids.map((skill) => (
                  <option key={skill} value={skill}>
                    {label(skill)}
                  </option>
                ))}
              </select>
            </label>
          ))}
          {gaps.length > 0 && (
            <div className="flex flex-col gap-2">
              <p className="text-[14px] font-semibold">보완할 기술별 진행 방식</p>
              {gaps.map((skill) => (
                <label key={skill} className="flex items-center justify-between gap-3 text-[14px]">
                  <span>{label(skill)}</span>
                  <select
                    value={actions[skill] ?? 'assess'}
                    onChange={(e) =>
                      setActions((prev) => ({ ...prev, [skill]: e.target.value as LearningChoice['action'] }))
                    }
                    className="h-10 rounded-xl bg-[var(--color-surface)] px-3"
                  >
                    {(Object.keys(ACTION_LABEL) as LearningChoice['action'][]).map((key) => (
                      <option key={key} value={key}>
                        {ACTION_LABEL[key]}
                      </option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="flex flex-col gap-1 text-[14px]">
              기간(주)
              <input
                type="number"
                min={1}
                max={24}
                value={weeks}
                onChange={(e) => setWeeks(Number(e.target.value))}
                className="h-10 rounded-xl bg-[var(--color-surface)] px-3"
              />
            </label>
            <label className="flex flex-col gap-1 text-[14px]">
              주당 학습 시간
              <input
                type="number"
                min={1}
                max={40}
                value={hoursPerWeek}
                onChange={(e) => setHoursPerWeek(Number(e.target.value))}
                className="h-10 rounded-xl bg-[var(--color-surface)] px-3"
              />
            </label>
            <label className="flex flex-col gap-1 text-[14px]">
              예산(원, 비우면 제한 없음)
              <input
                type="number"
                min={0}
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
                placeholder="0 = 무료 자료만"
                className="h-10 rounded-xl bg-[var(--color-surface)] px-3"
              />
            </label>
          </div>
          <label className="flex items-start gap-2 text-[13px] text-[var(--color-text-sub)]">
            <input
              type="checkbox"
              checked={useLlm}
              onChange={(e) => setUseLlm(e.target.checked)}
              className="mt-0.5 h-4 w-4 accent-[var(--color-primary)]"
            />
            LLM으로 단계 순서·시간을 배분할게요. 허용된 계정만 사용할 수 있고, 계획 옵션(기술·자료 ID·시간)이 AI 서버의
            모델로 전달되는 것에 동의해요. 자격증명 원문과 개인정보는 전달되지 않아요.
          </label>
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => generate.mutate()} isLoading={generate.isPending}>
              학습 계획 만들기
            </Button>
            <span className="text-[13px] text-[var(--color-text-muted)]">
              비교 결과는 {formatTime(analysis.valid_until)}까지만 쓸 수 있어요. 지나면 다시 비교해 주세요.
            </span>
          </div>
          {generate.isError && <ErrorAlert error={generate.error} title="계획을 만들지 못했어요" />}
        </Card>
      )}

      {plan && (
        <Card className="flex flex-col gap-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-[17px] font-bold">학습 계획</h2>
            <span className="text-[13px] text-[var(--color-text-muted)]">
              총 {plan.result.total_hours}시간 · 확인된 비용 {plan.result.known_cost_krw.toLocaleString('ko-KR')}원
              {plan.result.has_unknown_costs ? ' + 비용 미상 자료' : ''} ·{' '}
              {plan.result.generation_method === 'template' ? '기본 계획' : plan.result.generation_method === 'llm' ? 'LLM 배분' : 'LLM 초안 폐기 후 기본 계획'}
            </span>
          </div>
          {plan.result.steps.length === 0 && (
            <p className="text-[14px] text-[var(--color-text-sub)]">계획에 넣을 학습 단계가 없어요.</p>
          )}
          <ol className="flex flex-col gap-3">
            {plan.result.steps.map((step, index) => (
              <li key={step.step_id} className="rounded-2xl bg-[var(--color-surface)] px-4 py-3">
                <p className="text-[15px] font-semibold">
                  {index + 1}. {step.goal}
                  {step.conditional && (
                    <span className="ml-2 rounded-md bg-white px-2 py-0.5 text-xs font-semibold text-[var(--color-text-sub)]">
                      경험 확인 후 필요 시
                    </span>
                  )}
                </p>
                <p className="mt-1 text-[14px] text-[var(--color-text-sub)]">{step.activity}</p>
                <p className="mt-1 text-[13px] text-[var(--color-text-muted)]">
                  완료 기준: {step.completion_criterion}
                </p>
                <p className="mt-1 text-[13px] text-[var(--color-text-muted)]">
                  {step.hours}시간 · {step.allocations.map((a) => `${a.week}주차 ${a.hours}h`).join(', ')}
                </p>
                {step.resource && (
                  <p className="mt-1 text-[13px]">
                    자료:{' '}
                    <a
                      href={step.resource.resource.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="font-semibold text-[var(--color-primary)] hover:underline"
                    >
                      {step.resource.resource.title}
                    </a>{' '}
                    ({step.resource.resource.provider},{' '}
                    {step.resource.resource.cost_krw === null
                      ? '비용 미상'
                      : step.resource.resource.cost_krw === 0
                        ? '무료'
                        : `${step.resource.resource.cost_krw.toLocaleString('ko-KR')}원`}
                    )
                  </p>
                )}
              </li>
            ))}
          </ol>
          {plan.result.questions.length > 0 && (
            <div className="flex flex-col gap-1">
              <p className="text-[14px] font-semibold">확인이 필요한 항목</p>
              <WarningList codes={[...new Set(plan.result.questions.map((q) => q.code))]} />
            </div>
          )}
          <WarningList codes={plan.result.warnings} />
        </Card>
      )}
    </div>
  );
}

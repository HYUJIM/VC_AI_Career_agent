import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { getMarketSnapshot, getMarketSnapshots } from '@/api/market';
import { Alert } from '@/components/ui/Alert';
import { Card } from '@/components/ui/Card';
import { getApiErrorMessage } from '@/lib/apiClient';
import type { MarketRoleId, MarketSkillCategory, MarketSnapshot } from '@/types/market';

const CATEGORY_LABEL: Record<MarketSkillCategory, string> = {
  language: '언어',
  framework: '프레임워크',
  database: '데이터베이스',
  data: '데이터',
  cloud: '클라우드',
  devops: '인프라·배포',
  security: '보안',
  privacy: '개인정보·규제',
  certification: '자격증',
  practice: '개발 방식',
};

const WARNING_TEXT: Record<string, string> = {
  SMALL_SAMPLE_STATISTICS_ARE_INDICATIVE_ONLY: '공고 수가 30건 미만이라 참고용으로만 봐 주세요.',
  SINGLE_SOURCE: '채용 사이트 한 곳의 공고만 집계했어요.',
  ONE_COMPANY_EXCEEDS_20_PERCENT_OF_POSTINGS: '공고가 많은 회사가 있어 회사별 비중을 줄여 계산했어요.',
  POSTING_CAPTURED_IN_THE_FUTURE: '수집 시각이 잘못된 공고가 섞여 있어요.',
};

const EXPERIENCE_LABEL: Record<MarketSnapshot['experience'][number]['bucket'], string> = {
  entry: '신입',
  junior: '1~3년',
  mid: '4~7년',
  senior: '8년 이상',
  unspecified: '무관·미기재',
};

const TOP_SKILLS = 12;
const percent = (value: number) => `${Math.round(value * 100)}%`;

/** 직무별로 채용 공고가 어떤 역량을 얼마나 요구하는지 보여 준다. 통계가 없으면 아무것도 그리지 않는다. */
export function MarketTrendsCard() {
  const list = useQuery({ queryKey: ['market-snapshots'], queryFn: getMarketSnapshots });
  const [picked, setPicked] = useState<MarketRoleId | null>(null);
  const roleId = picked ?? list.data?.[0]?.role_id ?? null;
  const detail = useQuery({
    queryKey: ['market-snapshot', roleId],
    queryFn: () => getMarketSnapshot(roleId as MarketRoleId),
    enabled: roleId !== null,
  });

  if (list.isPending || (list.data && list.data.length === 0)) return null;
  if (list.isError) {
    return (
      <Alert variant="error" title="직무별 요구 역량 통계를 불러오지 못했어요">
        {getApiErrorMessage(list.error)}
      </Alert>
    );
  }

  const snapshot = detail.data;
  const labelOf = new Map(snapshot?.skills.map((skill) => [skill.skill_id, skill.label]) ?? []);
  const top = snapshot?.skills.slice(0, TOP_SKILLS) ?? [];
  const maxImportance = top[0]?.importance || 1;

  return (
    <Card className="flex flex-col gap-4">
      <div className="flex flex-col gap-1">
        <h2 className="text-[17px] font-bold">직무별 요구 역량 통계</h2>
        <p className="text-[14px] text-[var(--color-text-sub)]">
          수집한 채용 공고에서 어떤 역량을 얼마나 요구하는지 집계했어요. 학습 우선순위를 정할 때 참고하세요.
        </p>
      </div>

      <div className="flex flex-wrap gap-2" role="tablist" aria-label="직무 선택">
        {list.data.map((role) => {
          const selected = role.role_id === roleId;
          return (
            <button
              key={role.role_id}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => setPicked(role.role_id)}
              className={`rounded-full px-4 py-2 text-[14px] font-semibold transition-colors ${
                selected
                  ? 'bg-[var(--color-primary)] text-white'
                  : 'bg-[var(--color-surface)] text-[var(--color-text-sub)] hover:text-[var(--color-text)]'
              }`}
            >
              {role.role_label}
              <span className="ml-1 font-normal opacity-80">{role.sample_size}건</span>
            </button>
          );
        })}
      </div>

      {detail.isError && (
        <Alert variant="error" title="통계를 불러오지 못했어요">
          {getApiErrorMessage(detail.error)}
        </Alert>
      )}

      {snapshot && (
        <div className="flex flex-col gap-4" role="tabpanel">
          <p className="text-[13px] text-[var(--color-text-sub)]">
            공고 {snapshot.sample_size}건 · 회사 {snapshot.companies}곳 ·{' '}
            {new Date(snapshot.as_of).toLocaleDateString('ko-KR')} 기준
          </p>

          <ol className="flex flex-col gap-2">
            {top.map((skill) => (
              <li key={skill.skill_id} className="flex flex-col gap-1">
                <div className="flex items-baseline justify-between gap-3 text-[14px]">
                  <span className="font-semibold">
                    {skill.rank}. {skill.label}
                    <span className="ml-2 text-[12px] font-normal text-[var(--color-text-sub)]">
                      {CATEGORY_LABEL[skill.category]}
                    </span>
                  </span>
                  <span className="shrink-0 text-[13px] text-[var(--color-text-sub)]">
                    공고의 {percent(skill.mention_rate)}가 언급 · 그중 자격요건 {percent(skill.required_ratio)}
                  </span>
                </div>
                <div
                  className="h-2 overflow-hidden rounded-full bg-[var(--color-surface)]"
                  role="img"
                  aria-label={`${skill.label} 중요도 ${percent(skill.importance)}`}
                >
                  <div
                    className="h-full rounded-full bg-[var(--color-primary)]"
                    style={{ width: `${Math.max(2, (skill.importance / maxImportance) * 100)}%` }}
                  />
                </div>
              </li>
            ))}
          </ol>

          {snapshot.pairs.length > 0 && (
            <div className="flex flex-col gap-2">
              <h3 className="text-[14px] font-bold">함께 요구되는 기술</h3>
              <ul className="flex flex-wrap gap-2">
                {snapshot.pairs.slice(0, 6).map((pair) => (
                  <li
                    key={`${pair.skill_a}-${pair.skill_b}`}
                    className="rounded-full bg-[var(--color-surface)] px-3 py-1 text-[13px]"
                  >
                    {labelOf.get(pair.skill_a) ?? pair.skill_a} + {labelOf.get(pair.skill_b) ?? pair.skill_b}
                    <span className="ml-1 text-[var(--color-text-sub)]">{pair.postings}건</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="text-[13px] text-[var(--color-text-sub)]">
            요구 경력:{' '}
            {snapshot.experience
              .filter((item) => item.postings > 0)
              .map((item) => `${EXPERIENCE_LABEL[item.bucket]} ${item.postings}건`)
              .join(' · ')}
          </p>

          {snapshot.warnings.length > 0 && (
            <ul className="flex flex-col gap-1 text-[13px] text-[var(--color-text-sub)]">
              {snapshot.warnings.map((code) => (
                <li key={code}>※ {WARNING_TEXT[code] ?? code}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </Card>
  );
}

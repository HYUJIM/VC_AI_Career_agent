"""공고 묶음 → 직무별 시장 통계(스냅샷). 상태 없는 순수 계산이다.

1. 추출: 공고의 기술스택·자격요건·우대사항·주요업무에서 사전(catalog.py)에 있는 기술을 찾는다.
   공고 하나에서 한 기술은 한 번만 세고, 가장 강한 자리를 그 공고의 수준으로 삼는다
   (자격요건 required > 기술스택 stack > 우대사항 preferred > 주요업무·제목 mentioned).
2. 중복 제거: 같은 (source, external_id), 그리고 회사·제목·본문이 같은 재게시 공고는 한 건으로 센다.
3. 통계: 언급률과 Wilson 95% 구간, 필수 비율, 중요도, 함께 나오는 기술 쌍(lift), 경력 분포.
   중요도 = Σ(공고 가중치 × 수준 가중치) / Σ(공고 가중치).
   - 수준 가중치: required 1.0, stack 0.7, preferred 0.5, mentioned 0.3
   - 공고 가중치: 한 회사가 공고를 많이 올려 통계를 좌우하지 않게 회사당 합이 COMPANY_CAP(3)을 넘지 않게 줄인다.

공고 문장은 데이터로만 다룬다(지시로 해석하지 않는다). 스냅샷에는 원문을 넣지 않는다.
"""

import math
from collections import Counter, defaultdict
from datetime import datetime

from src.career.canonical import digest_json
from src.career.contracts.market import (
    ExperienceCount,
    ExtractedSkill,
    MarketPosting,
    MarketSnapshot,
    MarketSnapshotOutput,
    MarketSnapshotRequest,
    PostingExtraction,
    SkillPair,
    SkillStatistic,
    SourceCount,
)
from src.career.skill_catalog import bounded_normalized_text
from src.market.catalog import CATALOG_VERSION, ROLE_LABELS, SKILL_BY_ID, STACK_EXACT, TEXT_PATTERNS

EXTRACTOR_VERSION = "rules/1"
LEVEL_WEIGHT = {"required": 1.0, "stack": 0.7, "preferred": 0.5, "mentioned": 0.3}
LEVEL_ORDER = ("required", "stack", "preferred", "mentioned")
COMPANY_CAP = 3.0
SMALL_SAMPLE = 30
MAX_PAIRS = 20
Z95 = 1.959963984540054


def _text_skills(normalized: str) -> set[str]:
    return {
        skill_id
        for skill_id, patterns in TEXT_PATTERNS.items()
        if any(pattern.search(normalized) for pattern in patterns)
    }


def extract_skills(posting: MarketPosting) -> dict[str, str]:
    """공고 하나에서 찾은 기술과 그 수준. {skill_id: level}"""
    stack = set()
    for item in posting.tech_stack:
        normalized = bounded_normalized_text(item)
        exact = STACK_EXACT.get(normalized)
        if exact:
            stack.add(exact)
        stack |= _text_skills(normalized)
    found = {
        "required": _text_skills(bounded_normalized_text(posting.qualifications)),
        "stack": stack,
        "preferred": _text_skills(bounded_normalized_text(posting.preferred)),
        "mentioned": _text_skills(
            bounded_normalized_text(posting.title + "\n" + posting.responsibilities)
        ),
    }
    levels: dict[str, str] = {}
    for level in LEVEL_ORDER:  # 강한 자리부터 채운다
        for skill_id in found[level]:
            levels.setdefault(skill_id, level)
    return levels


def experience_bucket(posting: MarketPosting) -> str:
    years = posting.experience_min_years
    if years is None:
        return "unspecified"
    if years == 0:
        return "entry"
    if years <= 3:
        return "junior"
    if years <= 7:
        return "mid"
    return "senior"


def _content_key(posting: MarketPosting) -> tuple:
    return (
        bounded_normalized_text(posting.company),
        bounded_normalized_text(posting.title),
        bounded_normalized_text(posting.qualifications),
        bounded_normalized_text(posting.preferred),
        tuple(sorted(bounded_normalized_text(item) for item in posting.tech_stack)),
    )


def deduplicate(postings: list[MarketPosting]) -> tuple[list[MarketPosting], int]:
    """같은 출처 ID, 같은 내용의 재게시를 뺀다. 먼저 온 공고를 남긴다."""
    seen_ids, seen_content, kept = set(), set(), []
    for posting in postings:
        id_key = (posting.source, posting.external_id)
        content_key = _content_key(posting)
        if id_key in seen_ids or content_key in seen_content:
            continue
        seen_ids.add(id_key)
        seen_content.add(content_key)
        kept.append(posting)
    return kept, len(postings) - len(kept)


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    p = successes / total
    denominator = 1 + Z95**2 / total
    center = (p + Z95**2 / (2 * total)) / denominator
    margin = Z95 * math.sqrt(p * (1 - p) / total + Z95**2 / (4 * total**2)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _round(value: float) -> float:
    return round(value, 4)


def build_snapshot(request: MarketSnapshotRequest, now: datetime) -> MarketSnapshotOutput:
    postings, duplicates = deduplicate(request.postings)
    total = len(postings)
    per_company = Counter(bounded_normalized_text(p.company) for p in postings)
    weights = [min(1.0, COMPANY_CAP / per_company[bounded_normalized_text(p.company)]) for p in postings]
    weight_sum = sum(weights)

    extracted = [extract_skills(posting) for posting in postings]
    counts = defaultdict(Counter)  # skill_id -> level -> 공고 수
    score = Counter()  # skill_id -> 가중 점수 합
    for levels, weight in zip(extracted, weights):
        for skill_id, level in levels.items():
            counts[skill_id][level] += 1
            score[skill_id] += weight * LEVEL_WEIGHT[level]

    statistics = []
    for skill_id, by_level in counts.items():
        mentioned = sum(by_level.values())
        low, high = wilson_interval(mentioned, total)
        skill = SKILL_BY_ID[skill_id]
        statistics.append(
            SkillStatistic(
                rank=0,
                skill_id=skill_id,
                label=skill.label,
                category=skill.category,
                mentioned_postings=mentioned,
                required_postings=by_level["required"],
                stack_postings=by_level["stack"],
                preferred_postings=by_level["preferred"],
                mention_rate=_round(mentioned / total),
                mention_rate_low=_round(low),
                mention_rate_high=_round(high),
                required_ratio=_round(by_level["required"] / mentioned),
                importance=_round(score[skill_id] / weight_sum),
            )
        )
    # 중요도가 같으면 언급 수, 그다음 ID 순으로 고정해 같은 입력이 항상 같은 해시가 되게 한다.
    statistics.sort(key=lambda s: (-s.importance, -s.mentioned_postings, s.skill_id))
    for rank, item in enumerate(statistics, start=1):
        item.rank = rank

    # 함께 나오는 기술 쌍: 둘 다 3건 이상·표본의 10% 이상 언급된 기술끼리만 (드문 기술의 우연한 lift를 뺀다)
    floor = max(3, math.ceil(total * 0.1))
    frequent = sorted(s.skill_id for s in statistics if s.mentioned_postings >= floor)
    together = Counter()
    for levels in extracted:
        present = [skill_id for skill_id in frequent if skill_id in levels]
        for index, first in enumerate(present):
            for second in present[index + 1 :]:
                together[(first, second)] += 1
    mentioned_count = {s.skill_id: s.mentioned_postings for s in statistics}
    pairs = []
    for (first, second), both in together.items():
        lift = (both / total) / ((mentioned_count[first] / total) * (mentioned_count[second] / total))
        if both >= floor and lift > 1.0:
            pairs.append(SkillPair(skill_a=first, skill_b=second, postings=both, lift=_round(lift)))
    pairs.sort(key=lambda p: (-p.postings, -p.lift, p.skill_a, p.skill_b))

    buckets = Counter(experience_bucket(posting) for posting in postings)
    sources = Counter(posting.source for posting in postings)

    warnings = []
    if total < SMALL_SAMPLE:
        warnings.append("SMALL_SAMPLE_STATISTICS_ARE_INDICATIVE_ONLY")
    if len(sources) == 1:
        warnings.append("SINGLE_SOURCE")
    if max(per_company.values()) / total > 0.2:
        warnings.append("ONE_COMPANY_EXCEEDS_20_PERCENT_OF_POSTINGS")
    if any(posting.captured_at > now for posting in postings):
        warnings.append("POSTING_CAPTURED_IN_THE_FUTURE")

    body = {
        "role_id": request.role_id,
        "role_label": ROLE_LABELS[request.role_id],
        "catalog_version": CATALOG_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "as_of": request.as_of,
        "sample_size": total,
        "duplicates_removed": duplicates,
        "companies": len(per_company),
        "sources": [SourceCount(source=name, postings=n) for name, n in sorted(sources.items())],
        "experience": [
            ExperienceCount(bucket=bucket, postings=buckets[bucket])
            for bucket in ("entry", "junior", "mid", "senior", "unspecified")
        ],
        "skills": statistics,
        "pairs": pairs[:MAX_PAIRS],
        "warnings": warnings,
    }
    draft = MarketSnapshot(**body, content_hash="")
    payload = draft.model_dump(mode="json")
    payload.pop("content_hash")
    snapshot = draft.model_copy(update={"content_hash": digest_json(payload)})
    extractions = [
        PostingExtraction(
            source=posting.source,
            external_id=posting.external_id,
            skills=[
                ExtractedSkill(skill_id=skill_id, level=level)
                for skill_id, level in sorted(levels.items())
            ],
        )
        for posting, levels in zip(postings, extracted)
    ]
    return MarketSnapshotOutput(snapshot=snapshot, extractions=extractions)

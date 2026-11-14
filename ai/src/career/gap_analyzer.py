import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.career.contracts.career import (
    CareerAnalysisRequest,
    DemandStatistic,
    JobVersion,
    RequirementComparison,
    SkillEvidence,
)
from src.career.job_requirements import EXPERIENCE_PATTERN
from src.career.canonical import digest_json
from src.career.skill_catalog import CATALOG, normalized_text

CAPTURE_FRESHNESS_DAYS = 7
MINIMUM_SAMPLE_SIZE = 5  # Pilot policy, not a claim of statistical representativeness.
MAX_POSTINGS = 200


def canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"gclid", "fbclid"}
    ]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            urlencode(sorted(query)),
            "",
        )
    )


def open_and_fresh(version: JobVersion, now: datetime) -> bool:
    posting = version.posting
    return (
        posting.dataset_kind == "real"
        and posting.is_open
        and now - timedelta(days=CAPTURE_FRESHNESS_DAYS) <= posting.captured_at <= now
        and (posting.closes_at is None or posting.closes_at > now)
    )


def matches_filters(version: JobVersion, request: CareerAnalysisRequest) -> bool:
    posting = version.posting
    return (
        posting.role_id == request.target_role_id
        and (
            request.region is None
            or normalized_text(posting.region) == normalized_text(request.region)
        )
        and (request.seniority is None or posting.seniority == request.seniority)
    )


def deduplicate(versions: list[JobVersion], target_id=None):
    parents = list(range(len(versions)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    seen_urls, seen_contents = {}, {}
    for index, version in enumerate(versions):
        posting = version.posting
        url = canonical_url(str(posting.source_url))
        content = digest_json(
            [
                normalized_text(posting.company),
                normalized_text(posting.title),
                normalized_text(posting.raw_text),
            ]
        )
        for previous in (seen_urls.get(url), seen_contents.get(content)):
            if previous is not None:
                parents[root(index)] = root(previous)
        seen_urls[url] = index
        seen_contents[content] = index
    ordered = sorted(
        range(len(versions)),
        key=lambda index: (
            versions[index].posting_id == target_id,
            versions[index].posting.posted_at or datetime.min.replace(tzinfo=timezone.utc),
            versions[index].imported_at,
            str(versions[index].version_id),
        ),
        reverse=True,
    )
    unique, seen_groups = [], set()
    for index in ordered:
        group = root(index)
        if group not in seen_groups:
            seen_groups.add(group)
            unique.append(versions[index])
    return unique, len(versions) - len(unique)


def select_market(versions: list[JobVersion], request: CareerAnalysisRequest, now: datetime):
    eligible = [
        version
        for version in versions
        if open_and_fresh(version, now)
        and matches_filters(version, request)
        and version.posting.posted_at is not None
        and now - timedelta(days=90) <= version.posting.posted_at <= now
    ]
    recent = [v for v in eligible if v.posting.posted_at >= now - timedelta(days=30)]
    unique, duplicates = deduplicate(recent, request.target_posting_id)
    window = 30
    if len(unique) < MINIMUM_SAMPLE_SIZE:
        unique, duplicates = deduplicate(eligible, request.target_posting_id)
        window = 90
    return unique, duplicates, window


def demand_statistics(versions: list[JobVersion]) -> list[DemandStatistic]:
    result = []
    for skill in CATALOG.skills:
        mentioned, required, preferred, alternative, ambiguous = set(), set(), set(), set(), set()
        for version in versions:
            for requirement in version.posting.requirements:
                if skill.skill_id not in requirement.skill_ids:
                    continue
                mentioned.add(version.posting_id)
                if requirement.match == "any" and len(requirement.skill_ids) > 1:
                    alternative.add(version.posting_id)
                elif requirement.match == "unspecified" and len(requirement.skill_ids) > 1:
                    ambiguous.add(version.posting_id)
                else:
                    (required if requirement.importance == "required" else preferred).add(
                        version.posting_id
                    )
        if mentioned:
            result.append(
                DemandStatistic(
                    skill_id=skill.skill_id,
                    mentioned_postings=len(mentioned),
                    required_postings=len(required),
                    preferred_postings=len(preferred),
                    alternative_postings=len(alternative),
                    ambiguous_postings=len(ambiguous),
                    denominator=len(versions),
                )
            )
    return result


def compare_requirements(
    versions: list[JobVersion], evidence: list[SkillEvidence], has_excluded_records: bool
) -> list[RequirementComparison]:
    results = []
    for version in versions:
        for requirement in version.posting.requirements:
            relevant = [item for item in evidence if item.skill_id in requirement.skill_ids]
            # Completion != assessment != project != professional experience.
            satisfying = {
                item.skill_id
                for item in relevant
                if item.evidence_type == requirement.minimum_evidence
                or requirement.minimum_evidence == "alignment"
            }
            if requirement.kind != "skill" or re.search(EXPERIENCE_PATTERN, requirement.quote):
                status, reason = "unknown", "NON_SKILL_OR_DURATION_REQUIRES_MANUAL_ASSESSMENT"
            elif requirement.match == "unspecified" and len(requirement.skill_ids) > 1:
                status, reason = "unknown", "AMBIGUOUS_REQUIREMENT_LOGIC"
            elif (
                bool(satisfying)
                if requirement.match == "any"
                else set(requirement.skill_ids).issubset(satisfying)
            ):
                status, reason = "supported", "EVIDENCE_SUPPORTS_THIS_ANNOTATED_SCOPE_ONLY"
            elif relevant:
                status, reason = "partially_supported", "EVIDENCE_SCOPE_OR_ALTERNATIVES_INCOMPLETE"
            elif has_excluded_records:
                status, reason = "unknown", "EXCLUDED_CREDENTIALS_MAY_CONTAIN_EVIDENCE"
            else:
                status, reason = "not_evidenced", "NO_EVIDENCE_NOT_A_PROOF_OF_NO_SKILL"
            results.append(
                RequirementComparison(
                    posting_id=version.posting_id,
                    version_id=version.version_id,
                    requirement=requirement,
                    status=status,
                    reason_code=reason,
                    evidence_ids=sorted({item.evidence_id for item in relevant}, key=str),
                    priority=(
                        "none"
                        if status == "supported"
                        else "high"
                        if requirement.importance == "required"
                        else "medium"
                    ),
                    next_action=(
                        "none"
                        if status == "supported"
                        else "manual_assessment"
                        if status == "unknown"
                        else "confirm_or_provide_evidence"
                    ),
                )
            )
    order = {"high": 0, "medium": 1, "none": 2}
    return sorted(
        results, key=lambda item: (order[item.priority], str(item.posting_id), item.requirement.key)
    )

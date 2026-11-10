"""Deterministic evidence comparison (moved from BE app/services/career_analysis.py).

BE loads the verified snapshot and the stored job versions, then persists the result.
This module only computes; it never sees passwords, keys or other users' data.
"""

from datetime import datetime, timedelta
from uuid import uuid4

from src.career.canonical import digest_json
from src.career.contracts.api import JobSearchResult
from src.career.contracts.career import (
    CareerAnalysis,
    CareerAnalysisRequest,
    JobReference,
    JobVersion,
)
from src.career.contracts.verification import VerificationSnapshot
from src.career.errors import EvidenceError, Rejection
from src.career.gap_analyzer import (
    CAPTURE_FRESHNESS_DAYS,
    MAX_POSTINGS,
    MINIMUM_SAMPLE_SIZE,
    compare_requirements,
    demand_statistics,
    matches_filters,
    open_and_fresh,
    select_market,
)
from src.career.job_requirements import validate_annotations
from src.career.skill_normalizer import map_skills


def reference(version, target_id=None):
    posting = version.posting
    return JobReference(
        posting_id=version.posting_id,
        version_id=version.version_id,
        content_hash=version.content_hash,
        source_url=str(posting.source_url),
        company=posting.company,
        title=posting.title,
        posted_at=posting.posted_at,
        captured_at=posting.captured_at,
        closes_at=posting.closes_at,
        is_target=version.posting_id == target_id,
    )


def check_sources(versions: list[JobVersion]):
    """Defense in depth: BE re-hashed the stored versions; re-check content and annotations."""
    for version in versions:
        try:
            if version.content_hash != digest_json(version.posting.model_dump(mode="json")):
                raise EvidenceError("JOB_VERSION_BINDING_MISMATCH")
            validate_annotations(version.posting, version.imported_at)
        except EvidenceError as exc:
            raise Rejection(409, "JOB_SOURCE_INVALID", "Job source integrity check failed") from exc


def warnings_for(market, duplicates, window):
    warnings = [
        "LOCAL_REVIEWED_CORPUS_NOT_THE_WHOLE_MARKET",
        "MANUAL_CAPTURE_NOT_A_LIVE_CRAWL",
        "MENTION_FREQUENCY_IS_NOT_A_UNIVERSAL_MANDATORY_REQUIREMENT",
    ]
    if window == 90:
        warnings.append("LOOKBACK_EXPANDED_FROM_30_TO_90_DAYS")
    if len(market) < MINIMUM_SAMPLE_SIZE:
        warnings.append("SMALL_OR_EMPTY_MARKET_SAMPLE")
    if duplicates:
        warnings.append("DUPLICATE_SOURCES_REMOVED")
    return warnings


def _too_many():
    return Rejection(
        422, "TOO_MANY_ELIGIBLE_POSTINGS", "Too many eligible postings; narrow region or seniority"
    )


def search(request: CareerAnalysisRequest, candidates: list[JobVersion], now: datetime):
    check_sources(candidates)
    market, duplicates, window = select_market(candidates, request, now)
    if len(market) > MAX_POSTINGS:
        raise _too_many()
    return JobSearchResult(
        as_of=now,
        lookback_days=window,
        posting_count=len(market),
        duplicate_posting_count=duplicates,
        sources=[reference(item) for item in market],
        market_demand=demand_statistics(market),
        warnings=warnings_for(market, duplicates, window),
    )


def analyze(
    snapshot: VerificationSnapshot,
    request: CareerAnalysisRequest,
    candidates: list[JobVersion],
    target: JobVersion | None,
    now: datetime,
) -> CareerAnalysis:
    if snapshot.scope != "all":
        raise Rejection(
            409, "PROFILE_SCOPE_NOT_ALL", "Verify the full profile before career comparison"
        )
    if snapshot.valid_until <= now:
        raise Rejection(
            409, "STALE_PROFILE", "Verification snapshot is stale or invalid; verify again"
        )
    if target is not None and target.posting_id != request.target_posting_id:
        raise Rejection(409, "TARGET_MISMATCH", "Target posting does not match the request")
    check_sources(candidates + ([target] if target else []))
    market, duplicates, window = select_market(candidates, request, now)
    if len(market) > MAX_POSTINGS:
        raise _too_many()
    if target and (
        not matches_filters(target, request)
        or not open_and_fresh(target, now)
        or target.posting.posted_at
        and target.posting.posted_at > now
    ):
        raise Rejection(
            409,
            "TARGET_POSTING_NOT_ELIGIBLE",
            "Target posting is stale, closed, synthetic or outside filters",
        )
    if not market and not target:
        raise Rejection(
            422, "NO_ELIGIBLE_JOB_SOURCES", "No eligible reviewed job sources; import sources first"
        )
    selected = {item.posting_id: item for item in market}
    if target:
        selected[target.posting_id] = target
    evidence, unmapped = map_skills(snapshot)
    warnings = warnings_for(market, duplicates, window)
    if target and target.posting.posted_at is None:
        warnings.append("TARGET_POSTING_DATE_UNKNOWN_NOT_COUNTED_AS_RECENT")
    if target:
        warnings.append("COMPARISON_IS_TARGET_SPECIFIC_MARKET_STATS_ARE_CONTEXT_ONLY")
    if snapshot.excluded_credentials:
        warnings.append("EXCLUDED_CREDENTIALS_LIMIT_PROFILE_COVERAGE")
    if unmapped:
        warnings.append("UNMAPPED_CREDENTIALS_REQUIRE_CURATED_RULE_REVIEW")
    limits = [snapshot.valid_until]
    for version in selected.values():
        limits.append(version.posting.captured_at + timedelta(days=CAPTURE_FRESHNESS_DAYS))
        if version.posting.closes_at:
            limits.append(version.posting.closes_at)
        if version in market and version.posting.posted_at:
            limits.append(version.posting.posted_at + timedelta(days=window))
    return CareerAnalysis(
        analysis_id=uuid4(),
        verification_snapshot_id=snapshot.verification_snapshot_id,
        subject_did=snapshot.subject_did,
        as_of=now,
        valid_until=min(limits),
        request=request,
        lookback_days=window,
        market_posting_count=len(market),
        duplicate_posting_count=duplicates,
        comparison_scope="target_posting" if target else "market_postings",
        sources=[reference(item, request.target_posting_id) for item in selected.values()],
        skill_evidence=evidence,
        comparisons=compare_requirements(
            [target] if target else market,
            evidence,
            bool(snapshot.excluded_credentials),
        ),
        market_demand=demand_statistics(market),
        unmapped_record_ids=unmapped,
        excluded_record_ids=[item.record_id for item in snapshot.excluded_credentials],
        warnings=warnings,
    )

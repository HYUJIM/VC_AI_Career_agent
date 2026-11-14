"""Analysis semantics formerly asserted through BE's DB integration tests."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.career.career_analysis import analyze, search
from src.career.contracts.career import CareerAnalysisRequest
from src.career.errors import Rejection


def now():
    return datetime.now(timezone.utc)


def test_target_analysis_cites_bound_evidence_without_scores(mapped_snapshot, make_version):
    target = make_version()
    result = analyze(
        mapped_snapshot,
        CareerAnalysisRequest(target_posting_id=target.posting_id),
        [target],
        target,
        now(),
    )
    by_key = {item.requirement.key: item for item in result.comparisons}
    assert by_key["r0"].status == "supported"
    assert by_key["r1"].status == by_key["r2"].status == "not_evidenced"
    assert by_key["r3"].priority == "medium"
    assert result.comparison_scope == "target_posting"
    assert result.verification_snapshot_id == mapped_snapshot.verification_snapshot_id
    assert result.skill_evidence[0].record_id == mapped_snapshot.accepted_credentials[0].record_id
    assert result.valid_until <= mapped_snapshot.valid_until


def with_unmapped(snapshot, **update):
    """Adds a verified credential that no exact-claim rule maps (e.g. a national license)."""
    extra = snapshot.accepted_credentials[0].model_copy(deep=True, update={"record_id": uuid4()})
    extra.credential.achievement_name = "정보처리기사"
    extra.credential.achievement.update(name="정보처리기사", description="국가기술자격")
    accepted = [*snapshot.accepted_credentials, extra]
    return snapshot.model_copy(update={"accepted_credentials": accepted, **update}), extra.record_id


def test_verified_unmapped_credential_is_not_treated_as_excluded(mapped_snapshot, make_version):
    snapshot, unmapped_id = with_unmapped(mapped_snapshot)
    target = make_version()
    request = CareerAnalysisRequest(target_posting_id=target.posting_id)
    result = analyze(snapshot, request, [target], target, now())
    by_key = {item.requirement.key: item for item in result.comparisons}
    assert by_key["r0"].status == "supported"
    for key in ("r1", "r2", "r3"):
        assert by_key[key].status == "not_evidenced"
        assert by_key[key].reason_code == "NO_EVIDENCE_NOT_A_PROOF_OF_NO_SKILL"
        assert by_key[key].next_action == "confirm_or_provide_evidence"
    assert result.unmapped_record_ids == [unmapped_id]
    assert result.excluded_record_ids == []
    assert "UNMAPPED_CREDENTIALS_REQUIRE_CURATED_RULE_REVIEW" in result.warnings
    assert "EXCLUDED_CREDENTIALS_LIMIT_PROFILE_COVERAGE" not in result.warnings


def test_excluded_credentials_still_withhold_judgment_alongside_unmapped(
    mapped_snapshot, make_version
):
    revoked = mapped_snapshot.accepted_credentials[0].model_copy(
        update={"record_id": uuid4(), "status": "revoked", "accepted": False, "credential": None}
    )
    snapshot, _ = with_unmapped(mapped_snapshot, excluded_credentials=[revoked])
    target = make_version()
    request = CareerAnalysisRequest(target_posting_id=target.posting_id)
    result = analyze(snapshot, request, [target], target, now())
    by_key = {item.requirement.key: item for item in result.comparisons}
    assert by_key["r0"].status == "supported"
    for key in ("r1", "r2", "r3"):
        assert by_key[key].status == "unknown"
        assert by_key[key].reason_code == "EXCLUDED_CREDENTIALS_MAY_CONTAIN_EVIDENCE"


def test_target_requirements_are_separate_from_market_context(
    mapped_snapshot, make_version, make_posting
):
    target = make_version()
    other = make_version(make_posting([{"quote": "Docker 실무 경험", "skill_ids": ["docker"]}]))
    request = CareerAnalysisRequest(target_posting_id=target.posting_id)
    result = analyze(mapped_snapshot, request, [target, other], target, now())
    assert result.market_posting_count == 2
    assert all(item.posting_id == target.posting_id for item in result.comparisons)
    assert any(item.posting_id == other.posting_id for item in result.sources)
    assert any(item.skill_id == "docker" for item in result.market_demand)


def test_unknown_posting_date_can_only_be_explicit_target(
    mapped_snapshot, make_version, make_posting
):
    target = make_version(make_posting(posted_at=None))
    with pytest.raises(Rejection) as exc:
        analyze(mapped_snapshot, CareerAnalysisRequest(), [target], None, now())
    assert exc.value.status == 422
    result = analyze(
        mapped_snapshot,
        CareerAnalysisRequest(target_posting_id=target.posting_id),
        [target],
        target,
        now(),
    )
    assert result.market_posting_count == 0 and result.market_demand == []
    assert "TARGET_POSTING_DATE_UNKNOWN_NOT_COUNTED_AS_RECENT" in result.warnings


@pytest.mark.parametrize(
    "change",
    [
        {"is_open": False},
        {"dataset_kind": "synthetic"},
        {"posted_at": now() - timedelta(days=20), "captured_at": now() - timedelta(days=8)},
    ],
)
def test_closed_synthetic_and_old_capture_are_not_eligible(
    mapped_snapshot, make_version, make_posting, change
):
    target = make_version(make_posting(**change))
    with pytest.raises(Rejection) as exc:
        analyze(
            mapped_snapshot,
            CareerAnalysisRequest(target_posting_id=target.posting_id),
            [target],
            target,
            now(),
        )
    assert exc.value.status == 409
    assert exc.value.message == "Target posting is stale, closed, synthetic or outside filters"


def test_partial_profile_and_tampered_sources_fail_closed(mapped_snapshot, make_version):
    partial = mapped_snapshot.model_copy(update={"scope": "selected"})
    with pytest.raises(Rejection) as exc:
        analyze(partial, CareerAnalysisRequest(), [make_version()], None, now())
    assert exc.value.status == 409
    tampered = make_version()
    tampered.posting.title = "tampered after signing"
    with pytest.raises(Rejection) as exc:
        analyze(mapped_snapshot, CareerAnalysisRequest(), [tampered], None, now())
    assert (exc.value.status, exc.value.code) == (409, "JOB_SOURCE_INVALID")


def test_search_reports_denominator_and_warnings(make_version):
    result = search(CareerAnalysisRequest(), [make_version()], now())
    assert result.posting_count == 1 and result.lookback_days == 90
    assert "SMALL_OR_EMPTY_MARKET_SAMPLE" in result.warnings
    assert all(item.denominator == 1 for item in result.market_demand)


def test_stored_version_over_the_normalization_limit_fails_closed(
    mapped_snapshot, make_posting, make_version
):
    from src.career.contracts.career import JobPostingInput

    data = make_posting().model_dump(mode="json")
    data["raw_text"] += "\ufdfa" * 1000  # 상한이 생기기 전에 저장된 버전
    version = make_version(JobPostingInput.model_validate(data))
    with pytest.raises(Rejection) as exc:
        search(CareerAnalysisRequest(), [version], now())
    assert (exc.value.status, exc.value.code) == (409, "JOB_SOURCE_INVALID")
    with pytest.raises(Rejection) as exc:
        analyze(mapped_snapshot, CareerAnalysisRequest(), [version], None, now())
    assert (exc.value.status, exc.value.code) == (409, "JOB_SOURCE_INVALID")

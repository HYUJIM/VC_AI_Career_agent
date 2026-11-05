"""Synthetic schema fixtures only; no actual human approvals or model calls."""
import copy
import hashlib
import json
import uuid

import pytest

from src.rag.paper_evaluation_v2 import digest, validate_schema
from src.rag.paper_experiment_review import (
    make_review_bundle, pending_review, summarize_experiment,
)


def case_fixture(case_id='case', family_id='family'):
    fact = dict(fact_id='fact', entity_id='education', attribute='hours',
                value=30, unit='hour', time_scope=None, source_ids=['synthetic-source'])
    verification = dict(status='test-fixture-status', checks=[],
                        verifier_revision='test-only', policy_id='test-only',
                        artifact_sha256='1' * 64, suite='synthetic-only',
                        reference_time='2026-10-11T00:00:00Z')
    records = [dict(record_id=rid, original_id=rid, subject_did='did:test:synthetic',
                    raw_sha256='2' * 64, text='Synthetic education record',
                    facts=[fact], verification=verification) for rid in ('r1', 'r2')]
    return dict(
        version='paper-case/2.0', case_id=case_id, family_id=family_id, split='dev',
        synthetic=True, profile=dict(subject_did='did:test:synthetic', text='Test profile'),
        jd=dict(jd_id='jd', text='Test JD', sha256='3' * 64), request='Test request',
        records=records, eligible_record_ids=['r1'], reference_facts=[fact],
        allowed_fact_ids=['fact'], required_fact_ids=['fact'], closed_world=False,
        noise=dict(kind='expired', target_ratio=0.5, raw_record_ids=['r2']),
        candidate_record_ids=['r1', 'r2'],
        expected_behavior={c: 'answer' for c in ('A_no_rag', 'B_naive_rag', 'C_verified_rag')},
        gold_status='human_approved')  # Contract test only, never a research approval.


def result_fixture(case, condition, *, seed=42):
    context = [] if condition == 'A_no_rag' else ['r1', 'r2']
    if condition == 'C_verified_rag':
        context = ['r1']
    return dict(
        version='paper-result/2.0', run_id=f"{case['case_id']}:{condition}:{seed}",
        case_id=case['case_id'], condition=condition, seed=seed, status='success',
        finish_reason='eos', answer='40 hours', context_record_ids=context,
        input_tokens=50, output_tokens=3)


def review_fixture(result, error=True, *, abstain=False):
    """Return simulated contract data, not judgments by actual reviewers."""
    originals, reviewers = {}, []
    for reviewer_id in ('synthetic-test-person-one', 'synthetic-test-person-two'):
        raw = json.dumps(dict(reviewer_id=reviewer_id, result_sha256=digest(result),
                              note='Synthetic unit-test fixture, not a real review')).encode()
        sha = hashlib.sha256(raw).hexdigest()
        originals[sha] = raw
        reviewers.append(dict(kind='human', reviewer_id=reviewer_id,
                              reviewed_at='2026-10-11T00:00:00Z', original_review_sha256=sha))
    claim = dict(
        claim_id='claim', canonical_key='education:hours:40',
        spans=[dict(start=0, end=8, text='40 hours')], entity_id='education',
        attribute='hours', normalized_value=40, unit='hour', time_scope=None,
        assertion_kind='fact', factual_correctness='no' if error else 'yes',
        context_support='no', evidence_eligibility='unknown', citation_correct='not_applicable',
        error_type='O' if error else 'none', upward='unknown', jd_aligned='unknown',
        supporting_record_ids=[], reference_fact_ids=['fact'], rationale='Synthetic test label')
    review = dict(
        version='paper-review/2.0', result_sha256=digest(result), status='human_approved',
        reviewers=reviewers, segmentation_complete=True, issues=[],
        adjudication_rationale='Synthetic test fixture only',
        behavior='abstain' if abstain else 'answer', task_correct='unknown',
        abstention_correct='unknown', covered_required_fact_ids=[],
        claims=[] if abstain else [claim])
    return review, originals


def experiment(case_specs):
    """case_specs = (case_id, family_id, B_error, C_error) rows."""
    cases, rows, results, reviews, originals = [], [], {}, {}, {}
    for cid, family, b_error, c_error in case_specs:
        case = case_fixture(cid, family)
        cases.append(case)
        for condition, error in [('B_naive_rag', b_error), ('C_verified_rag', c_error)]:
            result = result_fixture(case, condition)
            run_id = result['run_id']
            rows.append(dict(run_id=run_id, case_id=cid, family_id=family,
                             condition=condition, seed=42, order_index=len(rows)))
            results[run_id] = result
            review, raw = review_fixture(result, error)
            reviews[run_id] = review
            originals.update(raw)
    return cases, rows, results, reviews, originals


@pytest.fixture
def data():
    return experiment([('case', 'family', True, False)])


def summarize(data, **kwargs):
    return summarize_experiment(*data[:4], original_reviews=data[4], **kwargs)


def test_pending_form_has_no_approval(data):
    result = next(iter(data[2].values()))
    pending = pending_review(result)
    validate_schema(pending, 'review')
    assert pending['result_sha256'] == digest(result)
    assert pending['status'] == 'pending' and pending['reviewers'] == []
    assert not pending['segmentation_complete'] and pending['claims'] == []


def test_packets_are_allowlisted_and_preserve_answer(data):
    cases, _, results, _, _ = data
    before = copy.deepcopy((cases, results))
    bundle = make_review_bundle(cases, list(results.values()))
    for packet in bundle['packets']:
        uuid.UUID(packet['review_id'])
        assert set(packet) == {'review_id', 'answer', 'request', 'profile', 'jd', 'context'}
        assert packet['answer'] == '40 hours'
        assert all(set(record) == {'record_id', 'text'} for record in packet['context'])
        assert bundle['adjudication_key'][packet['review_id']]['condition'] in ('B_naive_rag', 'C_verified_rag')
    assert len(bundle['reviews_pending']) == 2
    assert not bundle['paper_ready']
    assert (cases, results) == before


def test_failed_result_gets_pending_form_but_no_packet(data):
    results = list(data[2].values())
    results[0]['status'] = 'failed'
    bundle = make_review_bundle(data[0], results)
    assert len(bundle['packets']) == 1 and len(bundle['reviews_pending']) == 2


def test_bundle_rejects_duplicate_unknown_case_and_context(data):
    cases, _, results, _, _ = data
    result = next(iter(results.values()))
    with pytest.raises(ValueError, match='Duplicate'):
        make_review_bundle(cases, [result, result])
    invalid = dict(result, case_id='unknown')
    with pytest.raises(ValueError, match='Unknown'):
        make_review_bundle(cases, [invalid])
    invalid = dict(result, context_record_ids=['r2', 'r1'])
    with pytest.raises(ValueError, match='Context policy'):
        make_review_bundle(cases, [invalid])


def test_one_family_ci_null_and_metrics_companions(data):
    summary = summarize(data)
    primary = summary['paired']['metrics']['factual_correctness']
    assert primary['estimate'] == 1 and primary['ci95'] is None
    assert primary['complete_pairs'] == 1 and primary['family_count'] == 1
    assert summary['paired']['metrics']['coverage']['estimate'] == 0
    assert summary['paired']['metrics']['abstention_rate']['estimate'] == 0
    assert summary['by_condition']['B_naive_rag']['metrics']['evidence_eligibility']['unknown_rate']['value'] == 1
    assert summary['paper_ready'] is False


def test_family_weighting_and_bootstrap_are_reproducible():
    data = experiment([('a1', 'family-a', True, False),
                       ('a2', 'family-a', True, False),
                       ('b1', 'family-b', False, True)])
    first = summarize(data, bootstrap_samples=100, seed=71)
    second = summarize(data, bootstrap_samples=100, seed=71)
    metric = first['paired']['metrics']['factual_correctness']
    assert metric['estimate'] == 0  # Equal families, not the 1/3 case-weighted mean.
    assert metric['complete_pairs'] == 3 and metric['family_count'] == 2
    assert metric['ci95'] == [-1, 1]
    assert first == second


def test_planned_denominators_preserve_missing_failed_length_unreviewed():
    data = experiment([('a', 'family-a', True, False), ('b', 'family-b', True, False)])
    _, _, results, reviews, _ = data
    ids = list(results)
    del results[ids[0]], reviews[ids[0]]
    results[ids[1]]['status'] = 'failed'
    reviews[ids[1]] = pending_review(results[ids[1]])
    results[ids[2]]['finish_reason'] = 'length'
    reviews[ids[2]] = pending_review(results[ids[2]])
    del reviews[ids[3]]
    summary = summarize(data)
    assert summary['counts'] == dict(success=2, failed=1, missing=1, length=1,
                                     unreviewed=4, successful_unreviewed=2, approved=0)
    assert summary['planned_rates']['length']['value'] == .25
    metric = summary['paired']['metrics']['factual_correctness']
    assert metric['estimate'] is None and metric['excluded_pairs'] == 2
    assert metric['exclusion_reasons'] == {'missing_result': 1, 'missing_review': 1}


def test_zero_claim_abstention_remains_null(data):
    result = next(iter(data[2].values()))
    review, originals = review_fixture(result, abstain=True)
    data[3][result['run_id']] = review
    data[4].update(originals)
    summary = summarize(data)
    metric = summary['paired']['metrics']['factual_correctness']
    assert metric['estimate'] is None
    assert metric['exclusion_reasons'] == {'undefined_metric': 1}
    assert summary['paired']['metrics']['coverage']['complete_pairs'] == 1
    assert summary['paired']['metrics']['abstention_rate']['estimate'] == 1
    assert summary['response_summaries'][result['run_id']]['metrics']['factual_correctness']['value'] is None


def test_length_success_is_still_evaluated_unchanged(data):
    result = next(iter(data[2].values()))
    result['finish_reason'] = 'length'
    review, originals = review_fixture(result)
    data[3][result['run_id']] = review
    data[4].update(originals)
    out = summarize(data)
    assert out['counts']['length'] == 1
    assert out['paired']['metrics']['factual_correctness']['complete_pairs'] == 1


@pytest.mark.parametrize('field,value', [
    ('run_id', 'different'), ('case_id', 'different'),
    ('condition', 'A_no_rag'), ('seed', 99),
])
def test_result_identity_mismatch_is_rejected(data, field, value):
    next(iter(data[2].values()))[field] = value
    with pytest.raises(ValueError, match='planned identity'):
        summarize(data)


def test_duplicate_plan_and_unregistered_runs_are_rejected(data):
    invalid = copy.deepcopy(data)
    invalid[1].append(copy.deepcopy(invalid[1][0]))
    with pytest.raises(ValueError, match='Duplicate'):
        summarize(invalid)
    invalid = copy.deepcopy(data)
    invalid[2]['unregistered'] = next(iter(invalid[2].values()))
    with pytest.raises(ValueError, match='Unregistered'):
        summarize(invalid)


def test_review_hash_mismatch_is_rejected_even_pending(data):
    result = next(iter(data[2].values()))
    data[3][result['run_id']] = pending_review(result)
    data[3][result['run_id']]['result_sha256'] = 'a' * 64
    with pytest.raises(ValueError, match='binding mismatch'):
        summarize(data)


def test_original_files_must_exist_and_match_bytes(data):
    with pytest.raises(ValueError, match='Original review bytes'):
        summarize_experiment(*data[:4])
    key = next(iter(data[4]))
    data[4][key] += b'changed'
    with pytest.raises(ValueError, match='hash mismatch'):
        summarize(data)


def test_original_json_must_bind_reviewer_and_result(data):
    review = next(iter(data[3].values()))
    raw = json.dumps(dict(reviewer_id='wrong-person', result_sha256=review['result_sha256'])).encode()
    sha = hashlib.sha256(raw).hexdigest()
    review['reviewers'][0]['original_review_sha256'] = sha
    data[4][sha] = raw
    with pytest.raises(ValueError, match='Original reviewer/result binding'):
        summarize(data)


def test_second_person_cannot_reuse_first_original(data):
    review = next(iter(data[3].values()))
    review['reviewers'][1]['original_review_sha256'] = review['reviewers'][0]['original_review_sha256']
    with pytest.raises(ValueError, match='Independent original'):
        summarize(data)


def test_family_cannot_cross_splits():
    data = experiment([('a', 'same-family', True, False), ('b', 'same-family', True, False)])
    data[0][1]['split'] = 'test'
    with pytest.raises(ValueError, match='both dev and test'):
        summarize(data)


def test_no_planned_runs_and_disabled_bootstrap(data):
    empty = summarize_experiment([], [], {}, {})
    assert empty['planned_rates']['success']['value'] is None
    assert empty['paired']['metrics']['factual_correctness']['estimate'] is None
    assert summarize(data, bootstrap_samples=0)['paired']['metrics']['factual_correctness']['ci95'] is None
    with pytest.raises(ValueError, match='bootstrap_samples'):
        summarize(data, bootstrap_samples=-1)

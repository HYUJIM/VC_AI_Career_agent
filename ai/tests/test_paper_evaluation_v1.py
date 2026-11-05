from copy import deepcopy
import pytest
from src.rag.paper_evaluation_v1 import prepare, aggregate, rate, validate_review


def bundle():
    rows = [{'query_id': 'q', 'condition': c, 'run_id': c, 'generated_answer': '30시간 r1',
             'retrieved_contexts': [{'record_id': 'r1', 'snippet': '30시간', 'verification_status': 'verified'}],
             'execution': {'backend_details': {'finish_reason': 'length' if c.startswith('B') else 'eos'}}}
            for c in ('A_no_rag', 'B_naive_rag', 'C_verified_rag')]
    rows[0]['retrieved_contexts'] = []
    return prepare(rows, [{'query_id': 'q', 'subject_did': 'did:example:x', 'query_text': '시간?'}], [{'query_id': 'q'}])


def approved(b):
    r = deepcopy(next(r for r in b['reviews'] if next(k for k in b['adjudication_key'] if k['review_id'] == r['review_id'])['condition'] == 'B_naive_rag'))
    r.update(status='human_approved', reviewer_kind='human', reviewer_id='h1', reviewed_at='2026-10-08T12:00:00+09:00',
             second_reviewer_kind='human', second_reviewer_id='h2', second_reviewed_at='2026-10-08T13:00:00+09:00',
             behavior='answer', task_correct='yes', abstention_correct='yes', claim_segmentation_complete=True,
             claims=[{'claim_id': 'c1', 'text': '30시간', 'start': 0, 'end': 4, 'factual_correctness': 'yes',
                      'context_support': 'yes', 'evidence_eligibility': 'no', 'supporting_record_ids': ['r1'],
                      'citations': [{'record_id': 'r1', 'correct': 'yes'}], 'rationale': 'Synthetic test: content supported, policy ineligible.'}])
    return r


def test_pending_never_produces_zero_or_perfect_scores():
    b = bundle()
    s = aggregate(b, b['reviews'])
    assert not s['review_coverage_complete'] and not s['paper_ready']
    for v in s['conditions'].values():
        assert v['task_correct']['value'] is None
        assert v['human_approved'] == 0 and v['pending_or_missing'] == 1


def test_separates_support_eligibility_and_partial_coverage():
    b = bundle()
    s = aggregate(b, [approved(b)])
    v = s['conditions']['B_naive_rag']
    assert v['claim_context_unsupported']['value'] == 0
    assert v['claim_evidence_ineligible']['value'] == 1
    assert v['finish_reasons_all_results'] == {'length': 1}
    assert not s['review_coverage_complete']


@pytest.mark.parametrize('change', [
    {'reviewer_kind': 'agent'}, {'second_reviewer_id': 'h1'}, {'reviewed_at': '2026-10-08T12:00:00'},
    {'issues': ['unresolved']}, {'claim_segmentation_complete': False}, {'result_sha256': 'tampered'},
    {'behavior': 'unchecked'}, {'claims': []}, {'status': 'auto_approved'},
])
def test_invalid_approval_fails_closed(change):
    b = bundle()
    r = approved(b)
    r.update(change)
    with pytest.raises(ValueError):
        aggregate(b, [r])


@pytest.mark.parametrize('change', [
    {'text': '40시간'}, {'end': 99}, {'supporting_record_ids': ['unknown']},
    {'supporting_record_ids': []}, {'rationale': ''}, {'context_support': True},
    {'citations': [{'record_id': 'invented', 'correct': 'yes'}]},
])
def test_claim_provenance_is_checked(change):
    b = bundle()
    r = approved(b)
    r['claims'][0].update(change)
    with pytest.raises(ValueError):
        aggregate(b, [r])


def test_duplicate_unknown_reviews_and_no_mutation():
    b = bundle()
    old = deepcopy(b)
    r = approved(b)
    with pytest.raises(ValueError):
        aggregate(b, [r, r])
    r['review_id'] = 'alien'
    with pytest.raises(ValueError):
        aggregate(b, [r])
    assert b == old


def test_blinding_and_rates():
    b = bundle()
    assert b == bundle()
    for p in b['packets']:
        assert 'condition' not in p and 'model' not in p
        assert all('verification_status' not in c for c in p['contexts'])
    assert rate(['unknown', 'not_applicable'])['value'] is None
    assert rate(['yes', 'no', 'unknown']) == {'value': .5, 'numerator': 1, 'denominator': 2, 'unknown': 1, 'not_applicable': 0}


def test_prepare_rejects_missing_or_duplicate_pairs():
    q = [{'query_id': 'q', 'query_text': 'x', 'subject_did': 'did:example:x'}]
    row = {'query_id': 'q', 'condition': 'A_no_rag', 'run_id': 'r', 'generated_answer': '', 'retrieved_contexts': []}
    for rows in ([row], [row, row]):
        with pytest.raises(ValueError):
            prepare(rows, q, [{'query_id': 'q'}])

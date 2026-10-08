import pytest
from src.rag.backend_education_diagnostic import ROWS
from src.rag.backend_education_review import evaluate


def source():
    return {'subject_did': 'did:key:z6MkMockTestUser0013988b874', 'records': [
        {'record_id': rid, 'achievement': {'name': name, 'description': desc}}
        for rid, name, desc, _ in ROWS]}


def answer():
    return '\n'.join(f'{name} | {duration} | {rid}' for rid, name, _, duration in ROWS)


def test_exact_answer_and_separate_source_fields():
    r = evaluate(answer(), source(), 'eos')
    assert r['overall_status'] == 'passed'
    assert all(row['completion_hours'] is None for row in r['source_fields'])
    assert r['source_fields'][2]['course_duration']['value'] == 6


def test_na_is_ambiguous_not_hallucination_or_pass():
    r = evaluate(answer().replace('시간 미제공', 'N/A', 1), source(), 'eos')
    assert r['identity_and_completeness']['status'] == 'passed'
    assert r['duration_grounding']['status'] == 'needs_review'
    assert r['overall_status'] == 'needs_review'


def test_omitted_completion_suffix_is_wording_failure():
    r = evaluate(answer().replace('6기 수료', '6기'), source(), 'eos')
    assert r['identity_and_completeness']['status'] == 'passed'
    assert r['duration_grounding']['status'] == 'passed'
    assert r['wording_and_format']['status'] == 'failed'


@pytest.mark.parametrize('duration', ['6개월', '1년', '100시간'])
def test_wrong_record_duration_fails(duration):
    r = evaluate(answer().replace('시간 미제공', duration, 1), source(), 'eos')
    assert r['duration_grounding']['status'] == 'failed'


def test_existing_six_months_is_grounded_not_hours():
    r = evaluate(answer().replace('시간 미제공; 6개월 과정', '6개월'), source(), 'eos')
    assert r['duration_grounding']['status'] == 'passed'
    assert r['overall_status'] == 'needs_review'


@pytest.mark.parametrize('text,stop', [(answer(), 'length'), (answer().splitlines()[0], 'eos'), ('invalid', 'eos')])
def test_incomplete_or_malformed_never_passes(text, stop):
    assert evaluate(text, source(), stop)['identity_and_completeness']['status'] == 'failed'


def test_unknown_text_is_unassessable():
    r = evaluate(answer().replace('시간 미제공', '충분함', 1), source(), 'eos')
    assert r['duration_grounding']['status'] == 'needs_review'

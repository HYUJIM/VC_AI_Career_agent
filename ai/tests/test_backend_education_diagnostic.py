from copy import deepcopy
import pytest
from src.rag.backend_education_diagnostic import ROWS, reference, review


def fixture():
    return {'subject_did': 'did:key:z6MkMockTestUser0013988b874', 'records': [
        {'record_id': rid, 'achievement': {'name': name, 'description': desc}}
        for rid, name, desc, _ in ROWS]}


def answer():
    return '\n'.join(f'{name} | {duration} | {rid}' for rid, name, _, duration in ROWS)


def test_correct_complete_answer_passes():
    assert review(answer(), reference(fixture()), 'eos')['status'] == 'passed'


@pytest.mark.parametrize('mutation,code', [
    (lambda a: a.replace('시간 미제공', '6개월', 1), 'duration_mismatch'),
    (lambda a: a.replace(ROWS[0][0], ROWS[1][0], 1), 'name_mismatch'),
    (lambda a: a.replace(ROWS[0][0], 'unknown', 1), 'unknown_or_unselected_id'),
    (lambda a: '\n'.join(a.splitlines()[:2]), 'missing_id'),
    (lambda a: a + '\n' + a.splitlines()[0], 'duplicate_id'),
    (lambda a: '```json\n' + a + '\n```', 'format'),
])
def test_bad_claims_and_formats_fail(mutation, code):
    result = review(mutation(answer()), reference(fixture()), 'eos')
    assert result['status'] == 'failed'
    assert code in [e['code'] for e in result['errors']]
    assert result['answer_modified'] is False


def test_length_stop_fails_even_with_correct_text():
    assert review(answer(), reference(fixture()), 'length')['status'] == 'failed'


def test_source_change_requires_review():
    source = fixture()
    source['records'][0]['achievement']['description'] += ' 6개월'
    with pytest.raises(ValueError, match='source changed'): reference(source)


def test_wrong_subject_rejected():
    source = fixture()
    source['subject_did'] = 'other'
    with pytest.raises(ValueError, match='subject'): reference(source)

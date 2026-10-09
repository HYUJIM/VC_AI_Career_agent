from copy import deepcopy
import json
from pathlib import Path
import pytest

from src.rag.backend_education_display_v2 import build_display, render_html

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'data/backend_education_v1/cases.json').read_text(encoding='utf-8'))
DEV = [c for c in CASES['cases'] if c['split'] == 'development']


@pytest.mark.parametrize('case', DEV, ids=lambda c: c['case_id'])
def test_display_preserves_source_and_pending_review(case):
    source = deepcopy(case['export'])
    result = build_display(source)
    assert source == case['export']
    assert result['review_result']['human_adjudication'] is False
    assert result['model_answer'] == {'status': 'not_run', 'text': None}
    assert not result['paper_evaluation_eligible']
    assert not result['cryptographic_verification_performed']
    for original, row, expected in zip(source['records'], result['source_display'], case['expected']):
        assert row['name'] == original['achievement']['name']
        assert row['record_id'] == original['record_id']
        assert row['source_achievement'] == original['achievement']
        assert row['human_review']['status'] == 'pending'
        assert row['selection']['status'] == expected['selection']
        for key in ('course_duration', 'completion_hours'):
            assert {k: row[key][k] for k in ('value', 'unit', 'status')} == expected[key]
            for ev in row[key]['evidence']:
                assert original['achievement'][ev['path'].split('.')[1]][ev['start']:ev['end']] == ev['quote']


@pytest.mark.parametrize('mode', ['duplicate', 'cross_subject'])
def test_display_fails_closed(mode):
    source = deepcopy(DEV[0]['export'])
    if mode == 'duplicate':
        source['records'].append(deepcopy(source['records'][0]))
    else:
        source['records'][0]['subject_did'] = 'did:example:other'
    with pytest.raises(ValueError):
        build_display(source)


def test_html_escapes_untrusted_source_and_separates_model():
    source = deepcopy(DEV[0]['export'])
    source['records'][0]['achievement']['name'] = '<script>alert(1)</script> 교육 수료'
    response = build_display(source)
    original = deepcopy(response['source_display'])
    response['model_answer'] = {'status': 'unreviewed', 'text': '<img src=x onerror=alert(2)> 가짜 999시간'}
    html = render_html(response)
    assert '<script>' not in html and '<img' not in html
    assert '&lt;script&gt;' in html and '&lt;img' in html
    assert '제품 미연결' in html and '선별' in html
    assert response['source_display'] == original


def test_zero_missing_and_backend_status_are_independent():
    source = deepcopy(DEV[0]['export'])
    source['records'][0]['backend_status'] = 'invalid'
    source['records'][0]['achievement']['description'] = '교육 0시간 이수'
    row = build_display(source)['source_display'][0]
    assert row['completion_hours']['display'] == '0시간'
    assert row['course_duration']['value'] is None
    assert row['selection']['status'] == 'selected'


def test_all_record_states_visible():
    states = set()
    for case in DEV:
        result = build_display(case['export'])
        html = render_html(result)
        for row in result['source_display']:
            states.add(row['selection']['status'])
            assert row['record_id'] in html
    assert states == {'selected', 'rejected', 'review'}

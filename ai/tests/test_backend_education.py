"""Contract tests use development gold only; holdout is evaluated by the runner."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from src.rag.backend_education import structure, source_display, selection_metrics, field_metrics
from src.rag.backend_education_evaluation import evaluate_answer
from scripts.evaluate_backend_education import load_frozen

ROOT = Path(__file__).resolve().parents[1]
DATA, LOCK = load_frozen(ROOT)
DEV = [c for c in DATA['cases'] if c['split'] == 'development']


@pytest.mark.parametrize('case', DEV, ids=lambda c: c['case_id'])
def test_development_contract(case):
    result = structure(case['export'])
    assert selection_metrics(result['records'], case['expected'])['exact_states']
    assert field_metrics(result['records'], case['expected'])['status'] == 'passed'
    assert result['paper_evaluation_eligible'] is False


@pytest.mark.parametrize('probe', DATA['review_probes'])
def test_review_axes(probe):
    case = next(c for c in DEV if c['case_id'] == probe['case_id'])
    row = case['expected'][0]
    rid = row['record_id'] if probe['id_mode'] == 'same' else 'unknown'
    answer = f"{row['name']}{probe['name_suffix']} | {probe['duration']} | {probe['hours']} | {rid}"
    result = evaluate_answer(answer, case['expected'], probe['finish'])
    if probe['axis'] == 'passed':
        assert result['status'] == 'passed'
    else:
        assert result['axes'][probe['axis']]
    if probe['axis'] in ('wording', 'unknown'):
        assert not result['axes']['facts']


def test_source_retained_and_spans_reproduce_quotes():
    case = deepcopy(DEV[3])
    before = deepcopy(case['export'])
    result = structure(case['export'])
    assert case['export'] == before
    row = result['records'][0]
    assert row['name'] == before['records'][0]['achievement']['name']
    assert source_display(result)[0]['name'] == row['name']
    for field in ('selection', 'course_duration', 'completion_hours'):
        for e in row[field]['evidence']:
            text = row['source_achievement'][e['path'].split('.')[1]]
            assert text[e['start']:e['end']] == e['quote']


@pytest.mark.parametrize('mode', ['cross_subject', 'duplicate'])
def test_identity_is_fail_closed(mode):
    export = deepcopy(DEV[0]['export'])
    if mode == 'cross_subject':
        export['records'][0]['subject_did'] = 'did:example:alien'
    else:
        export['records'].append(deepcopy(export['records'][0]))
    with pytest.raises(ValueError):
        structure(export)


def test_backend_status_does_not_select_or_verify():
    export = deepcopy(DEV[0]['export'])
    export['records'][0]['backend_status'] = 'invalid'
    result = structure(export)
    assert result['records'][0]['selection']['status'] == 'selected'
    assert result['cryptographic_verification_performed'] is False


def test_unparseable_answer_is_unknown_and_missing():
    result = evaluate_answer('a completely different answer', DEV[0]['expected'], 'eos')
    assert result['axes']['unknown'] and result['axes']['completeness']
    assert not result['axes']['facts']


def test_empty_and_duplicate_outputs_fail_completeness():
    row = DEV[0]['expected'][0]
    answer = f"{row['name']} | 기간 미제공 | 시간 미제공 | {row['record_id']}"
    assert evaluate_answer('', DEV[0]['expected'], 'eos')['status'] == 'failed'
    assert any(e['code'] == 'duplicate_id' for e in evaluate_answer(answer+'\n'+answer, DEV[0]['expected'], 'eos')['axes']['completeness'])


def test_lock_rejects_changed_data(tmp_path):
    for path in LOCK['files']:
        dest = tmp_path/path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT/path).read_bytes())
    (tmp_path/'data/backend_education_v1/lock.json').write_text(json.dumps(LOCK))
    with (tmp_path/'data/backend_education_v1/cases.json').open('a') as stream:
        stream.write(' ')
    with pytest.raises(ValueError, match='Frozen'):
        load_frozen(tmp_path)


@pytest.mark.parametrize('description,status,value', [
    ('교육 0시간 이수', 'provided', 0),
    ('교육 10~20시간 이수', 'ambiguous', None),
    ('교육 매일 2시간 이수', 'ambiguous', None),
    ('교육 20시간 이수하지 않음', 'ambiguous', None),
])
def test_semantic_boundaries(description, status, value):
    export = deepcopy(DEV[0]['export'])
    export['records'][0]['achievement']['description'] = description
    row = structure(export)['records'][0]
    assert row['completion_hours']['status'] == status
    assert row['completion_hours']['value'] == value

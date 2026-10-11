"""Bounded runner contract tests: fake model, actual existing development drafts."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

from src.llm.transformers_llm import REVISION, measure_messages
from src.rag import paper_experiment as m
from src.rag.paper_context_renderer_v2 import CONDITIONS, render_abc
from src.rag.paper_evaluation_v2 import digest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/paper_experiment_v2'


class Tokenizer:
    def apply_chat_template(self, messages, **_):
        return json.dumps(messages, ensure_ascii=False)

    def __call__(self, texts, **_):
        return {'input_ids': [list(range(len(texts[0]) // 10))]}


class Model:
    calls = 0
    fail_on = None

    def __init__(self, **kwargs):
        self.tokenizer = Tokenizer()
        self.seed = kwargs['seed']
        self.last_generation = None
        self.runtime_metadata = dict(resolved_revision=REVISION, generation_config={
            'max_new_tokens': kwargs['max_new_tokens'], 'do_sample': False, 'num_beams': 1})

    def generate(self, messages):
        type(self).calls += 1
        if type(self).calls == self.fail_on:
            raise RuntimeError('simulated model failure')
        rendered, count = measure_messages(self.tokenizer, messages)
        self.last_generation = dict(messages=copy.deepcopy(messages), rendered_prompt=rendered,
                                    token_usage={'input_tokens': count, 'output_tokens': 8},
                                    finish_reason='length')
        return 'synthetic answer'


def plan(*drafts):
    return dict(version='paper-development-plan/1.0', drafts=list(drafts) or [
        'dev_drafts/dev-backend-001.draft.json'], generation_seeds=[42], order_seed=42, max_new_tokens=16)


@pytest.fixture
def locked():
    Model.calls, Model.fail_on = 0, None
    return m.prepare_experiment(plan(), DATA, Tokenizer(), fingerprint_fn=lambda: {})


def execute(locked, path):
    return m.execute_experiment(locked, path, Tokenizer(), Model, fingerprint_fn=lambda: {})


def resign(envelope):
    envelope['lock_sha256'] = digest(envelope['lock'])


def test_preflight_preserves_sources_and_context(locked):
    before = (DATA / plan()['drafts'][0]).read_bytes()
    lock = locked['lock']
    assert len(lock['planned_runs']) == 3
    assert set(r['condition'] for r in lock['planned_runs']) == set(CONDITIONS)
    payloads = lock['payloads']['dev-backend-001']
    assert payloads['A_no_rag']['context_record_ids'] == []
    assert payloads['B_naive_rag']['context_record_ids'] == payloads['C_verified_rag']['context_record_ids']
    assert m.sha(before) == lock['sources'][0]['sha256']
    assert before == (DATA / plan()['drafts'][0]).read_bytes()
    assert lock['paper_ready'] is False


def test_all_five_drafts_one_family():
    p = m.read_json(DATA / 'development_plan.example.json')
    envelope = m.prepare_experiment(p, DATA, Tokenizer(), fingerprint_fn=lambda: {})
    rows = envelope['lock']['planned_runs']
    assert len(rows) == 15
    assert len({r['family_id'] for r in rows}) == 1
    payloads = envelope['lock']['payloads']['dev-backend-002']
    assert len(payloads['B_naive_rag']['context_record_ids']) == 2
    assert len(payloads['C_verified_rag']['context_record_ids']) == 1


def test_order_is_reproducible(locked):
    again = m.prepare_experiment(plan(), DATA, Tokenizer(), fingerprint_fn=lambda: {})
    rows = lambda x: [(r['case_id'], r['condition'], r['seed']) for r in x['lock']['planned_runs']]
    assert rows(again) == rows(locked)
    assert again['lock']['planned_runs'][0]['run_id'] != locked['lock']['planned_runs'][0]['run_id']


@pytest.mark.parametrize('kind', ['digest', 'snapshot', 'prompt', 'plan', 'environment', 'seed'])
def test_reject_changed_lock(locked, kind):
    if kind == 'digest':
        locked['lock']['max_new_tokens'] = 8
    else:
        if kind == 'snapshot': locked['lock']['sources'][0]['text'] += ' '
        if kind == 'prompt': locked['lock']['payloads']['dev-backend-001']['A_no_rag']['input_tokens'] += 1
        if kind == 'plan': locked['lock']['planned_runs'].pop()
        if kind == 'environment': locked['lock']['fingerprint'] = {'changed': True}
        if kind == 'seed': locked['lock']['generation_seeds'] = [True]
        resign(locked)
    with pytest.raises(ValueError):
        m.validate_lock(locked, Tokenizer(), fingerprint_fn=lambda: {})


def test_preflight_overflow():
    class Huge(Tokenizer):
        def __call__(self, texts, **_):
            return {'input_ids': [list(range(2049))]}
    with pytest.raises(ValueError, match='no truncation'):
        m.prepare_experiment(plan(), DATA, Huge(), fingerprint_fn=lambda: {})


@pytest.mark.parametrize('mutation', ['case', 'family', 'test', 'jd'])
def test_invalid_case_identity(tmp_path, mutation):
    draft = m.read_json(DATA / plan()['drafts'][0])
    if mutation == 'case':
        m.write_json(tmp_path / 'first.json', draft)
    if mutation == 'family':
        m.write_json(tmp_path / 'first.json', draft)
        draft['case_id'] = 'second'
        draft['family_id'] = 'fake-independent-family'
    if mutation == 'test': draft['split'] = 'test'
    if mutation == 'jd': draft['jd']['text'] += 'changed'
    m.write_json(tmp_path / 'second.json', draft)
    paths = ['first.json', 'second.json'] if mutation in {'case', 'family'} else ['second.json']
    with pytest.raises(ValueError):
        m.prepare_experiment(plan(*paths), tmp_path, Tokenizer(), fingerprint_fn=lambda: {})


def test_execution_saves_answers_pending_reviews_and_length(locked, tmp_path):
    path = tmp_path / 'run'
    ledger = execute(locked, path)
    assert ledger['status'] == 'completed'
    assert ledger['counts'] == dict(planned=3, success=3, failed=0, missing=0, length=3)
    results = m.load_results(path, locked)
    pending = m.read_json(path / 'review/reviews_pending.json')
    packets = m.read_json(path / 'review/packets.json')
    assert Model.calls == 3
    assert len(packets) == 3
    assert all('condition' not in p and 'gold_status' not in p for p in packets)
    for rid, result in results.items():
        assert pending[rid]['status'] == 'pending'
        assert pending[rid]['result_sha256'] == digest(result)
    with pytest.raises(FileExistsError):
        execute(locked, path)
    assert Model.calls == 3


def test_fail_stop_preserves_success_and_missing(locked, tmp_path):
    Model.fail_on = 2
    path = tmp_path / 'run'
    ledger = execute(locked, path)
    assert ledger['counts'] == dict(planned=3, success=1, failed=1, missing=1, length=1)
    assert ledger['status'] == 'failed'
    assert Model.calls == 2
    results = m.load_results(path, locked)
    failed = [r for r in results.values() if r['status'] == 'failed'][0]
    assert failed['answer'] == '' and failed['output_tokens'] is None
    assert not (path / 'generation' / (failed['run_id'] + '.json')).exists()


def test_load_failure_retains_full_denominator(locked, tmp_path):
    def broken(**_):
        raise RuntimeError('CUDA unavailable')
    path = tmp_path / 'run'
    ledger = m.execute_experiment(locked, path, Tokenizer(), broken, fingerprint_fn=lambda: {})
    assert ledger['status'] == 'failed'
    assert ledger['counts']['missing'] == 3 and ledger['attempts'] == []


def test_max_runs_guard_does_not_start_model(locked, tmp_path):
    with pytest.raises(ValueError, match='maximum'):
        m.execute_experiment(locked, tmp_path / 'run', Tokenizer(), Model,
                             max_runs=2, fingerprint_fn=lambda: {})
    assert Model.calls == 0 and not (tmp_path / 'run').exists()


@pytest.mark.parametrize('mutation', ['answer', 'delete'])
def test_result_artifact_tampering_rejected(locked, tmp_path, mutation):
    path = tmp_path / 'run'
    execute(locked, path)
    artifact = next((path / 'results').glob('*.json'))
    if mutation == 'delete':
        artifact.unlink()  # Temporary unit-test output only.
    else:
        result = m.read_json(artifact)
        result['answer'] = 'changed'
        m.write_json(artifact, result, replace=True)
    with pytest.raises(ValueError):
        m.load_results(path, locked)


def formal_case(locked):
    """Synthetic metadata for binding tests only; no human-approved gold."""
    draft = json.loads(locked['lock']['sources'][0]['text'])
    case = {k: copy.deepcopy(draft[k]) for k in ('case_id', 'family_id', 'split', 'synthetic',
        'profile', 'jd', 'request', 'candidate_record_ids', 'eligible_record_ids', 'noise')}
    evidence = json.loads(render_abc(draft, max_new_tokens=16)['B_naive_rag']['messages'][1]['content'])['evidence']
    case.update(version='paper-case/2.0', records=[], reference_facts=[], allowed_fact_ids=[],
                required_fact_ids=[], closed_world=False, gold_status='pending',
                expected_behavior={c: 'mixed' for c in CONDITIONS})
    for r, text in zip(draft['source_records'], evidence):
        checks = [dict(detail='Synthetic binding test only', **c) if 'detail' not in c else c
                  for c in r['verification']['checks']]
        case['records'].append(dict(record_id=r['record_id'], original_id=r['source']['external_id'],
            subject_did=r['subject_did'], raw_sha256=r['raw_hash'], facts=[],
            text=json.dumps(text, ensure_ascii=False, sort_keys=True),
            verification=dict(status=r['verification']['status'], checks=checks,
                verifier_revision='synthetic-test', policy_id='synthetic-test', artifact_sha256='0'*64,
                suite='synthetic-test', reference_time='2026-10-11T00:00:00Z')))
    return case


def test_formal_case_bound_to_actual_generation(locked):
    case = formal_case(locked)
    assert case['case_id'] in m.bind_cases(locked['lock'], [case])
    case['records'][0]['text'] += ' not seen by model'
    with pytest.raises(ValueError, match='text/provenance'):
        m.bind_cases(locked['lock'], [case])
    with pytest.raises(ValueError, match='every planned case'):
        m.bind_cases(locked['lock'], [])


def test_cli_prepare_run_inspect_without_inference(tmp_path):
    spec = importlib.util.spec_from_file_location('experiment_cli', ROOT / 'scripts/run_paper_experiment.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    p = plan(str(DATA / plan()['drafts'][0]))
    path, lock, run = tmp_path / 'plan.json', tmp_path / 'lock.json', tmp_path / 'run'
    m.write_json(path, p)
    Model.calls, Model.fail_on = 0, None
    kwargs = dict(tokenizer_factory=Tokenizer, llm_factory=Model)
    assert cli.main(['prepare', '--plan', str(path), '--output', str(lock)], **kwargs) == 0
    assert Model.calls == 0
    assert cli.main(['run', '--lock', str(lock), '--output', str(run)], **kwargs) == 0
    assert cli.main(['inspect', '--run', str(run)], **kwargs) == 0
    cases, reviews = tmp_path / 'cases.json', run / 'review/reviews_pending.json'
    m.write_json(cases, [formal_case(m.read_json(lock))])
    assert cli.main(['aggregate', '--run', str(run), '--cases', str(cases), '--reviews', str(reviews),
                     '--output', str(tmp_path / 'report')], **kwargs) == 0
    report = m.read_json(tmp_path / 'report/summary.json')
    assert report['counts']['approved'] == 0 and report['paper_ready'] is False


def test_development_stop_preserves_full_plan_and_complete_abc(tmp_path):
    Model.calls, Model.fail_on = 0, None
    envelope = m.prepare_experiment(
        plan('dev_drafts/dev-backend-001.draft.json', 'dev_drafts/dev-backend-002.draft.json'),
        DATA, Tokenizer(), fingerprint_fn=lambda: {})
    before = copy.deepcopy(envelope)
    path = tmp_path / 'partial'
    ledger = m.execute_experiment(envelope, path, Tokenizer(), Model,
                                  fingerprint_fn=lambda: {}, stop_after=3)
    assert ledger['status'] == 'development_partial'
    assert ledger['requested_stop_after'] == 3
    assert ledger['stop_reason'] == 'requested_stop_after'
    assert ledger['counts'] == dict(planned=6, success=3, failed=0, missing=3, length=3)
    assert Model.calls == 3 and envelope == before
    assert m.read_json(path / 'execution_lock.json') == before
    assert ledger['planned_runs'] == before['lock']['planned_runs']
    results = m.load_results(path, envelope)
    assert {r['condition'] for r in results.values()} == set(CONDITIONS)
    assert len({r['case_id'] for r in results.values()}) == 1
    assert len(m.read_json(path / 'review/reviews_pending.json')) == 3


@pytest.mark.parametrize('limit', [0, 1, 2, 4, -3, True, 3.0, 6])
def test_invalid_development_stop_does_not_load_model(locked, tmp_path, limit):
    with pytest.raises(ValueError, match='complete A/B/C'):
        m.execute_experiment(locked, tmp_path / 'run', Tokenizer(), Model,
                             fingerprint_fn=lambda: {}, stop_after=limit)
    assert Model.calls == 0 and not (tmp_path / 'run').exists()


def test_stop_rejected_for_test_phase_before_model(locked, tmp_path, monkeypatch):
    # Isolate the phase guard; lock validation remains mandatory in real calls.
    monkeypatch.setattr(m, 'validate_lock', lambda *args, **kwargs: None)
    locked['lock']['split'] = 'test'
    with pytest.raises(ValueError, match='development-only'):
        m.execute_experiment(locked, tmp_path / 'run', Tokenizer(), Model,
                             fingerprint_fn=lambda: {}, stop_after=3)
    assert Model.calls == 0 and not (tmp_path / 'run').exists()


def test_failure_at_requested_stop_remains_failure(locked, tmp_path):
    Model.fail_on = 3
    ledger = m.execute_experiment(locked, tmp_path / 'run', Tokenizer(), Model,
                                  fingerprint_fn=lambda: {}, stop_after=3)
    assert ledger['status'] == 'failed'
    assert ledger['counts']['success'] == 2 and ledger['counts']['failed'] == 1


def test_cli_partial_run_returns_success_and_keeps_missing(tmp_path):
    spec = importlib.util.spec_from_file_location('partial_cli', ROOT / 'scripts/run_paper_experiment.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    p = plan(str(DATA / 'dev_drafts/dev-backend-001.draft.json'),
             str(DATA / 'dev_drafts/dev-backend-002.draft.json'))
    plan_path, lock_path, run_path = tmp_path/'plan.json', tmp_path/'lock.json', tmp_path/'run'
    m.write_json(plan_path, p)
    Model.calls, Model.fail_on = 0, None
    kwargs = dict(tokenizer_factory=Tokenizer, llm_factory=Model)
    assert cli.main(['prepare', '--plan', str(plan_path), '--output', str(lock_path)], **kwargs) == 0
    assert cli.main(['run', '--lock', str(lock_path), '--output', str(run_path),
                     '--max-runs', '6', '--stop-after', '3'], **kwargs) == 0
    assert Model.calls == 3
    assert m.read_json(run_path/'ledger.json')['counts']['missing'] == 3

"""Focused integration checks; no downloads, server, GPU, or inference."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from scripts.run_minimal_pipeline import main
from src.clients.backend_client import BackendClient
from src.data.backend_adapter import adapt_backend_response, make_export
from src.llm.transformers_llm import TransformersLLM, normalize_messages

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


class Tokenizer:
    def apply_chat_template(self, messages, **kwargs):
        assert kwargs == {'tokenize': False, 'add_generation_prompt': True}
        return json.dumps(messages, ensure_ascii=False)

    def __call__(self, texts, **kwargs):
        assert kwargs == {'add_special_tokens': False}
        return {'input_ids': [[1] * 100]}


class LLM:
    def __init__(self, **kwargs):
        assert kwargs['local_files_only'] is True
        self.runtime_metadata = {'test_double': True}
        self.tokenizer = Tokenizer()
        self.last_generation = None

    def generate(self, messages):
        assert [m['role'] for m in messages] == ['system', 'user']
        self.last_generation = {'messages': copy.deepcopy(messages),
                                'token_usage': {'input_tokens': 100, 'output_tokens': 2}}
        return 'Test double answer'


def tokenizer_factory(*args, **kwargs):
    assert kwargs['local_files_only'] is True
    assert kwargs['trust_remote_code'] is False
    return Tokenizer()


def forbidden(*args, **kwargs):
    pytest.fail('Model/tokenizer must not be loaded')


def be_args(tmp_path):
    response = ROOT / 'data/backend_demo/careers_response.json'
    payload = read(response)
    context = tmp_path / 'context.json'
    context.write_text(json.dumps({'subject_did': payload['user_profile']['did'],
                                  'profile': '지원자', 'jd': '백엔드 개발',
                                  'request': '근거를 요약하세요.'}), encoding='utf-8')
    return ['--backend-response', str(response), '--context', str(context),
            '--output', str(tmp_path / 'runs')], payload


def fixture_args(tmp_path, number=1):
    return ['--fixture-draft', str(ROOT / 'data/paper_experiment_v2/dev_drafts' /
                                  f'dev-backend-{number:03d}.draft.json'),
            '--output', str(tmp_path / 'runs')]


def failed_manifest(tmp_path):
    return read(next((tmp_path / 'runs').glob('*/manifest.json')))


def test_backend_never_promotes_verified_and_preserves_raw(tmp_path):
    args, payload = be_args(tmp_path)
    original = Path(args[1]).read_bytes()
    directory = main(args, tokenizer_factory=tokenizer_factory, llm_factory=LLM)
    manifest = read(directory / 'manifest.json')
    assert manifest['status'] == 'completed'
    assert manifest['completed_results'] == 3
    assert manifest['mock'] is True and manifest['inference_performed'] is False
    assert manifest['verification_basis'] == 'backend_status_only'
    assert manifest['paper_evaluation_eligible'] is False
    selected = manifest['selected_record_ids']
    assert selected['A_no_rag'] == selected['C_verified_rag'] == []
    assert len(selected['B_naive_rag']) == 3
    assert read(directory / 'backend/raw_response.json') == payload
    assert read(directory / 'backend/validation_report.json')['rejected_count'] == 1
    assert Path(args[1]).read_bytes() == original
    audit = read(directory / 'policy_audit.json')
    assert all(not r['eligible'] and 'verification_details_not_available' in r['reasons']
               for r in audit['records'])
    a = read(directory / 'A_no_rag.input.json')
    c = read(directory / 'C_verified_rag.input.json')
    assert a == c
    for result in directory.glob('*.result.json'):
        assert 'backend_status' not in json.dumps(read(result)['generation']['messages'])


@pytest.mark.parametrize('number', range(1, 6))
def test_existing_fixtures_connect_without_mutation(tmp_path, number):
    args = fixture_args(tmp_path, number)
    path = Path(args[1])
    original = path.read_bytes()
    draft = read(path)
    directory = main(args, tokenizer_factory=tokenizer_factory, llm_factory=LLM)
    manifest = read(directory / 'manifest.json')
    assert manifest['input_kind'] == 'signed_fixture'
    assert manifest['verification_basis'] == 'reported_checks_only'
    assert manifest['cryptographic_verification_performed'] is False
    assert manifest['completed_results'] == 3
    assert manifest['selected_record_ids']['B_naive_rag'] == draft['candidate_record_ids']
    assert manifest['selected_record_ids']['C_verified_rag'] == [
        rid for rid in draft['candidate_record_ids'] if rid in draft['eligible_record_ids']]
    assert path.read_bytes() == original


def test_backend_export_and_candidate_order(tmp_path):
    args, payload = be_args(tmp_path)
    records, _ = adapt_backend_response(payload, payload['user_profile']['did'])
    export = tmp_path / 'export.json'
    export.write_text(json.dumps(make_export(records, payload['user_profile']['did'])), encoding='utf-8')
    args[:2] = ['--backend-export', str(export)]
    selected = [records[2]['record_id'], records[0]['record_id']]
    for rid in selected:
        args += ['--record-id', rid]
    directory = main(args + ['--check-only'], tokenizer_factory=tokenizer_factory, llm_factory=forbidden)
    manifest = read(directory / 'manifest.json')
    assert manifest['selected_record_ids']['B_naive_rag'] == selected
    assert manifest['status'] == 'checked'
    assert not list(directory.glob('*.result.json'))


def test_live_client_contract_only_gets_careers(tmp_path):
    args, payload = be_args(tmp_path)
    args[:2] = ['--backend-url', 'http://localhost:3000']
    requests = []
    def handler(request):
        requests.append(request)
        assert request.method == 'GET'
        assert request.url.path == '/api/v1/subjects/' + payload['user_profile']['did'] + '/careers'
        return httpx.Response(200, json=payload)
    def client_factory(url):
        return BackendClient(url, transport=httpx.MockTransport(handler))
    directory = main(args + ['--check-only'], tokenizer_factory=tokenizer_factory,
                     llm_factory=forbidden, client_factory=client_factory)
    assert len(requests) == 1
    assert read(directory / 'backend/manifest.json')['source']['mode'] == 'http'


def test_backend_fetch_failure_is_saved(tmp_path):
    args, _ = be_args(tmp_path)
    args[:2] = ['--backend-url', 'http://localhost:3000']
    def client_factory(url):
        return BackendClient(url, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(httpx.HTTPStatusError):
        main(args, tokenizer_factory=forbidden, llm_factory=forbidden, client_factory=client_factory)
    assert failed_manifest(tmp_path)['stage'] == 'backend_fetch'


def test_subject_mismatch_blocks_before_model(tmp_path):
    args, _ = be_args(tmp_path)
    context_path = Path(args[3])
    context = read(context_path)
    context['subject_did'] = 'did:example:another'
    context_path.write_text(json.dumps(context), encoding='utf-8')
    with pytest.raises(ValueError, match='differs'):
        main(args, tokenizer_factory=forbidden, llm_factory=forbidden)
    assert failed_manifest(tmp_path)['status'] == 'failed'


def test_unknown_candidate_blocks_before_model(tmp_path):
    args, _ = be_args(tmp_path)
    with pytest.raises(ValueError, match='Unknown candidate'):
        main(args + ['--record-id', 'missing'], tokenizer_factory=forbidden, llm_factory=forbidden)
    assert failed_manifest(tmp_path)['stage'] == 'context_policy'


def test_changed_signature_cannot_use_stale_eligibility(tmp_path):
    args = fixture_args(tmp_path)
    draft = read(Path(args[1]))
    for check in draft['source_records'][0]['verification']['checks']:
        if check['check'] == 'signature':
            check['result'] = 'skip'
    changed = tmp_path / 'changed.json'
    changed.write_text(json.dumps(draft), encoding='utf-8')
    args[1] = str(changed)
    with pytest.raises(ValueError, match='eligibility disagrees'):
        main(args, tokenizer_factory=forbidden, llm_factory=forbidden)
    assert failed_manifest(tmp_path)['completed_results'] == 0


def test_all_conditions_preflight_before_first_generation(tmp_path):
    class Large(Tokenizer):
        def __call__(self, texts, **kwargs):
            messages = json.loads(texts[0])
            content = json.loads(messages[1]['content'])
            return {'input_ids': [[1] * (2049 if content['evidence'] else 100)]}
    with pytest.raises(ValueError, match='No truncation'):
        main(fixture_args(tmp_path) + ['--condition', 'A_no_rag'],
             tokenizer_factory=lambda *a, **k: Large(), llm_factory=forbidden)
    manifest = failed_manifest(tmp_path)
    assert manifest['stage'] == 'token_preflight'
    assert manifest['input_tokens']['B_naive_rag'] == 2049


def test_tokenizer_drift_blocks_generation(tmp_path):
    class Drift(LLM):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.tokenizer.apply_chat_template = lambda *a, **k: 'different'
        generate = forbidden
    with pytest.raises(ValueError, match='tokenization differ'):
        main(fixture_args(tmp_path), tokenizer_factory=tokenizer_factory, llm_factory=Drift)
    assert failed_manifest(tmp_path)['completed_results'] == 0


@pytest.mark.parametrize('stage', ['model_load', 'generation'])
def test_failure_preserves_manifest_and_completed_answers(tmp_path, stage):
    class Fail(LLM):
        def __init__(self, **kwargs):
            if stage == 'model_load':
                raise RuntimeError('intentional load failure')
            super().__init__(**kwargs)
            self.calls = 0
        def generate(self, messages):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError('intentional second generation failure')
            return super().generate(messages)
    with pytest.raises(RuntimeError):
        main(fixture_args(tmp_path), tokenizer_factory=tokenizer_factory, llm_factory=Fail)
    manifest = failed_manifest(tmp_path)
    assert manifest['status'] == 'failed' and manifest['stage'] == stage
    expected = 0 if stage == 'model_load' else 1
    assert manifest['completed_results'] == expected
    assert len(list((tmp_path / 'runs').glob('*/*.result.json'))) == expected


def test_repeated_runs_never_overwrite(tmp_path):
    args = fixture_args(tmp_path) + ['--condition', 'C_verified_rag']
    first = main(args, tokenizer_factory=tokenizer_factory, llm_factory=LLM)
    snapshot = {p.name: p.read_bytes() for p in first.iterdir()}
    second = main(args, tokenizer_factory=tokenizer_factory, llm_factory=LLM)
    assert first != second
    assert snapshot == {p.name: p.read_bytes() for p in first.iterdir()}
    assert read(second / 'manifest.json')['completed_results'] == 1


def test_qwen_interface_preserves_roles_before_budget_guard():
    messages = [{'role': 'system', 'content': 'Policy'}, {'role': 'user', 'content': 'Question'}]
    class Large(Tokenizer):
        def apply_chat_template(self, value, **kwargs):
            assert value == messages
            return super().apply_chat_template(value, **kwargs)
        def __call__(self, *args, **kwargs):
            return {'input_ids': SimpleNamespace(shape=(1, 2049))}
    llm = TransformersLLM.__new__(TransformersLLM)
    llm.torch = object()
    llm.tokenizer = Large()
    llm.max_input_tokens = 2048
    with pytest.raises(ValueError, match='No truncation'):
        llm.generate(messages)
    assert llm.last_generation is None
    assert normalize_messages('Legacy prompt') == [{'role': 'user', 'content': 'Legacy prompt'}]


@pytest.mark.parametrize('messages', [[], '', [{'role': 'assistant', 'content': 'x'}],
                                    [{'role': 'user', 'content': None}]])
def test_bad_messages_rejected(messages):
    with pytest.raises(ValueError):
        normalize_messages(messages)

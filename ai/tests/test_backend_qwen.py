import json
from pathlib import Path
import pytest
from scripts.run_backend_qwen import main, measure
from src.data.backend_adapter import adapt_backend_response, make_export


class Tokenizer:
    def __init__(self, count=100): self.count = count
    def apply_chat_template(self, messages, **kwargs):
        assert kwargs == dict(tokenize=False, add_generation_prompt=True)
        return 'CHAT:' + messages[0]['content']
    def __call__(self, texts, **kwargs):
        assert kwargs == dict(add_special_tokens=False)
        return {'input_ids': [[1] * self.count]}


def setup_args(tmp_path):
    payload = json.loads((Path(__file__).resolve().parents[1] / 'data/backend_demo/careers_response.json').read_text(encoding='utf-8'))
    did = payload['user_profile']['did']
    records, _ = adapt_backend_response(payload, did)
    export = tmp_path / 'export.json'
    export.write_text(json.dumps(make_export(records, did)), encoding='utf-8')
    return ['--export', str(export), '--question', '교육?', '--output', str(tmp_path / 'runs')], records


def test_over_budget_never_loads_model(tmp_path):
    args, _ = setup_args(tmp_path)
    def forbidden(**kwargs): raise AssertionError('Model must not load')
    with pytest.raises(ValueError, match='2049'):
        main(args, lambda *a, **k: Tokenizer(2049), forbidden)
    manifest = json.loads(next((tmp_path / 'runs').glob('*/manifest.json')).read_text())
    assert manifest['inference_performed'] is False
    assert manifest['stage'] == 'token_preflight'


def test_check_only_and_selection_audit(tmp_path):
    args, records = setup_args(tmp_path)
    directory = main(args + ['--check-only', '--record-id', records[0]['record_id']], lambda *a, **k: Tokenizer())
    result = json.loads((directory / 'evidence.json').read_text(encoding='utf-8'))
    assert sum(r['selected'] for r in result['selection_audit']) == 1
    assert len(result['selection_audit']) == len(records)
    assert not (directory / 'answer.txt').exists()


def test_success_saves_real_metadata_contract(tmp_path):
    args, _ = setup_args(tmp_path)
    class LLM:
        def __init__(self, **kwargs):
            self.tokenizer = Tokenizer()
            self.runtime_metadata = {'test_double': True}
            self.last_generation = {'token_usage': {'input_tokens': 100, 'output_tokens': 2}}
        def generate(self, prompt): return 'test answer'
    directory = main(args, lambda *a, **k: Tokenizer(), LLM)
    result = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
    assert result['generation']['token_usage']['output_tokens'] == 2
    assert result['paper_evaluation_eligible'] is False


def test_model_failure_saved(tmp_path):
    args, _ = setup_args(tmp_path)
    def fail(**kwargs): raise RuntimeError('CUDA unavailable')
    with pytest.raises(RuntimeError, match='CUDA'):
        main(args, lambda *a, **k: Tokenizer(), fail)
    manifest = json.loads(next((tmp_path / 'runs').glob('*/manifest.json')).read_text())
    assert manifest['stage'] == 'model_load'
    assert manifest['inference_performed'] is False


def test_unknown_id_fails_before_tokenizer(tmp_path):
    args, _ = setup_args(tmp_path)
    with pytest.raises(ValueError, match='unknown'):
        main(args + ['--record-id', 'missing'], lambda *a, **k: pytest.fail('must not tokenize'))

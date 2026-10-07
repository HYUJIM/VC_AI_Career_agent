"""Control-flow tests use a fake backend, not GPU inference."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from src.llm.transformers_llm import TransformersLLM, REVISION, validate_settings

ROOT = Path(__file__).resolve().parents[1]

def runner():
    spec=importlib.util.spec_from_file_location('tf_runner_test', ROOT/'scripts/run_transformers_experiment.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class FakeBackend:
    def __init__(self, **kwargs):
        assert kwargs['revision']==REVISION
        assert kwargs['local_files_only'] is True
        self.runtime_metadata={'test_double': True}
        self.last_generation=None
    def generate(self, prompt):
        self.last_generation={'token_usage': {'input_tokens':10,'output_tokens':3,'total_tokens':13}}
        return 'TEST DOUBLE RESPONSE'

def configure(monkeypatch,tmp_path,backend):
    mod=runner()
    monkeypatch.setattr(mod,'TransformersLLM',backend)
    monkeypatch.setattr('sys.argv',['run_transformers_experiment.py','--output',str(tmp_path)])
    return mod

def test_all_conditions_saved_with_backend_metadata(monkeypatch,tmp_path):
    configure(monkeypatch,tmp_path,FakeBackend).main()
    path=next(tmp_path.glob('run-/*/manifest.json'))
    manifest=json.loads(path.read_text(encoding="utf-8"))
    assert manifest['status']=='completed' and manifest['completed_results']==36
    assert manifest['runtime']['test_double'] is True
    results=[json.loads(p.read_text(encoding="utf-8")) for p in path.parent.glob('*.json') if p.name!='manifest.json']
    assert len(results)==36
    for r in results:
        assert r['execution']['token_usage']['total_tokens']==13
        index=int(r['query_id'][-2:])
        expected={'A_no_rag':0,'B_naive_rag':2 if index==2 else 1,'C_verified_rag':1 if index in (1,2,6) else 0}
        assert len(r['retrieved_contexts'])==expected[r['condition']]

def test_failure_preserves_completed_result(monkeypatch,tmp_path):
    class FailSecond(FakeBackend):
        calls=0
        def generate(self,prompt):
            self.calls+=1
            if self.calls==2: raise RuntimeError('intentional generation failure')
            return super().generate(prompt)
    with pytest.raises(RuntimeError): configure(monkeypatch,tmp_path,FailSecond).main()
    path=next(tmp_path.glob('run-/*/manifest.json'))
    m=json.loads(path.read_text(encoding="utf-8"))
    assert m['status']=='failed' and m['completed_results']==1
    assert m['failed_condition']=='B_naive_rag' and m['failed_stage']=='inference'
    assert len(list(path.parent.glob('*.json')))==2

def test_model_load_failure_is_recorded(monkeypatch,tmp_path):
    class FailLoad:
        def __init__(self,**kwargs): raise RuntimeError('intentional load failure')
    with pytest.raises(RuntimeError): configure(monkeypatch,tmp_path,FailLoad).main()
    m=json.loads(next(tmp_path.glob('run-/*/manifest.json')).read_text(encoding="utf-8"))
    assert m['status']=='failed' and m['failed_stage']=='load_model'
    assert m['completed_results']==0 and m['inference_performed'] is False

@pytest.mark.parametrize('revision,out_tokens,in_tokens', [('main',128,2048),(REVISION,257,2048),(REVISION,128,2049)])
def test_invalid_settings_rejected(revision,out_tokens,in_tokens):
    with pytest.raises(ValueError): validate_settings(revision,out_tokens,in_tokens)

def test_long_input_fails_without_silent_truncation():
    class Tokenizer:
        def apply_chat_template(self,*a,**kw): return 'rendered'
        def __call__(self,*a,**kw):
            assert 'truncation' not in kw
            return {'input_ids':SimpleNamespace(shape=(1,2049))}
    llm=TransformersLLM.__new__(TransformersLLM)
    llm.torch=object()
    llm.tokenizer=Tokenizer()
    llm.max_input_tokens=2048
    llm.last_generation={'stale':True}
    with pytest.raises(ValueError,match='No truncation'): llm.generate('long input')
    assert llm.last_generation is None

import json
from pathlib import Path
import pytest
from src.config import ModelConfig
from src.data.credential_loader import load_credentials
from src.data.query_loader import load_queries
from src.llm.local_llm import MockLLM
from src.rag.pipeline import Pipeline, snippet
from src.rag.retriever import FixedCandidateRetriever

DATA = Path(__file__).resolve().parents[1] / 'data/research_stage1'

def fixture():
    return load_credentials(DATA/'credentials.json'), load_queries(DATA/'queries.jsonl')

def test_real_stage1_candidates_and_prompt_boundary():
    records, queries = fixture()
    retriever = FixedCandidateRetriever(records, queries, DATA/'candidate_sets.jsonl', 2)
    pipeline = Pipeline(retriever, MockLLM(), ModelConfig('mock','v1',0,1,1),2)
    for q in queries:
        q['gold_answer'] = 'DO_NOT_LEAK_GOLD'
        a,b,c = pipeline.run_all(q)
        index = int(q['query_id'][-2:])
        assert len(b['retrieved_contexts']) == (2 if index == 2 else 1)
        assert len(c['retrieved_contexts']) == (1 if index in (1,2,6) else 0)
        assert a['execution']['candidate_record_ids'] == []
        assert b['execution']['candidate_record_ids'] == c['execution']['candidate_record_ids']
        assert not b['execution']['excluded_records']
        assert len(c['execution']['excluded_records']) == len(b['retrieved_contexts'])-len(c['retrieved_contexts'])
        for result in (a,b,c):
            prompt = result['execution']['prompt']
            for forbidden in ('DO_NOT_LEAK_GOLD','trust_level','verification','gold_answer','answerable'):
                assert forbidden not in prompt
            assert result['execution']['generation_seconds'] >= 0
        assert 'issued_at' in b['execution']['prompt']
        assert 'expires_at' in b['execution']['prompt']

def test_nested_metadata_excluded():
    records,_ = fixture()
    record = records[0]
    record['achievement']['verification'] = 'SECRET_STATUS'
    record['evidence'] = [{'name':'evidence', 'verification':'SECRET_STATUS'}]
    content = snippet(record)
    assert 'SECRET_STATUS' not in content
    assert 'trust_level' not in content
    assert record['achievement']['description'] in content

@pytest.mark.parametrize('kind', ['missing','duplicate_query','duplicate_record','unknown_record','cross_subject','truncation'])
def test_bad_candidates_rejected_before_inference(tmp_path, kind):
    records,queries = fixture()
    rows = [json.loads(line) for line in (DATA/'candidate_sets.jsonl').read_text(encoding="utf-8").splitlines()]
    top_k = 2
    if kind=='missing': rows.pop()
    elif kind=='duplicate_query': rows.append(rows[0])
    elif kind=='duplicate_record': rows[0]['record_ids'] *= 2
    elif kind=='unknown_record': rows[0]['record_ids'] = ['unknown']
    elif kind=='cross_subject': rows[0]['record_ids'] = [records[-1]['record_id']]
    elif kind=='truncation': top_k = 1
    path=tmp_path/'candidates.jsonl'
    path.write_text('\n'.join(json.dumps(r) for r in rows), encoding="utf-8")
    with pytest.raises(ValueError): FixedCandidateRetriever(records,queries,path,top_k)

def test_completed_condition_survives_next_failure():
    records,queries = fixture()
    retriever=FixedCandidateRetriever(records,queries,DATA/'candidate_sets.jsonl',2)
    class FailingLLM:
        count=0
        def generate(self,prompt):
            self.count += 1
            if self.count == 2: raise RuntimeError('intentional')
            return 'first completed response'
    pipeline=Pipeline(retriever,FailingLLM(),ModelConfig('test','v1',0,1,1),2)
    iterator=pipeline.iter_all(queries[0])
    assert next(iterator)['generated_answer']=='first completed response'
    with pytest.raises(RuntimeError): next(iterator)

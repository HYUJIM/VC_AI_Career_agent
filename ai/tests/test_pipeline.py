import copy
import json
from pathlib import Path
import pytest
from jsonschema import ValidationError
from src.config import ModelConfig
from src.data.credential_loader import load_credentials
from src.data.query_loader import load_queries
from src.rag.pipeline import Pipeline
from src.rag.retriever import ListRetriever
from src.utils.schema_validator import validate

ROOT = Path(__file__).resolve().parents[1]
class SpyLLM:
    def __init__(self): self.prompts=[]
    def generate(self, prompt):
        self.prompts.append(prompt)
        return "test response"

def setup():
    records=load_credentials(ROOT/'data/mock/credentials.json')
    query=load_queries(ROOT/'data/experiment_queries.jsonl')[0]
    llm=SpyLLM()
    return Pipeline(ListRetriever(records),llm,ModelConfig('test','v1',0,7,20),6),query,llm,records

def test_conditions_and_gold_leakage():
    p,q,llm,records=setup()
    q.update(gold_answer='SECRET_GOLD',gold_record_ids=['SECRET_ID'],notes='SECRET_NOTES')
    results=p.run_all(q)
    assert results[0]['retrieved_contexts']==[]
    assert len(results[1]['retrieved_contexts'])==6
    assert {c['verification_status'] for c in results[2]['retrieved_contexts']}=={'verified'}
    assert len(results[2]['retrieved_contexts'])==1
    assert all(r['model']==results[0]['model'] for r in results)
    for prompt in llm.prompts:
        assert all(s not in prompt for s in ['SECRET_GOLD','SECRET_ID','SECRET_NOTES','answerable','verification_status'])
    assert 'Python 전문가' in llm.prompts[1]
    assert 'Python 전문가' not in llm.prompts[2]
    for result in results: validate(result,'rag-run-result')

def test_a_never_retrieves():
    p,q,_,_=setup()
    class Forbidden:
        def search(self,*args): raise AssertionError('retrieval called')
    p.retriever=Forbidden()
    assert p.run_query(q,'A_no_rag')['retrieved_contexts']==[]

def test_schema_rejects_invalid_enum_and_date():
    p,q,_,records=setup()
    q['query_type']='random'
    with pytest.raises(ValidationError): p.run_all(q)
    records[0]['normalized_at']='not-a-date'
    with pytest.raises(ValidationError): validate(records[0],'credential-record')

def test_subject_isolation_and_no_refill():
    p,q,_,records=setup()
    other=copy.deepcopy(records[0]); other['subject_did']='did:example:other'
    with pytest.raises(ValueError): p.run_query(q,'B_naive_rag',[other])
    p.retriever=ListRetriever([other, records[1], records[0]])
    p.top_k=1
    results=p.run_all(q)
    assert len(results[1]['retrieved_contexts'])==1
    assert results[2]['retrieved_contexts']==[]

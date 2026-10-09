import importlib.util
import json
from pathlib import Path
import pytest
from jsonschema import ValidationError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('paper_v2', ROOT/'src/rag/paper_evaluation_v2.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

@pytest.fixture
def data():
    fact = dict(fact_id='f',entity_id='e',attribute='hours',value=30,unit='hour',time_scope=None,source_ids=['source'])
    case = dict(version='paper-case/2.0',case_id='c',family_id='family',split='dev',synthetic=True,
        profile=dict(subject_did='did:test',text='synthetic'),jd=dict(jd_id='j',text='JD',sha256='0'*64),request='portfolio',
        records=[],eligible_record_ids=[],reference_facts=[fact],allowed_fact_ids=['f'],required_fact_ids=['f'],closed_world=False,
        noise=dict(kind='none',target_ratio=0,raw_record_ids=[]),candidate_record_ids=[],
        expected_behavior={c:'abstain' for c in ['A_no_rag','B_naive_rag','C_verified_rag']},gold_status='human_approved')
    result = dict(version='paper-result/2.0',run_id='r',case_id='c',condition='B_naive_rag',seed=42,status='success',finish_reason='eos',answer='40 hours',context_record_ids=[],input_tokens=None,output_tokens=None)
    claim = dict(claim_id='c1',canonical_key='e:hours:40',spans=[dict(start=0,end=8,text='40 hours')],entity_id='e',attribute='hours',normalized_value=40,unit='hour',time_scope=None,assertion_kind='fact',factual_correctness='no',context_support='no',evidence_eligibility='unknown',citation_correct='not_applicable',error_type='O',upward='yes',jd_aligned='yes',supporting_record_ids=[],reference_fact_ids=['f'],rationale='30 in T')
    review = dict(version='paper-review/2.0',result_sha256=m.digest(result),status='human_approved',reviewers=[dict(kind='human',reviewer_id=x,reviewed_at='2026-10-09T12:00:00+09:00',original_review_sha256='1'*64) for x in ['one','two']],segmentation_complete=True,issues=[],adjudication_rationale='resolved',behavior='answer',task_correct='no',abstention_correct='no',covered_required_fact_ids=[],claims=[claim])
    return case,result,review

def test_counts(data):
    out=m.summarize_response(*data)
    assert out['metrics']['factual_correctness']['value']==1
    assert out['metrics']['evidence_eligibility']['value'] is None
    assert out['error_counts']['O']==1 and out['D_JD_subset_of_F']==0
    assert out['paper_ready'] is False

def test_jd_subset_not_added(data):
    data[2]['claims'][0]['error_type']='F'
    out=m.summarize_response(*data)
    assert out['error_counts']['F']==1 and out['D_JD_subset_of_F']==1

def test_pending_no_scores(data):
    data[2]['status']='pending'
    assert m.summarize_response(*data)['metrics'] is None

def test_abstain_empty_null(data):
    data[2].update(behavior='abstain',claims=[])
    assert m.summarize_response(*data)['metrics']['factual_correctness']['value'] is None

@pytest.mark.parametrize('mutation', ['duplicate','span','hash','agent','same_person','date','issues','gold','failure','context','reference','coverage'])
def test_reject_invalid(data, mutation):
    c,r,v=data
    if mutation=='duplicate': v['claims'].append(dict(v['claims'][0],claim_id='c2'))
    if mutation=='span': v['claims'][0]['spans'][0]['end']=99
    if mutation=='hash': v['result_sha256']='a'*64
    if mutation=='agent': v['reviewers'][0]['kind']='agent'
    if mutation=='same_person': v['reviewers'][1]['reviewer_id']='one'
    if mutation=='date': v['reviewers'][0]['reviewed_at']='2026-10-09T12:00:00'
    if mutation=='issues': v['issues']=['unresolved']
    if mutation=='gold': c['gold_status']='pending'
    if mutation=='failure': r['status']='failed'; v['result_sha256']=m.digest(r)
    if mutation=='context': r['context_record_ids']=['x']; v['result_sha256']=m.digest(r)
    if mutation=='reference': v['claims'][0]['reference_fact_ids']=[]
    if mutation=='coverage': v['covered_required_fact_ids']=['f']
    with pytest.raises((ValueError, ValidationError)): m.summarize_response(c,r,v)

def test_noise():
    assert m.exposure([],['noise'])['value'] is None
    assert m.exposure(['a','b'],['b'])['value']==0.5
    with pytest.raises(ValueError): m.exposure(['a','a'],[])

def test_schema_and_blank_forms():
    for name in ['case','review','result','judge']:
        schema=json.loads((m.SCHEMAS/f'{name}.schema.json').read_text())
        m.Draft202012Validator.check_schema(schema)
    for name in ['case','review','judge']:
        value=json.loads((m.SCHEMAS/f'{name}.blank.json').read_text())
        with pytest.raises(ValidationError): m.validate_schema(value,name)

def test_judge_not_review():
    with pytest.raises(ValidationError): m.validate_schema({'version':'paper-judge/2.0'},'review')

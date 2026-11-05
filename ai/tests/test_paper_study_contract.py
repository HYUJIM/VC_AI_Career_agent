"""Synthetic contract fixtures only; no actual human approval or model inference."""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError
from src.rag import paper_evaluation_v2 as evaluator
from src.rag import paper_study as study
from src.rag.paper_experiment_review import pending_review, summarize_experiment
from test_paper_experiment_review import case_fixture, result_fixture, review_fixture


@pytest.fixture
def single():
    case=case_fixture()
    case.update(version='paper-case/2.1',gold_origin='synthetic_world',gold_source_hashes=['a'*64],
                lineage_id='test-only',retained_fact_ids=['fact'],closed_world_scope=['education:hours'],
                gold_review=dict(reviewer_id='synthetic-one',reviewed_at='2026-10-11T00:00:00Z',
                                 original_review_sha256='b'*64,scope_sha256='a'*64))
    result=result_fixture(case,'B_naive_rag')
    review, originals = review_fixture(result)
    review.update(version='paper-review/2.1',review_policy=study.POLICY)
    review['reviewers']=review['reviewers'][:1]
    for claim in review['claims']:
        claim.update(cited_record_ids=[],used_ineligible_record_ids=[],ineligible_use_rationale='',citation_correct='no')
    return case,result,review,originals


def test_explicit_single_policy_and_old_default(single):
    case,result,review,_=single
    with pytest.raises(ValueError,match='policy'):
        evaluator.summarize_response(case,result,review)
    value=evaluator.summarize_response(case,result,review,review_policy=study.POLICY)
    assert value['metrics']['factual_correctness']['value']==1
    assert value['metrics']['citation_correct']['value']==0
    assert value['paper_ready'] is False


def test_true_but_ineligible_is_not_factual_error(single):
    case,result,review,_=single
    claim=review['claims'][0]
    claim.update(factual_correctness='yes',context_support='yes',supporting_record_ids=['r2'],
                 evidence_eligibility='no',cited_record_ids=['r2'],citation_correct='yes',
                 used_ineligible_record_ids=['r2'],ineligible_use_rationale='Synthetic test: provided ineligible source',
                 error_type='none')
    value=evaluator.summarize_response(case,result,review,review_policy=study.POLICY)
    assert value['metrics']['factual_correctness']['value']==0
    assert value['metrics']['evidence_eligibility']['value']==1


@pytest.mark.parametrize('mutation',['fake_citation','missing_citation','unprovided_ineligible','no_use_reason','eligible_without_source','wrong_policy','agent'])
def test_reject_inconsistent_new_annotations(single,mutation):
    case,result,review,_=single;claim=review['claims'][0]
    if mutation=='fake_citation':claim.update(cited_record_ids=['imaginary'],citation_correct='yes')
    if mutation=='missing_citation':claim['citation_correct']='not_applicable'
    if mutation=='unprovided_ineligible':claim.update(used_ineligible_record_ids=['imaginary'],evidence_eligibility='no',ineligible_use_rationale='x')
    if mutation=='no_use_reason':claim.update(used_ineligible_record_ids=['r2'],evidence_eligibility='no')
    if mutation=='eligible_without_source':claim['evidence_eligibility']='yes'
    if mutation=='wrong_policy':review['review_policy']='dual_human_v2'
    if mutation=='agent':review['reviewers'][0]['kind']='agent'
    with pytest.raises((ValueError, ValidationError)):
        evaluator.summarize_response(case,result,review,review_policy=study.POLICY)


def test_zero_claims_stay_undefined(single):
    case,result,review,_=single
    review.update(claims=[],behavior='abstain',covered_required_fact_ids=[])
    out=evaluator.summarize_response(case,result,review,review_policy=study.POLICY)
    assert out['metrics']['factual_correctness']['value'] is None
    assert out['metrics']['coverage_retained']['value']==0


def test_original_review_bytes_still_required(single):
    case,result,review,originals=single
    row={k:result[k] for k in ('run_id','case_id','condition','seed')}
    row.update(family_id=case['family_id'],order_index=0)
    with pytest.raises(ValueError,match='Original review bytes'):
        summarize_experiment([case],[row],{result['run_id']:result},{result['run_id']:review},review_policy=study.POLICY)
    report=summarize_experiment([case],[row],{result['run_id']:result},{result['run_id']:review},
                                review_policy=study.POLICY,original_reviews=originals)
    assert report['counts']['approved']==1


def test_declarations_scope_and_bytes():
    value=dict(kind='human',reviewer_id='synthetic-test-only',reviewed_at='2026-10-11T00:00:00Z',
               scope_sha256='a'*64,decision='approved')
    text=json.dumps(value);artifact=dict(text=text,sha256=hashlib.sha256(text.encode()).hexdigest())
    assert study.human_declaration(artifact,'a'*64)['reviewer_id']=='synthetic-test-only'
    with pytest.raises(ValueError):study.human_declaration(artifact,'b'*64)
    artifact['text']+=' '
    with pytest.raises(ValueError):study.human_declaration(artifact,'a'*64)


def test_seed_world_distinct_lineages_and_truth_inventory():
    path=Path(__file__).resolve().parents[1]/'scripts/prepare_paper_study.py'
    spec=importlib.util.spec_from_file_location('prepare_study_cli',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    world=module.seed_world()
    assert world['human_review_status']=='pending'
    assert len({f['lineage_id'] for f in world['families']})==10
    assert len([f for f in world['families'] if f['split']=='test'])==8
    assert all(len(f['required_fact_ids'])==3 and len(f['retained_fact_ids'])==2 for f in world['families'])


def test_repeat_slots_not_chosen_from_successful_results():
    cases=[];rows=[]
    for i in range(8):
        for kind in study.STRATA:
            cid=f'{i}-{kind}';cases.append(dict(case_id=cid,noise={'kind':kind}))
            for condition in ('A_no_rag','B_naive_rag','C_verified_rag'):
                rows.append(dict(run_id=cid+condition,case_id=cid,condition=condition))
    slots=study.repeat_slots(cases,rows)
    assert len(slots)==36 and len(set(slots))==36
    assert slots==study.repeat_slots(cases,rows)


def test_primary_equal_family_weights_and_incomplete_family_exclusion(single,monkeypatch):
    cases=[];rows=[];results={};reviews={};originals={}
    for family in range(2):
        for kind in study.PRIMARY:
            case=copy.deepcopy(single[0]);case.update(case_id=f'{family}-{kind}',family_id=f'family-{family}')
            case['noise']['kind']=kind;cases.append(case)
            for condition in ('B_naive_rag','C_verified_rag'):
                result=result_fixture(case,condition)
                review,raw=review_fixture(result,error=family==0 and condition=='B_naive_rag')
                review.update(version='paper-review/2.1',review_policy=study.POLICY)
                review['reviewers']=review['reviewers'][:1]
                for claim in review['claims']:
                    claim.update(cited_record_ids=[],used_ineligible_record_ids=[],ineligible_use_rationale='',citation_correct='no')
                rid=result['run_id'];results[rid]=result;reviews[rid]=review;originals.update(raw)
                rows.append(dict(run_id=rid,case_id=case['case_id'],family_id=case['family_id'],condition=condition,seed=42,order_index=len(rows)))
    monkeypatch.setattr(study,'study_cases',lambda _:cases)
    report=study.summarize_study({'planned_runs':rows},results,reviews,originals,bootstrap_samples=20)
    assert report['primary']['estimate']==0.5 and report['primary']['family_count']==2
    rid=next(r for r in results if r.startswith('1-') and results[r]['condition']=='C_verified_rag')
    results.pop(rid);reviews.pop(rid)
    report=study.summarize_study({'planned_runs':rows},results,reviews,originals,bootstrap_samples=20)
    assert report['primary']['estimate']==1 and report['primary']['family_count']==1
    assert 'family-1' in report['primary']['excluded_families']


def test_pending_study_cannot_load_model_or_create_run(tmp_path,monkeypatch):
    from src.rag import paper_experiment as runner
    monkeypatch.setattr(runner,'validate_lock',lambda *a,**kw:None)
    envelope={'lock':{'planned_runs':[{}]*3,'execution_ready':False,'blockers':['human_gold_review_missing']}}
    def forbidden(**kwargs):
        pytest.fail('Model must not load before actual gold review')
    with pytest.raises(ValueError,match='human_gold_review_missing'):
        runner.execute_experiment(envelope,tmp_path/'run',None,forbidden)
    assert not (tmp_path/'run').exists()


def test_same_person_repeat_requires_48_hours_and_preserves_prior(single,monkeypatch):
    case,result,first,originals=single
    rid=result['run_id'];rows=[dict(run_id=rid,case_id=case['case_id'],condition=result['condition'])]
    monkeypatch.setattr(study,'study_cases',lambda _: [case])
    second=copy.deepcopy(first)
    second['reviewers'][0]['reviewed_at']='2026-10-14T00:00:00Z'
    raw=json.dumps(dict(reviewer_id=second['reviewers'][0]['reviewer_id'],result_sha256=evaluator.digest(result),
                        note='Synthetic second-pass test file')).encode()
    sha=hashlib.sha256(raw).hexdigest();originals[sha]=raw;second['reviewers'][0]['original_review_sha256']=sha
    before=copy.deepcopy(first)
    report=study.repeat_agreement({'planned_runs':rows},{rid:result},{rid:first},{rid:second},originals)
    assert report['completed']==1 and report['independent_reviewers']==1
    assert report['axes']['factual_correctness']['agreement']==1
    assert first==before
    second['reviewers'][0]['reviewed_at']='2026-10-11T01:00:00Z'
    with pytest.raises(ValueError,match='48 hours'):
        study.repeat_agreement({'planned_runs':rows},{rid:result},{rid:first},{rid:second},originals)

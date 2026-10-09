"""Contract validation and per-response human counts, never semantic judging."""
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

SCHEMAS = Path(__file__).resolve().parents[2] / 'data/paper_experiment_v2'

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()

def validate_schema(value, kind):
    if kind not in {'case','review','result','judge'}:
        raise ValueError('Unknown contract')
    schema = json.loads((SCHEMAS / f'{kind}.schema.json').read_text(encoding='utf-8'))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)

def _unique(rows, key):
    values = [r[key] for r in rows]
    if len(values) != len(set(values)):
        raise ValueError(f'Duplicate {key}')
    return set(values)

def validate_case(case):
    validate_schema(case, 'case')
    records = _unique(case['records'], 'record_id')
    facts = _unique(case['reference_facts'], 'fact_id')
    for field in ('eligible_record_ids','candidate_record_ids'):
        if not set(case[field]) <= records:
            raise ValueError('Unknown record reference')
    if not set(case['noise']['raw_record_ids']) <= records:
        raise ValueError('Unknown noise record')
    if not set(case['required_fact_ids']) <= set(case['allowed_fact_ids']) <= facts:
        raise ValueError('Unknown or disallowed required fact')
    for record in case['records']:
        if record['subject_did'] != case['profile']['subject_did']:
            raise ValueError('Cross-subject record')
        _unique(record['verification']['checks'], 'check')
    return True

def rate(labels, positive='no'):
    counts = {label: labels.count(label) for label in ('yes','no','unknown','not_applicable')}
    denominator = counts['yes'] + counts['no']
    return dict(numerator=counts[positive], denominator=denominator,
                value=counts[positive]/denominator if denominator else None,
                unknown=counts['unknown'], not_applicable=counts['not_applicable'])

def exposure(record_ids, noise_ids):
    if len(record_ids) != len(set(record_ids)) or len(noise_ids) != len(set(noise_ids)):
        raise ValueError('Duplicate exposure identity')
    n = len(set(record_ids) & set(noise_ids))
    return dict(numerator=n, denominator=len(record_ids), value=n/len(record_ids) if record_ids else None)

def summarize_response(case, result, review):
    """Counts only. No run-level estimate, freeze gate or automatic approval."""
    validate_case(case)
    validate_schema(result, 'result')
    validate_schema(review, 'review')
    if result['case_id'] != case['case_id'] or review['result_sha256'] != digest(result):
        raise ValueError('Case/result binding mismatch')
    expected = case['candidate_record_ids']
    if result['condition'] == 'A_no_rag':
        expected = []
    elif result['condition'] == 'C_verified_rag':
        expected = [x for x in expected if x in case['eligible_record_ids']]
    if result['context_record_ids'] != expected:
        raise ValueError('Context policy mismatch')
    if review['status'] != 'human_approved':
        return dict(metric_eligible=False, metrics=None, paper_ready=False)
    if result['status'] != 'success' or case['gold_status'] != 'human_approved':
        raise ValueError('Successful result and reviewed gold required')
    if len(review['reviewers']) != 2 or len({r['reviewer_id'].strip() for r in review['reviewers']}) != 2 or any(not r['reviewer_id'].strip() for r in review['reviewers']):
        raise ValueError('Two distinct humans required')
    if not review['segmentation_complete'] or review['issues'] or not review['adjudication_rationale'].strip():
        raise ValueError('Unresolved review')
    claims = review['claims']
    _unique(claims,'claim_id')
    _unique(claims,'canonical_key')
    if review['behavior'] in {'answer','mixed'} and not claims:
        raise ValueError('Missing segmentation')
    fact_ids = {f['fact_id'] for f in case['reference_facts']}
    for c in claims:
        if not c['spans']:
            raise ValueError('Missing claim spans')
        for s in c['spans']:
            if not 0 <= s['start'] < s['end'] <= len(result['answer']) or result['answer'][s['start']:s['end']] != s['text']:
                raise ValueError('Invalid original answer span')
        if not set(c['supporting_record_ids']) <= set(expected) or not set(c['reference_fact_ids']) <= fact_ids:
            raise ValueError('Unknown evidence reference')
        if c['context_support'] == 'yes' and not c['supporting_record_ids']:
            raise ValueError('Context support needs evidence')
        if c['assertion_kind'] == 'fact' and c['factual_correctness'] in {'yes','no'} and not c['reference_fact_ids']:
            raise ValueError('Factual decision needs independent reference')
        if c['assertion_kind'] == 'recommendation' and (c['factual_correctness'] != 'not_applicable' or c['error_type'] not in {'none','unknown'}):
            raise ValueError('Recommendation must not masquerade as fact error')
    covered = set(review['covered_required_fact_ids'])
    justified = {f for c in claims if c['factual_correctness']=='yes' for f in c['reference_fact_ids']}
    if not covered <= set(case['required_fact_ids']) or not covered <= justified:
        raise ValueError('Unsupported coverage credit')
    factual = [c for c in claims if c['assertion_kind']=='fact']
    metrics = {axis: rate([c[axis] for c in factual], 'yes' if axis=='citation_correct' else 'no') for axis in ('factual_correctness','context_support','evidence_eligibility','citation_correct')}
    required = len(case['required_fact_ids'])
    metrics['coverage'] = dict(numerator=len(covered), denominator=required, value=len(covered)/required if required else None)
    return dict(metric_eligible=True, paper_ready=False, metrics=metrics,
                fact_claims=len(factual), error_counts={k:sum(c['error_type']==k for c in factual) for k in ('F','O','other','none','unknown')},
                D_JD_subset_of_F=sum(c['error_type']=='F' and c['jd_aligned']=='yes' for c in factual))

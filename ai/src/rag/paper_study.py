"""Versioned study scope, evidence linkage and single-human analysis.

These checks bind artifacts, not human identities or the truth of synthetic gold.
"""
import copy
import hashlib
import json
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from src.rag.paper_evaluation_v2 import digest, validate_case, validate_schema
from src.rag.paper_experiment_review import _family_estimate, _validate_originals, summarize_experiment

POLICY = 'single_human_v1'
PROTOCOL = 'paper-study/2.1'
STRATA = ('none', 'forged_signature', 'revoked', 'expired', 'untrusted_issuer', 'valid_conflict')
PRIMARY = STRATA[1:5]


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def snapshot(path):
    raw = Path(path).read_bytes()
    return dict(sha256=_hash(raw), text=raw.decode('utf-8'))


def unpack(artifact):
    if _hash(artifact['text'].encode('utf-8')) != artifact['sha256']:
        raise ValueError('Study artifact bytes changed')
    return json.loads(artifact['text'].lstrip('\ufeff'))


def human_declaration(artifact, scope_hash):
    value = unpack(artifact)
    if (set(value) != {'kind', 'reviewer_id', 'reviewed_at', 'scope_sha256', 'decision'}
            or value['kind'] != 'human' or value['decision'] != 'approved'
            or not isinstance(value['reviewer_id'], str) or not value['reviewer_id'].strip()
            or value['scope_sha256'] != scope_hash):
        raise ValueError('Human declaration scope/identity mismatch')
    stamp = datetime.fromisoformat(value['reviewed_at'].replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Human declaration needs a timezone')
    return value


def validate_bundle(bundle):
    """Validate the complete 2-dev/8-test inventory and actual exported evidence."""
    from src.rag.paper_experiment import _drafts, bind_cases
    from src.rag.paper_vc_adapter_v2 import audit_records
    world, vc = unpack(bundle['world']), unpack(bundle['verification'])
    if vc['world_sha256'] != bundle['world']['sha256']:
        raise ValueError('VC evidence does not bind the pre-contamination world')
    if vc['cryptographic_verification_performed'] is not True:
        raise ValueError('Actual signed-fixture verification required')
    if not vc.get('code_sha256') or not vc.get('public_snapshots'):
        raise ValueError('Verification provenance missing')
    for artifact in vc['public_snapshots'].values():
        unpack(artifact)
    families = {f['family_id']: f for f in world['families']}
    if len(families) != 10 or Counter(f['split'] for f in families.values()) != {'dev': 2, 'test': 8}:
        raise ValueError('Expected 2 dev and 8 test families')
    if len({f['lineage_id'] for f in families.values()}) != 10 or len({f['subject_did'] for f in families.values()}) != 10:
        raise ValueError('Family lineage/subject overlap')
    # Detect personal factual replicas, excluding intentionally shared schemas/JDs.
    facts = [digest([(x['attribute'], x['value'], x['time_scope']) for x in f['facts']]) for f in families.values()]
    if len(set(facts)) != len(facts):
        raise ValueError('Copied personal facts are not independent families')
    if Counter(f['job'] for f in families.values() if f['split'] == 'test') != {'backend': 4, 'data': 4}:
        raise ValueError('Test job composition differs from protocol')
    evidence = {r['record']['record_id']: r for r in vc['records']}
    if len(evidence) != len(vc['records']):
        raise ValueError('Duplicate verification artifact')
    seen, cases = set(), {}
    for entry in bundle['entries']:
        draft, case = entry['draft'], entry['case']
        validate_case(case)
        family = families[case['family_id']]
        kind = case['noise']['kind']
        key = (case['family_id'], kind)
        if key in seen or kind not in STRATA or case['case_id'] in cases:
            raise ValueError('Duplicate/unknown study case')
        seen.add(key)
        if (case['split'] != family['split'] or case['lineage_id'] != family['lineage_id']
                or case['profile']['subject_did'] != family['subject_did']
                or case['reference_facts'] != family['facts']
                or case['gold_source_hashes'] != [bundle['world']['sha256']]
                or case['gold_origin'] != 'synthetic_world'
                or case['gold_status'] != 'pending' or case['gold_review'] is not None):
            raise ValueError('Gold/source/lineage mismatch; input bundles remain pending')
        if (case['required_fact_ids'] != family['required_fact_ids']
                or case['retained_fact_ids'] != family['retained_fact_ids']
                or len(case['required_fact_ids']) != 3 or len(case['retained_fact_ids']) != 2):
            raise ValueError('Coverage denominator changed')
        source = dict(text=json.dumps(draft, ensure_ascii=False), sha256='')
        source['sha256'] = _hash(source['text'].encode())
        _drafts([source], split=case['split'])
        bind_cases(dict(sources=[source], max_new_tokens=256, split=case['split']), [case])
        records = draft['source_records']
        if len(records) != 4 or len(case['candidate_record_ids']) != 4:
            raise ValueError('Four common candidates required')
        eligible = len(audit_records(records)['eligible_records'])
        if eligible != (2 if kind in PRIMARY else 4):
            raise ValueError('Incorrect actual policy composition')
        if len(case['noise']['raw_record_ids']) != (0 if kind == 'none' else 2):
            raise ValueError('Incorrect manipulation fraction')
        expected_truth = (family['index'] + PRIMARY.index(kind)) % 2 == 0 if kind in PRIMARY else None
        if entry['target_truth'] != expected_truth:
            raise ValueError('Truth/policy counterbalancing changed')
        for record in records:
            artifact = evidence.get(record['record_id'])
            if artifact is None or artifact['record'] != record:
                raise ValueError('Normalized record differs from verification output')
            raw = artifact['raw_ref']
            if _hash(raw['compactJws'].encode()) != artifact['jws_sha256']:
                raise ValueError('Original JWS bytes changed')
            import base64
            part = raw['compactJws'].split('.')[1]
            document = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
            if digest(document) != record['raw_hash'] or raw['externalId'] != record['source']['external_id']:
                raise ValueError('Signed payload/raw hash mismatch')
            if artifact['case_id'] != case['case_id']:
                raise ValueError('Verification case mismatch')
            slot = artifact['slot']
            fact_index = 2 if kind == 'valid_conflict' and slot >= 2 else slot
            false_content = (slot >= 2 and expected_truth is False) or (kind == 'valid_conflict' and slot == 3)
            statement = family['counterfacts'][fact_index] if false_content else family['facts'][fact_index]['value']
            if (artifact['fact_index'] != fact_index or artifact['false_content'] != false_content
                    or record['achievement']['description'] != statement):
                raise ValueError('Contamination differs from the frozen world assignment')
            formal = next(r for r in case['records'] if r['record_id'] == record['record_id'])
            if formal['verification']['artifact_sha256'] != bundle['verification']['sha256']:
                raise ValueError('Formal record points to different verification bytes')
        retained_support = {f['fact_id'] for r in case['records'] if r['record_id'] in case['eligible_record_ids'] for f in r['facts']}
        if not set(case['retained_fact_ids']) <= retained_support:
            raise ValueError('Retained facts lack eligible supporting records')
        cases[case['case_id']] = case
    if seen != {(f, k) for f in families for k in STRATA}:
        raise ValueError('Incomplete study strata/family inventory')
    return world, cases


def prepare_study(plan, base_dir, tokenizer, *, fingerprint_fn):
    from src.rag.paper_experiment import prepare_experiment, write_json
    validate_schema(plan, 'experiment')
    base = Path(base_dir)
    bundle = json.loads((base / plan['bundle']).read_text(encoding='utf-8-sig'))
    validate_bundle(bundle)
    entries = [e for e in bundle['entries'] if e['case']['split'] == plan['phase']]
    # The existing preparer accepts immutable source snapshots directly internally.
    sources = []
    for entry in entries:
        raw = json.dumps(entry['draft'], ensure_ascii=False).encode()
        sources.append(dict(path='study:' + entry['case']['case_id'], text=raw.decode(), sha256=_hash(raw)))
    standard = dict(version='paper-development-plan/1.0', drafts=['embedded'],
                    generation_seeds=[42], order_seed=42, max_new_tokens=256)
    envelope = prepare_experiment(standard, base, tokenizer, fingerprint_fn=fingerprint_fn,
                                 _sources=sources, _split=plan['phase'], _prompt_version='study-2.1')
    lock = envelope['lock']
    lock.update(version='paper-study-lock/2.1', purpose='study', split=plan['phase'], prompt_version='study-2.1')
    scope = dict(protocol_id=PROTOCOL, review_policy=POLICY, phase=plan['phase'], bundle=bundle,
                 settings={k: lock[k] for k in ('model_id','revision','max_new_tokens','max_input_tokens',
                    'do_sample','num_beams','generation_seeds','order_seed','fingerprint')},
                 primary_strata=list(PRIMARY), prompt_sha256=digest(lock['payloads']))
    # Stable plan identity excludes random UUIDs and timestamps so a declaration can
    # be supplied on a later prepare without approving a different study scope.
    scope['planned_identities'] = [{k:r[k] for k in ('case_id','family_id','condition','seed','order_index')}
                                   for r in lock['planned_runs']]
    gold = snapshot(base / plan['gold_review']) if plan['gold_review'] else None
    declaration = snapshot(base / plan['declaration']) if plan['declaration'] else None
    lock['study'] = dict(scope=scope, scope_sha256=digest(scope), gold_review=gold, declaration=declaration)
    blockers = validate_study_lock(lock)
    lock.update(execution_ready=not blockers, blockers=blockers)
    return dict(lock=lock, lock_sha256=digest(lock))


def validate_study_lock(lock):
    study = lock['study']; scope = study['scope']
    if digest(scope) != study['scope_sha256'] or scope['protocol_id'] != PROTOCOL or scope['review_policy'] != POLICY:
        raise ValueError('Study protocol/policy scope changed')
    if scope['phase'] != lock['split'] or scope['primary_strata'] != list(PRIMARY):
        raise ValueError('Phase/strata changed')
    if any(lock[k] != v for k,v in scope['settings'].items()) or digest(lock['payloads']) != scope['prompt_sha256']:
        raise ValueError('Study generation settings changed')
    if scope['planned_identities'] != [{k:r[k] for k in ('case_id','family_id','condition','seed','order_index')}
                                      for r in lock['planned_runs']]:
        raise ValueError('Study plan changed')
    _, all_cases = validate_bundle(scope['bundle'])
    expected = {c['case_id'] for c in all_cases.values() if c['split'] == lock['split']}
    if expected != set(lock['payloads']):
        raise ValueError('Study cases selected after freeze')
    by_id = {e['draft']['case_id']:e['draft'] for e in scope['bundle']['entries']}
    if {json.loads(s['text'])['case_id']:json.loads(s['text']) for s in lock['sources']} != {i:by_id[i] for i in expected}:
        raise ValueError('Study input snapshot differs from gold/evidence scope')
    blockers = []
    if not study['gold_review']:
        blockers.append('human_gold_review_missing')
    else:
        human_declaration(study['gold_review'], scope['bundle']['world']['sha256'])
    if not study['declaration']:
        blockers.append('human_protocol_declaration_missing')
    else:
        human_declaration(study['declaration'], study['scope_sha256'])
    # Development inspection/generation may be explicitly run with pending gold.
    return blockers if lock['split'] == 'test' else []


def study_cases(lock):
    scope = lock['study']['scope']; gold = lock['study']['gold_review']
    cases = [copy.deepcopy(e['case']) for e in scope['bundle']['entries'] if e['case']['split'] == lock['split']]
    if gold:
        declaration = human_declaration(gold, scope['bundle']['world']['sha256'])
        for case in cases:
            case['gold_status'] = 'human_approved'
            case['gold_review'] = dict(reviewer_id=declaration['reviewer_id'], reviewed_at=declaration['reviewed_at'],
                                      scope_sha256=declaration['scope_sha256'], original_review_sha256=gold['sha256'])
    return cases


def repeat_slots(cases, rows):
    indexed = {c['case_id']:c for c in cases}; strata = defaultdict(list)
    for row in rows:
        strata[(indexed[row['case_id']]['noise']['kind'], row['condition'])].append(row['run_id'])
    rng = random.Random(20261011)
    return [rid for key in sorted(strata) for rid in rng.sample(sorted(strata[key]), min(2, len(strata[key])))]


def exposure_table(lock):
    """Separate policy ineligibility from semantic manipulation at each stage."""
    rows = {}
    for case in study_cases(lock):
        invalid = {r['record_id'] for r in case['records']} - set(case['eligible_record_ids'])
        manipulated = set(case['noise']['raw_record_ids'])
        selections = {'raw': [r['record_id'] for r in case['records']], 'candidate': case['candidate_record_ids']}
        selections.update({condition: value['context_record_ids'] for condition,value in lock['payloads'][case['case_id']].items()})
        rows[case['case_id']] = {stage: dict(count=len(ids),
            policy_ineligible_count=len(set(ids)&invalid),
            policy_ineligible_ratio=len(set(ids)&invalid)/len(ids) if ids else None,
            manipulated_count=len(set(ids)&manipulated),
            manipulated_ratio=len(set(ids)&manipulated)/len(ids) if ids else None)
            for stage,ids in selections.items()}
    return rows


def repeat_agreement(lock, results, first, repeated, originals):
    """Same-person consistency only; preserve both annotations and never reconcile."""
    from src.rag.paper_evaluation_v2 import summarize_response
    cases = {c['case_id']:c for c in study_cases(lock)}
    selected = set(repeat_slots(list(cases.values()), lock['planned_runs']))
    if not set(repeated) <= selected:
        raise ValueError('Repeat sample was not selected before outcome inspection')
    axes = ('factual_correctness','context_support','evidence_eligibility','citation_correct')
    counts = {axis:dict(equal=0,compared=0) for axis in axes}
    segmentation_changes = 0; changes=[]
    for rid,second in repeated.items():
        if rid not in results or rid not in first:
            raise ValueError('Repeat requires an original result and first review')
        initial=first[rid];result=results[rid];case=cases[result['case_id']]
        for review in (initial,second):
            summary=summarize_response(case,result,review,review_policy=POLICY)
            if not summary['metric_eligible']:
                raise ValueError('Repeat agreement requires completed human reviews')
            _validate_originals(review,originals)
        a,b=initial['reviewers'][0],second['reviewers'][0]
        if a['reviewer_id'] != b['reviewer_id']:
            raise ValueError('This report describes same-person repeat review')
        delta=datetime.fromisoformat(b['reviewed_at'].replace('Z','+00:00'))-datetime.fromisoformat(a['reviewed_at'].replace('Z','+00:00'))
        if delta.total_seconds()<48*3600 or a['original_review_sha256']==b['original_review_sha256']:
            raise ValueError('Repeat requires a new original review at least 48 hours later')
        before={c['canonical_key']:c for c in initial['claims']};after={c['canonical_key']:c for c in second['claims']}
        segmentation_changes += len(set(before)^set(after))
        for key in set(before)&set(after):
            for axis in axes:
                counts[axis]['compared']+=1
                equal=before[key][axis]==after[key][axis];counts[axis]['equal']+=int(equal)
                if not equal:
                    changes.append(dict(run_id=rid,canonical_key=key,axis=axis,before=before[key][axis],
                                        after=after[key][axis],reason=after[key]['rationale']))
    return dict(kind='same_person_repeat_consistency',planned=len(selected),completed=len(repeated),
                missing_run_ids=sorted(selected-set(repeated)),segmentation_changes=segmentation_changes,
                axes={k:dict(v,agreement=v['equal']/v['compared'] if v['compared'] else None) for k,v in counts.items()},
                changes=changes,independent_reviewers=1,paper_ready=False)


def summarize_study(lock, results, reviews, originals, *, bootstrap_samples=2000):
    cases = study_cases(lock)
    report = summarize_experiment(cases, lock['planned_runs'], results, reviews,
                                   original_reviews=originals, review_policy=POLICY,
                                   bootstrap_samples=bootstrap_samples, seed=42)
    by_case = {c['case_id']:c for c in cases}
    deltas, exclusions, partial = {}, {}, defaultdict(list)
    for pair in report['paired']['pairs']:
        case = by_case[pair['case_id']]
        if case['noise']['kind'] not in PRIMARY:
            continue
        row = pair['metrics']['factual_correctness']
        partial[case['family_id']].append((case['noise']['kind'], row['B_minus_C'], row['excluded_reason']))
    for family, rows in partial.items():
        if len(rows) == 4 and all(d is not None for _,d,_ in rows):
            deltas[family] = [d for _,d,_ in rows]
        else:
            exclusions[family] = [dict(stratum=k, reason=r or 'stratum_missing') for k,d,r in rows if d is None]
    report['primary'] = dict(metric='factual_error_rate_B_minus_C', strata=list(PRIMARY),
                            **_family_estimate(deltas, bootstrap_samples, 42), excluded_families=exclusions)
    report['paired_available_cases_sensitivity'] = report.pop('paired')
    report['review_policy'] = POLICY
    report['gold_origin'] = 'synthetic_world'
    report['repeat_review_slots'] = repeat_slots(cases, lock['planned_runs'])
    report['exposure'] = exposure_table(lock) if 'payloads' in lock else {}
    extras = {}
    for condition in ('A_no_rag','B_naive_rag','C_verified_rag'):
        ids = [r for r,s in report['response_summaries'].items() if s['metric_eligible'] and results[r]['condition'] == condition]
        events = sum(any(c['used_ineligible_record_ids'] for c in reviews[r]['claims'] if c['assertion_kind']=='fact') for r in ids)
        retained = [report['response_summaries'][r]['metrics']['coverage_retained']['value'] for r in ids]
        extras[condition] = dict(assessed_success=len(ids), ineligible_use_events=events,
            ineligible_use_response_rate=events/len(ids) if ids else None,
            coverage_retained_mean=sum(retained)/len(retained) if retained else None)
        expected_answer = [r for r in ids if by_case[results[r]['case_id']]['expected_behavior'][condition] != 'abstain']
        excess = sum(reviews[r]['behavior']=='abstain' for r in expected_answer)
        extras[condition]['excess_abstention'] = dict(numerator=excess,denominator=len(expected_answer),
                                                     value=excess/len(expected_answer) if expected_answer else None)
    report['supplementary'] = extras
    strata = {}
    entries = lock.get('study',{}).get('scope',{}).get('bundle',{}).get('entries',[])
    truth = {e['case']['case_id']:e['target_truth'] for e in entries}
    for kind in STRATA:
        strata[kind] = {}
        for condition in ('A_no_rag','B_naive_rag','C_verified_rag'):
            strata[kind][condition] = {}
            for group in ('all','true','false'):
                selected = [row for row in lock['planned_runs'] if row['condition']==condition
                    and by_case[row['case_id']]['noise']['kind']==kind
                    and (group=='all' or truth.get(row['case_id']) is (group=='true'))]
                approved = [report['response_summaries'][r['run_id']] for r in selected
                    if report['response_summaries'].get(r['run_id'],{}).get('metric_eligible')]
                values = [s['metrics']['factual_correctness']['value'] for s in approved
                          if s['metrics']['factual_correctness']['value'] is not None]
                strata[kind][condition][group] = dict(planned=len(selected),assessed=len(approved),
                    defined=len(values),factual_error_response_mean=sum(values)/len(values) if values else None)
    report['strata_descriptive'] = strata
    b,c = (extras[k]['coverage_retained_mean'] for k in ('B_naive_rag','C_verified_rag'))
    report['retained_coverage_warning'] = None if b is None or c is None else c-b <= -0.10
    return report

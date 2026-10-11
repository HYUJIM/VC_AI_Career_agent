"""Bounded development experiments using the existing renderer and Qwen backend.

An execution lock preserves inputs/settings; it is not human research approval.
No cryptographic verification, gold creation, automatic retry, or semantic judge.
"""
import copy
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import itertools
import json
import os
from pathlib import Path
import platform
import random
import uuid

from src.data.credential_loader import validate_records
from src.llm.transformers_llm import MODEL_ID, REVISION, measure_messages, validate_settings
from src.rag.paper_context_renderer_v2 import CONDITIONS, render_abc
from src.rag.paper_evaluation_v2 import digest, validate_case, validate_schema
from src.rag.paper_experiment_review import pending_review

ROOT = Path(__file__).resolve().parents[2]
CODE_FILES = (
    'src/rag/paper_experiment.py', 'src/rag/paper_experiment_review.py',
    'src/rag/paper_context_renderer_v2.py', 'src/rag/paper_vc_adapter_v2.py',
    'src/rag/paper_evaluation_v2.py', 'src/llm/transformers_llm.py',
    'src/data/credential_loader.py', 'src/utils/schema_validator.py',
    'scripts/run_paper_experiment.py',
    'data/paper_experiment_v2/case.schema.json',
    'data/paper_experiment_v2/result.schema.json',
    'data/paper_experiment_v2/review.schema.json',
    '../VC/src/schemas/credential-record.schema.json',
    'src/rag/paper_study.py', 'scripts/prepare_paper_study.py',
    '../VC/scripts/prepare-paper-study.ts',
    'data/paper_experiment_v2_1/case.schema.json',
    'data/paper_experiment_v2_1/review.schema.json',
    'data/paper_experiment_v2_1/experiment.schema.json',
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint():
    packages = {}
    for name in ('torch', 'transformers', 'safetensors', 'tokenizers', 'jsonschema', 'numpy', 'rfc3339-validator'):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return dict(code={name: sha((ROOT / name).read_bytes()) for name in CODE_FILES},
                environment=dict(python=platform.python_version(), platform=platform.platform(),
                                 packages=packages))


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value, *, replace=False):
    """Exclusive new artifacts; atomic replacement only for this run's ledger."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    if replace:
        temporary = path.with_name(path.name + '.' + str(uuid.uuid4()) + '.tmp')
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)
        os.replace(temporary, path)
    else:
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(text)


def _seed(value):
    if type(value) is not int or not 0 <= value < 2**32:
        raise ValueError('Seed must be an integer in 0..2**32-1')
    return value


def _drafts(sources, *, split="dev"):
    drafts, families, subjects, records = {}, {}, {}, {}
    if not isinstance(sources, list) or not sources:
        raise ValueError('At least one explicit draft is required')
    for source in sources:
        if sha(source['text'].encode('utf-8')) != source['sha256']:
            raise ValueError('Input snapshot hash mismatch')
        draft = json.loads(source['text'].lstrip('\ufeff'))
        validate_records(draft.get('source_records'))
        for key in ('case_id', 'family_id'):
            if not isinstance(draft.get(key), str) or not draft[key].strip():
                raise ValueError('Missing case/family identity')
        case_id, family = draft['case_id'], draft['family_id']
        if case_id in drafts:
            raise ValueError('Duplicate case_id')
        if split not in {'dev','test'} or draft.get('split') != split:
            raise ValueError('This bounded runner accepts development drafts only')
        if sha(draft['jd']['text'].encode('utf-8')) != draft['jd']['sha256']:
            raise ValueError('JD text/hash mismatch')
        subject = draft['profile']['subject_did']
        if subject in subjects and subjects[subject] != family:
            raise ValueError('Same subject must belong to the same family')
        subjects[subject] = family
        if family in families and families[family] != subject:
            raise ValueError('Family contains inconsistent subject identities')
        families[family] = subject
        for record in draft['source_records']:
            rid = record['record_id']
            identity = (family, digest(record))
            if rid in records and records[rid] != identity:
                raise ValueError('Shared record identity/content mismatch')
            records[rid] = identity
        drafts[case_id] = draft
    return drafts


def _payloads(drafts, budget, tokenizer, *, prompt_version="legacy"):
    payloads = {}
    for case_id, draft in drafts.items():
        payloads[case_id] = {}
        for condition, payload in render_abc(draft, max_new_tokens=budget, prompt_version=prompt_version).items():
            rendered, count = measure_messages(tokenizer, payload['messages'])
            if count > 2048:
                raise ValueError(f'{case_id}/{condition}: {count} input tokens exceeds 2048; no truncation')
            evidence = json.loads(payload['messages'][1]['content'])['evidence']
            payloads[case_id][condition] = dict(
                **payload, input_tokens=count, rendered_prompt_sha256=sha(rendered.encode('utf-8')),
                context_record_ids=[r['record_id'] for r in evidence])
    return payloads


def prepare_experiment(plan, base_dir, tokenizer, *, fingerprint_fn=fingerprint,
                       _sources=None, _split="dev", _prompt_version="legacy"):
    """Validate every condition before model loading and return an execution lock."""
    if isinstance(plan, dict) and plan.get('version') == 'paper-study-plan/2.1':
        from src.rag.paper_study import prepare_study
        return prepare_study(plan, base_dir, tokenizer, fingerprint_fn=fingerprint_fn)
    if not isinstance(plan, dict) or set(plan) != {
            'version', 'drafts', 'generation_seeds', 'order_seed', 'max_new_tokens'}:
        raise ValueError('Expected paper-development-plan/1.0 with explicit plan fields')
    if plan['version'] != 'paper-development-plan/1.0':
        raise ValueError('Unsupported plan version')
    if type(plan['max_new_tokens']) is not int:
        raise ValueError('Output budget must be an integer')
    validate_settings(REVISION, plan['max_new_tokens'], 2048)
    seeds = plan['generation_seeds']
    if not isinstance(seeds, list) or not seeds or len(set(map(_seed, seeds))) != len(seeds):
        raise ValueError('Expected nonempty unique generation seeds')
    _seed(plan['order_seed'])
    if not isinstance(plan['drafts'], list) or not plan['drafts']:
        raise ValueError('Expected nonempty explicit draft paths')
    sources = []
    for name in ([] if _sources is not None else plan['drafts']):
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Invalid draft path')
        path = (Path(base_dir) / name).resolve()
        raw = path.read_bytes()
        sources.append(dict(path=str(path), sha256=sha(raw), text=raw.decode('utf-8')))
    sources = sources if _sources is None else _sources
    drafts = _drafts(sources, split=_split)
    payloads = _payloads(drafts, plan['max_new_tokens'], tokenizer, prompt_version=_prompt_version)
    rng = random.Random(plan['order_seed'])
    blocks = list(itertools.product(drafts, seeds))
    rng.shuffle(blocks)
    orders = list(itertools.permutations(CONDITIONS))
    rng.shuffle(orders)
    planned = []
    for index, (case_id, seed) in enumerate(blocks):
        for condition in orders[index % len(orders)]:
            planned.append(dict(run_id=str(uuid.uuid4()), case_id=case_id,
                                family_id=drafts[case_id]['family_id'], condition=condition,
                                seed=seed, order_index=len(planned)))
    lock = dict(version='paper-development-lock/1.0', created_at=now(),
                purpose='development', paper_ready=False, cryptographic_verification_performed=False,
                verification_basis='reported_checks_only', sources=sources, planned_runs=planned,
                generation_seeds=seeds, order_seed=plan['order_seed'], payloads=payloads,
                model_id=MODEL_ID, revision=REVISION, max_new_tokens=plan['max_new_tokens'],
                max_input_tokens=2048, do_sample=False, num_beams=1,
                fingerprint=fingerprint_fn(), retry_policy='none; preserve failed/missing runs',
                limitations=['Execution configuration lock is not an approved research freeze.',
                             'Current gold, independent references and human reviews are not approved.',
                             'Greedy seeds are repetitions, not independent participant families.'])
    return dict(lock=lock, lock_sha256=digest(lock))


def validate_lock(envelope, tokenizer, *, fingerprint_fn=fingerprint):
    lock = envelope['lock']
    if digest(lock) != envelope['lock_sha256']:
        raise ValueError('Execution lock hash mismatch')
    modern = lock['version'] == 'paper-study-lock/2.1'
    if (lock['version'] not in {'paper-development-lock/1.0', 'paper-study-lock/2.1'} or lock['paper_ready'] is not False
            or lock['purpose'] != ('study' if modern else 'development') or lock['model_id'] != MODEL_ID
            or lock['revision'] != REVISION or lock['do_sample'] is not False
            or lock['num_beams'] != 1 or lock['max_input_tokens'] != 2048):
        raise ValueError('Unsupported execution settings')
    validate_settings(REVISION, lock['max_new_tokens'], lock['max_input_tokens'])
    if lock['fingerprint'] != fingerprint_fn():
        raise ValueError('Code/environment changed; prepare a new lock')
    drafts = _drafts(lock['sources'], split=lock.get('split', 'dev'))
    if _payloads(drafts, lock['max_new_tokens'], tokenizer, prompt_version=lock.get('prompt_version', 'legacy')) != lock['payloads']:
        raise ValueError('Prompt/tokenizer preflight changed')
    seeds = lock['generation_seeds']
    if not seeds or len(set(map(_seed, seeds))) != len(seeds):
        raise ValueError('Invalid generation seeds')
    _seed(lock['order_seed'])
    expected = set(itertools.product(drafts, CONDITIONS, seeds))
    identities, ids = set(), set()
    for index, row in enumerate(lock['planned_runs']):
        uuid.UUID(row['run_id'])
        key = (row['case_id'], row['condition'], row['seed'])
        if (row['order_index'] != index or row['run_id'] in ids or key in identities
                or key not in expected or row['family_id'] != drafts[row['case_id']]['family_id']):
            raise ValueError('Invalid planned run identity/order')
        ids.add(row['run_id'])
        identities.add(key)
    if identities != expected:
        raise ValueError('Incomplete A/B/C planned denominator')
    if modern:
        from src.rag.paper_study import validate_study_lock
        blockers = validate_study_lock(lock)
        if lock['execution_ready'] != (not blockers) or lock['blockers'] != blockers:
            raise ValueError('Study readiness status inconsistent')
    elif lock.get('split', 'dev') != 'dev' or lock.get('prompt_version', 'legacy') != 'legacy':
        raise ValueError('Legacy locks remain development-only')
    return drafts


def run_counts(planned, results):
    success = sum(r['status'] == 'success' for r in results.values())
    failed = sum(r['status'] == 'failed' for r in results.values())
    return dict(planned=len(planned), success=success, failed=failed,
                missing=len(planned) - success - failed,
                length=sum(r['finish_reason'] == 'length' for r in results.values()))


def development_review_bundle(lock, results):
    """Solo-readable output; no automatic labels or invented formal case/gold."""
    if lock['version'] == 'paper-study-lock/2.1':
        from src.rag.paper_study import study_cases, repeat_slots, POLICY
        from src.rag.paper_experiment_review import make_review_bundle
        cases = study_cases(lock)
        bundle = make_review_bundle(cases, list(results.values()), seed=lock['order_seed'], review_policy=POLICY)
        bundle['repeat_review_slots'] = repeat_slots(cases, lock['planned_runs'])
        return bundle
    packets, key, pending = [], {}, {}
    drafts = _drafts(lock['sources'], split=lock.get('split', 'dev'))
    for result in results.values():
        pending[result['run_id']] = pending_review(result)
        if result['status'] != 'success':
            continue
        review_id = str(uuid.uuid4())
        payload = lock['payloads'][result['case_id']][result['condition']]
        context = json.loads(payload['messages'][1]['content'])
        packets.append(dict(review_id=review_id, answer=result['answer'], **context))
        key[review_id] = dict(run_id=result['run_id'], case_id=result['case_id'],
                              family_id=drafts[result['case_id']]['family_id'],
                              condition=result['condition'], seed=result['seed'],
                              result_sha256=digest(result), gold_status='pending')
    random.Random(lock['order_seed']).shuffle(packets)
    return dict(packets=packets, adjudication_key=key, reviews_pending=pending, paper_ready=False)


def execute_experiment(envelope, directory, tokenizer, llm_factory, *, max_runs=15,
                       fingerprint_fn=fingerprint, stop_after=None):
    """One model load, independent conversations, fail-stop, no resume/overwrite."""
    validate_lock(envelope, tokenizer, fingerprint_fn=fingerprint_fn)
    lock, directory = envelope['lock'], Path(directory)
    if type(max_runs) is not int or not 1 <= len(lock['planned_runs']) <= max_runs:
        raise ValueError('Plan exceeds explicit maximum run count')
    if stop_after is not None:
        if lock.get('split', 'dev') != 'dev':
            raise ValueError('stop-after is development-only')
        if (type(stop_after) is not int or stop_after < 3 or stop_after % 3
                or stop_after > len(lock['planned_runs'])):
            raise ValueError('stop-after must select complete A/B/C blocks within the plan')
        # Stop only at whole case/seed boundaries; retain the frozen full plan.
        for offset in range(0, stop_after, 3):
            block = lock['planned_runs'][offset:offset + 3]
            if (len({(r['case_id'], r['family_id'], r['seed']) for r in block}) != 1
                    or {r['condition'] for r in block} != set(CONDITIONS)):
                raise ValueError('stop-after requires contiguous complete A/B/C blocks')
    if lock.get('execution_ready') is False:
        raise ValueError('Study not ready: ' + ', '.join(lock['blockers']))
    directory.mkdir(parents=True, exist_ok=False)
    write_json(directory / 'execution_lock.json', envelope)
    ledger = dict(version='paper-ledger/2.0', lock_sha256=envelope['lock_sha256'],
                  status='loading', planned_runs=lock['planned_runs'], attempts=[], quarantine=[],
                  started_at=now(), paper_ready=False)
    if stop_after is not None:
        ledger['requested_stop_after'] = stop_after
    results = {}
    write_json(directory / 'ledger.json', ledger)
    try:
        llm = llm_factory(revision=REVISION, max_new_tokens=lock['max_new_tokens'],
                          max_input_tokens=2048, seed=lock['planned_runs'][0]['seed'], local_files_only=True)
        runtime = copy.deepcopy(llm.runtime_metadata)
        if (runtime['resolved_revision'] != REVISION
                or runtime['generation_config']['max_new_tokens'] != lock['max_new_tokens']
                or runtime['generation_config']['do_sample'] is not False
                or runtime['generation_config']['num_beams'] != 1):
            raise ValueError('Model runtime disagrees with execution lock')
        if _payloads(_drafts(lock['sources'], split=lock.get('split', 'dev')), lock['max_new_tokens'], llm.tokenizer, prompt_version=lock.get('prompt_version', 'legacy')) != lock['payloads']:
            raise ValueError('Loaded model tokenizer differs from preflight')
        write_json(directory / 'runtime.json', runtime)
        ledger['status'] = 'running'
        for row in lock['planned_runs']:
            payload = lock['payloads'][row['case_id']][row['condition']]
            attempt = dict(run_id=row['run_id'], attempt_id=str(uuid.uuid4()), started_at=now(), status='running')
            ledger['attempts'].append(attempt)
            write_json(directory / 'ledger.json', ledger, replace=True)
            result = dict(version='paper-result/2.0',
                          **{k: row[k] for k in ('run_id', 'case_id', 'condition', 'seed')},
                          status='failed', finish_reason='exception', answer='',
                          context_record_ids=payload['context_record_ids'],
                          input_tokens=payload['input_tokens'], output_tokens=None)
            try:
                llm.seed = row['seed']
                llm.last_generation = None
                answer = llm.generate(payload['messages'])
                generation = copy.deepcopy(llm.last_generation)
                if (generation['messages'] != payload['messages']
                        or generation['token_usage']['input_tokens'] != payload['input_tokens']
                        or not 0 <= generation['token_usage']['output_tokens'] <= lock['max_new_tokens']
                        or sha(generation['rendered_prompt'].encode('utf-8')) != payload['rendered_prompt_sha256']):
                    raise ValueError('Generated input differs from locked prompt')
                result.update(status='success', answer=answer, finish_reason=generation['finish_reason'],
                              output_tokens=generation['token_usage']['output_tokens'])
                validate_schema(result, 'result')
                write_json(directory / 'generation' / (row['run_id'] + '.json'),
                           dict(seed=row['seed'], **generation))
            except Exception as error:
                result.update(status='failed', answer='', finish_reason='exception', output_tokens=None)
                attempt['error'] = dict(type=type(error).__name__, message=str(error))
            validate_schema(result, 'result')
            path = directory / 'results' / (row['run_id'] + '.json')
            write_json(path, result)
            results[row['run_id']] = result
            attempt.update(status=result['status'], finished_at=now(),
                           result_canonical_sha256=digest(result), result_file_sha256=sha(path.read_bytes()))
            write_json(directory / 'ledger.json', ledger, replace=True)
            if result['status'] == 'failed':
                ledger['status'] = 'failed'
                break
            if stop_after is not None and len(results) == stop_after and len(results) < len(lock['planned_runs']):
                ledger.update(status='development_partial', stop_reason='requested_stop_after')
                break
        else:
            ledger['status'] = 'completed'
    except KeyboardInterrupt:
        ledger['status'] = 'interrupted'
        raise
    except Exception as error:
        ledger.update(status='failed', error=dict(type=type(error).__name__, message=str(error)))
    finally:
        ledger.update(finished_at=now(), counts=run_counts(lock['planned_runs'], results))
        write_json(directory / 'ledger.json', ledger, replace=True)
        bundle = development_review_bundle(lock, results)
        write_json(directory / 'review/packets.json', bundle['packets'])
        write_json(directory / 'review/reviews_pending.json', bundle['reviews_pending'])
        write_json(directory / 'private/adjudication_key.json', bundle['adjudication_key'])
        if 'repeat_review_slots' in bundle:
            write_json(directory / 'review/repeat_slots.json', bundle['repeat_review_slots'])
            from src.rag.paper_study import study_cases, POLICY
            from src.rag.paper_experiment_review import make_review_bundle
            repeated = make_review_bundle(study_cases(lock),
                [r for rid,r in results.items() if rid in bundle['repeat_review_slots']],
                seed=20261011, review_policy=POLICY)
            write_json(directory / 'review/repeat_packets.json', repeated['packets'])
            write_json(directory / 'private/repeat_key.json', repeated['adjudication_key'])
    return ledger


def bind_cases(lock, cases):
    """Bind separately supplied formal cases to the actual locked generation data."""
    drafts = _drafts(lock['sources'], split=lock.get('split', 'dev'))
    indexed = {}
    for case in cases:
        validate_case(case)
        cid = case['case_id']
        if cid not in drafts or cid in indexed:
            raise ValueError('Unknown/duplicate formal case')
        draft = drafts[cid]
        for key in ('family_id', 'split', 'synthetic', 'profile', 'jd', 'request',
                    'candidate_record_ids', 'eligible_record_ids', 'noise'):
            if case[key] != draft[key]:
                raise ValueError(f'Formal case differs from generated draft: {key}')
        records = {r['record_id']: r for r in case['records']}
        if set(records) != {r['record_id'] for r in draft['source_records']}:
            raise ValueError('Formal record coverage differs from draft')
        # Only the renderer's public allowlisted evidence enters review text.
        all_evidence = json.loads(render_abc(dict(draft, candidate_record_ids=list(records)),
                                           max_new_tokens=lock['max_new_tokens'])['B_naive_rag']['messages'][1]['content'])['evidence']
        texts = {r['record_id']: json.dumps(r, ensure_ascii=False, sort_keys=True) for r in all_evidence}
        for source in draft['source_records']:
            record = records[source['record_id']]
            if (record['text'] != texts[source['record_id']]
                    or record['original_id'] != source['source']['external_id']
                    or record['raw_sha256'] != source['raw_hash']
                    or record['verification']['status'] != source['verification']['status']):
                raise ValueError('Formal record text/provenance differs from draft')
            checks = {c['check']: c for c in record['verification']['checks']}
            original = {c['check']: c for c in source['verification']['checks']}
            if set(checks) != set(original) or any(
                    any(checks[k].get(field) != value for field, value in original[k].items()) for k in original):
                raise ValueError('Formal verification checks differ from draft')
        indexed[cid] = case
    if set(indexed) != set(drafts):
        raise ValueError('Formal cases required for every planned case; no denominator selection')
    return indexed


def load_results(directory, envelope):
    """Check persisted result bytes and plan linkage before any evaluation."""
    directory = Path(directory)
    ledger = read_json(directory / 'ledger.json')
    lock = envelope['lock']
    if (ledger['lock_sha256'] != envelope['lock_sha256']
            or ledger['planned_runs'] != lock['planned_runs']):
        raise ValueError('Ledger/lock mismatch')
    plan = {r['run_id']: r for r in lock['planned_runs']}
    attempts = {r['run_id']: r for r in ledger['attempts']}
    if len(attempts) != len(ledger['attempts']) or not set(attempts) <= set(plan):
        raise ValueError('Duplicate/unknown attempt')
    results = {}
    for path in (directory / 'results').glob('*.json'):
        result = read_json(path)
        validate_schema(result, 'result')
        rid = result['run_id']
        if rid not in attempts or rid in results or path.name != rid + '.json':
            raise ValueError('Unregistered/duplicate result')
        if (attempts[rid].get('result_file_sha256') != sha(path.read_bytes())
                or attempts[rid].get('result_canonical_sha256') != digest(result)
                or attempts[rid]['status'] != result['status']
                or any(result[k] != plan[rid][k] for k in ('case_id', 'condition', 'seed'))):
            raise ValueError('Result/attempt/plan mismatch')
        payload = lock['payloads'][result['case_id']][result['condition']]
        if (result['context_record_ids'] != payload['context_record_ids']
                or result['input_tokens'] != payload['input_tokens']):
            raise ValueError('Result context/token preflight mismatch')
        results[rid] = result
    if any(a['status'] in {'success', 'failed'} and rid not in results for rid, a in attempts.items()):
        raise ValueError('Completed attempt is missing its result artifact')
    return results

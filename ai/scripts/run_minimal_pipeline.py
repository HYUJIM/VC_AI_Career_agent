"""Connect BE evidence or one Work 2B fixture draft to Work 2C and Qwen.

Reported-check policy only; this runner never performs signature verification.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.clients.backend_client import BackendClient
from src.data.backend_adapter import save_backend_export, validate_backend_export
from src.data.backend_seed import extract_seed
from src.data.credential_loader import validate_records
from src.llm.transformers_llm import (
    MODEL_ID, REVISION, TransformersLLM, measure_messages, validate_settings,
)
from src.rag.paper_context_renderer_v2 import CONDITIONS, render_abc
from src.rag.paper_vc_adapter_v2 import audit_records


def backend_draft(export, context, record_ids=None):
    """Wrap validated BE evidence for the existing renderer, without fake checks."""
    validate_backend_export(export)
    if not isinstance(context, dict) or context.get('subject_did') != export['subject_did']:
        raise ValueError('Context/BE subject mismatch')
    records = export['records']
    if not records:
        raise ValueError('No usable BE records')
    report = audit_records(records)
    # The BE contract has no verification field. Do not promote backend_status.
    if report['eligible_records']:
        raise ValueError('BE status-only records cannot be policy eligible')
    report.update(input_kind='backend_demo', verification_basis='backend_status_only')
    for row in report['records']:
        row['reasons'].append('verification_details_not_available')
    draft = {
        'draft_version': 'work2b-case-draft/0.1',
        'profile': {'subject_did': export['subject_did'], 'text': context.get('profile')},
        'jd': {'text': context.get('jd')}, 'request': context.get('request'),
        'source_records': records,
        'candidate_record_ids': [r['record_id'] for r in records] if record_ids is None else record_ids,
        'eligible_record_ids': [],
        # This is a renderer consistency snapshot, not fabricated VC evidence.
        'record_audits': [{'record_id': r['record_id'], 'eligible': False,
                           'reported_verification': r.get('verification')} for r in records],
    }
    return draft, report


def main(argv=None, *, tokenizer_factory=None, llm_factory=TransformersLLM,
         client_factory=BackendClient):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--backend-response', type=Path, help='Saved careers response JSON')
    source.add_argument('--backend-export', type=Path, help='Existing backend_evidence.json')
    source.add_argument('--backend-url', help='BE origin; performs only the careers GET')
    source.add_argument('--backend-seed', type=Path, help='Read BE SQL seed; DID comes from --context')
    source.add_argument('--fixture-draft', type=Path, help='One existing Work 2B draft')
    parser.add_argument('--context', type=Path,
                        help='BE JSON: subject_did, profile, jd, request (nonempty text)')
    parser.add_argument('--record-id', action='append', help='Explicit BE candidates, in order')
    parser.add_argument('--condition', choices=CONDITIONS, action='append')
    parser.add_argument('--check-only', action='store_true', help='Token preflight only; no model load')
    parser.add_argument('--max-new-tokens', type=int, default=128)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/minimal_pipeline')
    args = parser.parse_args(argv)
    validate_settings(REVISION, args.max_new_tokens, 2048)
    if args.fixture_draft and (args.context or args.record_id):
        parser.error('Fixture drafts supply their own context and candidate IDs')
    if not args.fixture_draft and not args.context:
        parser.error('BE input requires --context')
    conditions = args.condition or list(CONDITIONS)
    if len(conditions) != len(set(conditions)):
        parser.error('Duplicate condition')

    directory = args.output / str(uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {
        'runner_version': 'minimal-pipeline/1.1', 'status': 'preparing',
        'stage': 'input_validation', 'started_at': datetime.now(timezone.utc).isoformat(),
        'input_kind': 'signed_fixture' if args.fixture_draft else 'backend_seed' if args.backend_seed else 'backend_demo',
        'purpose': 'fixture_connectivity_only' if args.fixture_draft else 'backend_connectivity_only',
        'verification_basis': 'reported_checks_only' if args.fixture_draft else 'backend_status_only',
        'cryptographic_verification_performed': False, 'paper_ready': False,
        'paper_evaluation_eligible': False, 'inference_performed': False,
        'generation_performed': False, 'mock': None, 'completed_results': 0,
        'conditions': conditions, 'model_id': MODEL_ID, 'revision': REVISION,
        'max_input_tokens': 2048, 'max_new_tokens': args.max_new_tokens,
        'input_sha256': {}, 'code_sha256': {},
        'limitations': ['No signature re-verification; no operational authenticity claim.',
                        'Explicit candidates, not semantic retrieval; no refill.',
                        'Caller profile text is not verified credential evidence.'],
    }

    def write(name, value):
        (directory / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')

    def read_input(path, label):
        raw = path.read_bytes()
        manifest['input_sha256'][label] = hashlib.sha256(raw).hexdigest()
        (directory / (label + '.input.json')).write_bytes(raw)
        return json.loads(raw.decode('utf-8-sig'))

    write('manifest.json', manifest)
    try:
        for relative in ('scripts/run_minimal_pipeline.py', 'src/llm/transformers_llm.py',
                         'src/rag/paper_context_renderer_v2.py', 'src/rag/paper_vc_adapter_v2.py',
                         'src/data/backend_adapter.py', 'src/data/backend_seed.py', 'src/data/credential_loader.py',
                         'src/clients/backend_client.py'):
            manifest['code_sha256'][relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if args.fixture_draft:
            draft = read_input(args.fixture_draft, 'fixture')
            if not isinstance(draft, dict):
                raise ValueError('Expected fixture draft object')
            validate_records(draft.get('source_records'))
            report = audit_records(draft['source_records'])
        else:
            context = read_input(args.context, 'context')
            if not isinstance(context, dict):
                raise ValueError('Expected BE context object')
            if args.backend_export:
                export = read_input(args.backend_export, 'backend_export')
            else:
                if args.backend_seed:
                    manifest['stage'] = 'seed_extraction'
                    write('manifest.json', manifest)
                    payload, source_info = extract_seed(args.backend_seed, context.get('subject_did'))
                    manifest['input_sha256']['backend_seed'] = source_info['sha256']
                    write('seed_selection.json', source_info)
                elif args.backend_response:
                    payload = read_input(args.backend_response, 'backend_response')
                    source_info = {'mode': 'offline_response'}
                else:
                    manifest['stage'] = 'backend_fetch'
                    write('manifest.json', manifest)
                    with client_factory(args.backend_url) as client:
                        payload = client.careers(context.get('subject_did'))
                    source_info = {'mode': 'http', 'base_url': args.backend_url}
                manifest['stage'] = 'backend_adapter'
                save_backend_export(payload, context.get('subject_did'), directory / 'backend', source=source_info)
                export = json.loads((directory / 'backend/backend_evidence.json').read_text(encoding='utf-8'))
            draft, report = backend_draft(export, context, args.record_id)
            report['input_kind'] = manifest['input_kind']
        manifest['stage'] = 'context_policy'
        write('policy_audit.json', report)
        rendered_inputs = render_abc(draft, max_new_tokens=args.max_new_tokens)
        manifest['subject_did'] = draft['profile']['subject_did']
        manifest['candidate_record_ids'] = draft['candidate_record_ids']
        manifest['selected_record_ids'] = {
            condition: [r['record_id'] for r in json.loads(value['messages'][1]['content'])['evidence']]
            for condition, value in rendered_inputs.items()
        }
        for condition, value in rendered_inputs.items():
            write(condition + '.input.json', value)
        manifest['stage'] = 'token_preflight'
        write('manifest.json', manifest)
        if tokenizer_factory is None:
            from transformers import AutoTokenizer
            tokenizer_factory = AutoTokenizer.from_pretrained
        tokenizer = tokenizer_factory(MODEL_ID, revision=REVISION, local_files_only=True, trust_remote_code=False)
        measured = {}
        for condition, value in rendered_inputs.items():
            rendered, count = measure_messages(tokenizer, value['messages'])
            measured[condition] = (rendered, count)
            (directory / (condition + '.prompt.txt')).write_text(rendered, encoding='utf-8')
        manifest['input_tokens'] = {key: value[1] for key, value in measured.items()}
        manifest['prompt_sha256'] = {key: hashlib.sha256(value[0].encode('utf-8')).hexdigest()
                                     for key, value in measured.items()}
        if any(count > 2048 for _, count in measured.values()):
            raise ValueError('Input exceeds 2048 tokens; select fewer candidates. No truncation.')
        if args.check_only:
            manifest.update(status='checked', stage='complete')
            return directory
        manifest['stage'] = 'model_load'
        write('manifest.json', manifest)
        llm = llm_factory(max_new_tokens=args.max_new_tokens, local_files_only=True)
        manifest['runtime_metadata'] = llm.runtime_metadata
        manifest['mock'] = bool(llm.runtime_metadata.get('test_double', False))
        # Check every condition before generating the first answer.
        for condition, value in rendered_inputs.items():
            if measure_messages(llm.tokenizer, value['messages']) != measured[condition]:
                raise ValueError('Preflight and generation tokenization differ')
        for condition in conditions:
            manifest.update(stage='generation', active_condition=condition)
            write('manifest.json', manifest)
            answer = llm.generate(rendered_inputs[condition]['messages'])
            manifest['generation_performed'] = True
            manifest['inference_performed'] = not manifest['mock']
            manifest['stage'] = 'save_result'
            write(condition + '.result.json', {
                'condition': condition, 'input_kind': manifest['input_kind'],
                'verification_basis': manifest['verification_basis'],
                'paper_evaluation_eligible': False, 'mock': manifest['mock'],
                'inference_performed': manifest['inference_performed'],
                'selected_record_ids': manifest['selected_record_ids'][condition],
                'generated_answer': answer, 'generation': llm.last_generation,
            })
            manifest['completed_results'] += 1
            write('manifest.json', manifest)
        manifest.update(status='completed', stage='complete')
        return directory
    except BaseException as error:
        manifest.update(status='failed', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        manifest['ended_at'] = datetime.now(timezone.utc).isoformat()
        write('manifest.json', manifest)
        print('Status:', manifest['status'], '| Results:', manifest['completed_results'])
        print('Saved:', directory)


if __name__ == '__main__':
    main()

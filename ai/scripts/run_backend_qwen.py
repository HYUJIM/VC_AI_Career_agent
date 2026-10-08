"""Auditable BE-only Qwen runner; never an A/B/C paper evaluation."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.backend_adapter import load_backend_export
from src.rag.backend_pipeline import prepare_backend_prompt
from src.llm.transformers_llm import MODEL_ID, REVISION, TransformersLLM, validate_settings


def measure(tokenizer, prompt):
    rendered = tokenizer.apply_chat_template([{'role': 'user', 'content': prompt}],
                                             tokenize=False, add_generation_prompt=True)
    tokens = tokenizer([rendered], add_special_tokens=False)['input_ids'][0]
    return rendered, len(tokens)


def main(argv=None, tokenizer_factory=None, llm_factory=TransformersLLM):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--education-diagnostic', action='store_true')
    group.add_argument('--question')
    group.add_argument('--question-file', type=Path)
    parser.add_argument('--record-id', action='append')
    parser.add_argument('--selection-reason', default='Explicit user selection')
    parser.add_argument('--status', choices=['all', 'verified'], default='all')
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--max-new-tokens', type=int, default=128)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/backend_qwen')
    args = parser.parse_args(argv)
    validate_settings(REVISION, args.max_new_tokens, 2048)
    directory = args.output / str(uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    started = perf_counter()
    manifest = dict(status='preparing', purpose='backend_connectivity_only',
                    paper_evaluation_eligible=False, verification_basis='backend_status_only',
                    mock=False, inference_performed=False, model_id=MODEL_ID, revision=REVISION,
                    max_input_tokens=2048, max_new_tokens=args.max_new_tokens)
    def write(name, value):
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    try:
        manifest['stage'] = 'input_validation'
        export = load_backend_export(args.export)
        manifest['export_sha256'] = hashlib.sha256(args.export.read_bytes()).hexdigest()
        manifest['runner_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        expected = None
        if args.education_diagnostic:
            from src.rag.backend_education_diagnostic import reference, review, QUESTION, VERSION
            if args.record_id or args.status != 'all':
                raise ValueError('Diagnostic fixes its three record IDs and status=all')
            expected = reference(export)
            args.record_id = [r['record_id'] for r in expected]
            args.selection_reason = 'Manual fixture selection, not automatic retrieval'
            question = QUESTION
            manifest['diagnostic_version'] = VERSION
            manifest['automatic_retrieval_evaluated'] = False
            manifest['review_status'] = 'not_run'
            manifest['diagnostic_sha256'] = hashlib.sha256((ROOT / 'src/rag/backend_education_diagnostic.py').read_bytes()).hexdigest()
            write('reference.json', expected)
        else:
            question = args.question if args.question is not None else args.question_file.read_text(encoding='utf-8-sig')
        result = prepare_backend_prompt(export, question, record_ids=args.record_id, status_filter=args.status)
        used = {c['record_id'] for c in result['retrieved_contexts']}
        result['selection_audit'] = [dict(record_id=r['record_id'], selected=r['record_id'] in used,
            reason=('included' if r['record_id'] in used else
                    'not_explicitly_selected' if args.record_id is not None and r['record_id'] not in args.record_id
                    else 'backend_reported_status')) for r in export['records']]
        result['selection_reason'] = args.selection_reason if args.record_id else 'All records passing status filter'
        write('evidence.json', result)
        if not used:
            raise ValueError('No selected evidence; no inference performed')
        manifest['stage'] = 'token_preflight'
        if tokenizer_factory is None:
            from transformers import AutoTokenizer
            tokenizer_factory = AutoTokenizer.from_pretrained
        tokenizer = tokenizer_factory(MODEL_ID, revision=REVISION, local_files_only=True, trust_remote_code=False)
        rendered, count = measure(tokenizer, result['prompt'])
        (directory / 'prompt.txt').write_text(result['prompt'], encoding='utf-8')
        (directory / 'rendered_prompt.txt').write_text(rendered, encoding='utf-8')
        manifest.update(input_tokens=count, token_budget_checked=True,
                        prompt_sha256=hashlib.sha256(result['prompt'].encode()).hexdigest())
        # Measure the full candidate pool for a reproducible comparison with selection.
        full = prepare_backend_prompt(export, question, status_filter=args.status)
        manifest['all_candidate_input_tokens'] = measure(tokenizer, full['prompt'])[1]
        manifest['empty_evidence_input_tokens'] = measure(tokenizer, prepare_backend_prompt(export, question, record_ids=[])['prompt'])[1]
        if count > 2048:
            raise ValueError(f'Input has {count} tokens; limit is 2048. Select explicit record IDs; no truncation.')
        if args.check_only:
            manifest.update(status='checked', stage='complete')
        else:
            manifest['stage'] = 'model_load'
            write('manifest.json', manifest)
            llm = llm_factory(max_new_tokens=args.max_new_tokens)
            manifest['runtime_metadata'] = llm.runtime_metadata
            # Guard against drift between preflight and generation tokenizers.
            if measure(llm.tokenizer, result['prompt']) != (rendered, count):
                raise ValueError('Preflight and generation tokenization differ')
            manifest['stage'] = 'generation'
            write('manifest.json', manifest)
            answer = llm.generate(result['prompt'])
            manifest['inference_performed'] = True
            result.update(generated_answer=answer, generation=llm.last_generation,
                          runtime_metadata=llm.runtime_metadata, inference_performed=True, mock=False)
            write('result.json', result)
            (directory / 'answer.txt').write_text(answer, encoding='utf-8')
            if expected is not None:
                manifest['stage'] = 'answer_review'
                checked = review(answer, expected, (llm.last_generation or {}).get('finish_reason'))
                write('review.json', checked)
                manifest['review_status'] = checked['status']
            manifest.update(status='completed', stage='complete')
    except BaseException as error:
        manifest.update(status='failed', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        manifest['total_seconds'] = perf_counter() - started
        write('manifest.json', manifest)
        print('Status:', manifest['status'], '| Inference:', manifest['inference_performed'],
              '| Review:', manifest.get('review_status', 'not_requested'))
        print('Saved:', directory)
    return directory


if __name__ == '__main__':
    main()

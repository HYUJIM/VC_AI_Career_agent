"""Frozen BE-only engineering evaluation. Default offline, --qwen performs inference."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.data.backend_adapter import load_backend_export, validate_backend_export
from src.rag.backend_education import structure, source_display, selection_metrics, field_metrics, generation_prompt
from src.rag.backend_education_evaluation import evaluate_answer


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_frozen(root=ROOT):
    lock_path = root / 'data/backend_education_v1/lock.json'
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    for path, expected in lock['files'].items():
        if digest(root / path) != expected:
            raise ValueError('Frozen evaluation input changed: ' + path)
    return json.loads((root / 'data/backend_education_v1/cases.json').read_text(encoding='utf-8')), lock


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=['development', 'holdout'], default='development')
    parser.add_argument('--qwen', action='store_true')
    parser.add_argument('--export', type=Path, help='Additional real export: structure only, no invented gold labels')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/backend_education')
    args = parser.parse_args(argv)
    data, lock = load_frozen()
    directory = args.output / str(uuid4())
    directory.mkdir(parents=True, exist_ok=False)
    def write(path, value):
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    manifest = dict(status='running', split=args.split, started_at=datetime.now(timezone.utc).isoformat(),
        inference_requested=args.qwen, inference_count=0, human_adjudication=False,
        paper_evaluation_eligible=False, purpose='synthetic_engineering_evaluation',
        code_sha256={p: digest(ROOT/p) for p in [
            'scripts/evaluate_backend_education.py', 'src/rag/backend_education.py',
            'src/rag/backend_education_evaluation.py', 'src/rag/backend_pipeline.py',
            'src/rag/pipeline.py', 'src/data/backend_adapter.py', 'src/llm/transformers_llm.py']})
    write('manifest.json', manifest)
    write('cases_snapshot.json', data)
    write('lock_snapshot.json', lock)
    # Also retain the exact bytes that the frozen digest refers to.
    for path in lock['files']:
        dest = directory / 'frozen' / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes((ROOT/path).read_bytes())
    summary, llm, tokenizer = [], None, None
    try:
        if args.export:
            export = load_backend_export(args.export)
            source = structure(export)
            write('real_source/export.json', export)
            write('real_source/structured.json', source)
            write('real_source/source_display.json', source_display(source))
            manifest['real_export_file_sha256'] = digest(args.export)
        if args.qwen:
            from transformers import AutoTokenizer
            from src.llm.transformers_llm import MODEL_ID, REVISION, TransformersLLM
            from scripts.run_backend_qwen import measure
            tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, local_files_only=True, trust_remote_code=False)
            manifest.update(model_id=MODEL_ID, revision=REVISION, max_input_tokens=2048, max_new_tokens=256)
        for case in data['cases']:
            if case['split'] != args.split:
                continue
            cid = case['case_id']
            entry = dict(case_id=cid, expected_error=case['expected_error'], generations={})
            summary.append(entry)
            try:
                validate_backend_export(case['export'])
            except ValueError as error:
                entry.update(contract_status='rejected', contract_error=str(error),
                             expected_rejection_observed=bool(case['expected_error']))
                write(cid+'/contract.json', entry)
                continue
            if case['expected_error']:
                entry['contract_status'] = 'unexpected_acceptance'
                continue
            entry['contract_status'] = 'accepted'
            source = structure(case['export'])
            write(cid+'/structured.json', source)
            write(cid+'/source_display.json', source_display(source))
            entry['selection'] = selection_metrics(source['records'], case['expected'])
            entry['fields'] = field_metrics(source['records'], case['expected'])
            for mode in ('oracle', 'automatic'):
                ids = [r['record_id'] for r in (case['expected'] if mode == 'oracle' else source['records'])
                       if (r['selection'] if mode == 'oracle' else r['selection']['status']) == 'selected']
                run = dict(selected_ids=ids, inference_performed=False, status='not_run')
                entry['generations'][mode] = run
                prompt = generation_prompt(case['export'], ids)
                base = cid+'/'+mode+'/'
                write(base+'input.json', dict(prompt=prompt, selected_ids=ids))
                if not args.qwen:
                    continue
                rendered, count = measure(tokenizer, prompt)
                write(base+'token_preflight.json', dict(input_tokens=count, rendered_prompt=rendered))
                run['input_tokens'] = count
                if count > 2048:
                    run.update(status='input_overflow', expected_overflow=case['expected_input_overflow'])
                    continue
                if case['expected_input_overflow']:
                    run['unexpected_under_budget'] = True
                if not ids:
                    run['status'] = 'no_selected_evidence'
                    write(base+'review.json', evaluate_answer('', case['expected'], 'eos'))
                    continue
                if llm is None:
                    llm = TransformersLLM(max_new_tokens=256)
                    manifest['runtime_metadata'] = llm.runtime_metadata
                if measure(llm.tokenizer, prompt) != (rendered, count):
                    raise ValueError('Generation and preflight tokenizers differ')
                # Persist the active case before any GPU call, for failure audit.
                manifest['active_generation'] = dict(case_id=cid, mode=mode)
                write('manifest.json', manifest)
                answer = llm.generate(prompt)
                manifest['inference_count'] += 1
                run.update(status='completed', inference_performed=True)
                (directory/base/'answer.txt').write_text(answer, encoding='utf-8')
                write(base+'generation.json', llm.last_generation)
                review = evaluate_answer(answer, case['expected'], llm.last_generation['finish_reason'])
                run.update(review_status=review['status'], finish_reason=llm.last_generation['finish_reason'],
                    axis_counts={k: len(v) for k, v in review['axes'].items()})
                write(base+'review.json', review)
                write('summary.json', summary)
            write('summary.json', summary)
        manifest['status'] = 'completed'
    except BaseException as error:
        manifest.update(status='failed', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        write('summary.json', summary)
        manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
        manifest['artifacts_sha256'] = {str(p.relative_to(directory)): digest(p) for p in directory.rglob('*') if p.is_file() and p.name != 'manifest.json'}
        write('manifest.json', manifest)
        print('Saved:', directory, '| status:', manifest['status'], '| inference count:', manifest['inference_count'])
    return directory


if __name__ == '__main__':
    main()

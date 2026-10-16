"""Run a locked Study 02 split once. First result kept; no retries.

python scripts/study02_run.py --split-dir <study02 dir>/<split>
"""
import argparse
import copy
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.llm.transformers_llm import REVISION, TransformersLLM  # noqa: E402
from src.rag.paper_experiment import now, read_json, write_json  # noqa: E402
from src.rag.study02 import sha  # noqa: E402


def counts(rows, results):
    done = [results[r['run_id']] for r in rows if r['run_id'] in results]
    return dict(planned=len(rows), success=sum(x['status'] == 'success' for x in done),
                failed=sum(x['status'] == 'failed' for x in done), missing=len(rows) - len(done))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--split-dir', required=True)
    ap.add_argument('--allow-download', action='store_true', help='fetch the pinned model revision if not cached')
    args = ap.parse_args()
    d = Path(args.split_dir)
    lock_path = d / 'payloads.lock.json'
    lock_hash = sha(lock_path.read_text(encoding='utf-8'))
    lock = read_json(lock_path)
    assert lock['revision'] == REVISION and lock['max_new_tokens'] == 256 and lock['do_sample'] is False
    run = d / 'run'
    run.mkdir(exist_ok=False)
    rows = lock['planned_runs']
    write_json(run / 'manifest.json', dict(
        version='study02-run/1.0', split=lock['split'], lock_file_sha256=lock_hash,
        formal_study=False, human_gold_review=False, paper_ready=False,
        runner_sha256=sha(Path(__file__).read_text(encoding='utf-8')),
        note='Exploratory follow-up on an already-exposed test set; see design doc section 2.'))
    ledger = dict(version='study02-ledger/1.0', status='loading', started_at=now(), attempts=[])
    results = {}
    try:
        llm = TransformersLLM(revision=REVISION, max_new_tokens=256, max_input_tokens=2048,
                              seed=42, local_files_only=not args.allow_download)
        write_json(run / 'runtime.json', copy.deepcopy(llm.runtime_metadata))
        ledger['status'] = 'running'
        for row in rows:
            p = lock['payloads'][row['case_key']][row['condition']]
            attempt = dict(run_id=row['run_id'], attempt_id=str(uuid.uuid4()), started_at=now())
            ledger['attempts'].append(attempt)
            result = {k: row[k] for k in ('run_id', 'case_id', 'case_key', 'condition', 'jd_variant', 'seed')}
            result.update(status='failed', answer='', finish_reason='exception', output_tokens=None,
                          input_tokens=p['input_tokens'], context_record_ids=p['context_record_ids'])
            try:
                llm.seed = row['seed']
                answer = llm.generate(p['messages'])
                g = copy.deepcopy(llm.last_generation)
                assert g['messages'] == p['messages']
                assert sha(g['rendered_prompt']) == p['rendered_prompt_sha256']
                result.update(status='success', answer=answer, finish_reason=g['finish_reason'],
                              output_tokens=g['token_usage']['output_tokens'])
                write_json(run / 'generation' / f"{row['run_id']}.json", g)
            except Exception as exc:  # recorded, never retried
                attempt['error'] = dict(type=type(exc).__name__, message=str(exc))
            path = run / 'results' / f"{row['run_id']}.json"
            write_json(path, result)
            results[row['run_id']] = result
            attempt.update(status=result['status'], finished_at=now(),
                           result_file_sha256=sha(path.read_text(encoding='utf-8')))
            ledger['counts'] = counts(rows, results)
            write_json(run / 'ledger.json', ledger, replace=True)
            print(json.dumps(dict(n=len(results), **ledger['counts'])), flush=True)
            if result['status'] == 'failed':
                ledger['status'] = 'failed'
                break
        else:
            ledger['status'] = 'completed'
    except BaseException as exc:
        ledger.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                      error=dict(type=type(exc).__name__, message=str(exc)))
    finally:
        ledger.update(finished_at=now(), counts=counts(rows, results))
        write_json(run / 'ledger.json', ledger, replace=True)
        assert sha(lock_path.read_text(encoding='utf-8')) == lock_hash
        print(json.dumps(dict(status=ledger['status'], **ledger['counts'])), flush=True)
    return 0 if ledger['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())

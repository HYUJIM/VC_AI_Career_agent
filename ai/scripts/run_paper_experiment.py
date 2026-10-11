"""Prepare, execute, inspect, or aggregate bounded A/B/C development experiments."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.llm.transformers_llm import MODEL_ID, REVISION, TransformersLLM
from src.rag.paper_experiment import (
    bind_cases, execute_experiment, load_results, prepare_experiment,
    read_json, run_counts, sha, validate_lock, write_json,
)
from src.rag.paper_experiment_review import make_review_bundle, summarize_experiment


def local_tokenizer():
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION,
                                        trust_remote_code=False, local_files_only=True)


def main(argv=None, *, tokenizer_factory=local_tokenizer, llm_factory=TransformersLLM):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='All-condition preflight; no model load')
    prepare.add_argument('--plan', type=Path, required=True)
    prepare.add_argument('--output', type=Path, required=True, help='New execution lock JSON')
    run = commands.add_parser('run', help='One model load, bounded plan, no retry')
    run.add_argument('--lock', type=Path, required=True)
    run.add_argument('--output', type=Path, required=True, help='New run directory')
    run.add_argument('--max-runs', type=int, default=15)
    run.add_argument('--stop-after', type=int, help='Development only: stop after complete A/B/C blocks; keep the full plan')
    inspect = commands.add_parser('inspect', help='Count persisted runs without inference or gold')
    inspect.add_argument('--run', type=Path, required=True)
    aggregate = commands.add_parser('aggregate', help='Explicit formal cases and human reviews required')
    aggregate.add_argument('--run', type=Path, required=True)
    aggregate.add_argument('--cases', type=Path, help='Array of paper-case/2.0 objects')
    aggregate.add_argument('--reviews', type=Path, required=True, help='run_id -> paper-review/2.0')
    aggregate.add_argument('--original-review', type=Path, action='append', default=[])
    aggregate.add_argument('--repeat-reviews', type=Path, help='Optional same-person second-pass run_id -> review JSON')
    aggregate.add_argument('--output', type=Path, required=True, help='New report directory')
    aggregate.add_argument('--bootstrap-samples', type=int, default=2000)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            if args.output.exists():
                raise FileExistsError('Output exists; choose a new lock filename')
            envelope = prepare_experiment(read_json(args.plan), args.plan.parent, tokenizer_factory())
            write_json(args.output, envelope)
            response = dict(lock=str(args.output.resolve()), planned=len(envelope['lock']['planned_runs']),
                            paper_ready=False, inference_performed=False,
                            execution_ready=envelope['lock'].get('execution_ready', True),
                            blockers=envelope['lock'].get('blockers', []),
                            scope_sha256=envelope['lock'].get('study', {}).get('scope_sha256'))
        elif args.command == 'run':
            ledger = execute_experiment(read_json(args.lock), args.output, tokenizer_factory(), llm_factory,
                                        max_runs=args.max_runs, stop_after=args.stop_after)
            response = dict(status=ledger['status'], **ledger['counts'], output=str(args.output.resolve()),
                            paper_ready=False)
            print(json.dumps(response, ensure_ascii=False))
            return 0 if ledger['status'] in {'completed', 'development_partial'} else 1
        else:
            envelope = read_json(args.run / 'execution_lock.json')
            validate_lock(envelope, tokenizer_factory())
            results = load_results(args.run, envelope)
            if args.command == 'inspect':
                response = dict(**run_counts(envelope['lock']['planned_runs'], results), paper_ready=False,
                                reviews_pending=len(results), semantic_metrics=None)
            else:
                modern = envelope['lock']['version'] == 'paper-study-lock/2.1'
                if modern:
                    from src.rag.paper_study import study_cases, summarize_study, POLICY
                    cases = study_cases(envelope['lock'])
                    if args.cases and read_json(args.cases) != cases:
                        raise ValueError('Cannot replace frozen study cases during aggregation')
                else:
                    if not args.cases:
                        raise ValueError('Legacy aggregation requires --cases')
                    cases = read_json(args.cases)
                bind_cases(envelope['lock'], cases)
                originals = {sha(p.read_bytes()): p.read_bytes() for p in args.original_review}
                report = summarize_study(envelope['lock'], results, read_json(args.reviews), originals, bootstrap_samples=args.bootstrap_samples) if modern else summarize_experiment(cases, envelope['lock']['planned_runs'], results,
                                              read_json(args.reviews), original_reviews=originals,
                                              bootstrap_samples=args.bootstrap_samples,
                                              seed=envelope['lock']['order_seed'])
                bundle = make_review_bundle(cases, list(results.values()), seed=envelope['lock']['order_seed'], review_policy=POLICY if modern else 'dual_human_v2')
                if args.repeat_reviews:
                    if not modern:
                        raise ValueError('Same-person repeat report requires the explicit study policy')
                    from src.rag.paper_study import repeat_agreement
                    report['repeat_consistency'] = repeat_agreement(envelope['lock'],results,
                        read_json(args.reviews),read_json(args.repeat_reviews),originals)
                args.output.mkdir(parents=True, exist_ok=False)
                write_json(args.output / 'summary.json', report)
                write_json(args.output / 'review/packets.json', bundle['packets'])
                write_json(args.output / 'private/adjudication_key.json', bundle['adjudication_key'])
                response = dict(output=str(args.output.resolve()), **report['counts'], paper_ready=False)
        print(json.dumps(response, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps(dict(error=type(error).__name__, message=str(error)), ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

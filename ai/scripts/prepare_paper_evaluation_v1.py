"""Prepare immutable review bundle or aggregate submitted human reviews; no inference."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.rag.paper_evaluation_v1 import prepare, aggregate


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    for name in ('run', 'queries', 'rubric', 'protocol', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--seed', type=int, default=42)
    a = sub.add_parser('aggregate')
    for name in ('bundle', 'reviews', 'output'):
        a.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    directory = args.output / str(uuid4())
    if args.command == 'prepare':
        files = sorted(p for p in args.run.glob('*.json') if p.name != 'manifest.json')
        inputs = {str(p): p.read_bytes() for p in files + [args.run / 'manifest.json', args.queries, args.rubric, args.protocol]}
        results = [json.loads(inputs[str(p)].decode('utf-8-sig')) for p in files]
        lines = lambda p: [json.loads(s) for s in inputs[str(p)].decode('utf-8-sig').splitlines() if s.strip()]
        bundle = prepare(results, lines(args.queries), lines(args.rubric), args.seed)
        summary = aggregate(bundle, bundle['reviews'])
        outputs = {'review_packets.json': bundle['packets'], 'adjudication_key.json': bundle['adjudication_key'],
                   'reviews_pending.json': bundle['reviews'], 'summary_pending.json': summary,
                   'bundle.json': bundle, 'original_results.json': results}
        manifest = {'purpose': 'retrospective_development_review_not_preregistered_paper_run',
                    'inference_performed': False, 'human_adjudication': False, 'seed': args.seed,
                    'input_sha256': {n: hashlib.sha256(b).hexdigest() for n, b in inputs.items()}}
    else:
        bundle = read(args.bundle / 'bundle.json')
        old = read(args.bundle / 'manifest.json')
        for name, expected in old['file_sha256'].items():
            if hashlib.sha256((args.bundle / name).read_bytes()).hexdigest() != expected:
                raise ValueError('Review bundle artifact changed: ' + name)
        outputs = {'summary.json': aggregate(bundle, read(args.reviews)), 'reviews_snapshot.json': read(args.reviews)}
        manifest = {'purpose': 'human_review_aggregation', 'inference_performed': False,
                    'bundle_manifest_sha256': hashlib.sha256((args.bundle / 'manifest.json').read_bytes()).hexdigest(),
                    'reviews_sha256': hashlib.sha256(args.reviews.read_bytes()).hexdigest()}
    directory.mkdir(parents=True, exist_ok=False)
    for name, value in outputs.items():
        (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.command == 'prepare':
        (directory / 'protocol_snapshot.md').write_bytes(inputs[str(args.protocol)])
        (directory / 'queries_snapshot.jsonl').write_bytes(inputs[str(args.queries)])
        (directory / 'rubric_snapshot.jsonl').write_bytes(inputs[str(args.rubric)])
        (directory / 'source_manifest.json').write_bytes(inputs[str(args.run / 'manifest.json')])
    manifest['code_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in
                              (Path(__file__), ROOT / 'src/rag/paper_evaluation_v1.py')}
    manifest['file_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    (directory / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(directory)


if __name__ == '__main__':
    main()

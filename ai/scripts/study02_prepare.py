"""Build and lock Study 02 inputs for one split (dev or test). No model calls.

python scripts/study02_prepare.py --source <study01 dir> --out <study02 dir> --split dev
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.llm.transformers_llm import MODEL_ID, REVISION, measure_messages  # noqa: E402
from src.rag.paper_experiment import fingerprint, now, read_json, write_json  # noqa: E402
from src.rag.study02 import sha  # noqa: E402
from src.rag import study02  # noqa: E402

CODE = ('src/rag/study02.py', 'src/rag/study02_eval.py', 'src/rag/paper_context_renderer_v2.py',
        'src/llm/transformers_llm.py', 'scripts/study02_prepare.py', 'scripts/study02_run.py',
        'scripts/study02_evaluate.py')
SEEDS = {'dev': (2026101301, 2026101311), 'test': (2026101302, 2026101312)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--split', choices=('dev', 'test'), required=True)
    args = ap.parse_args()
    src, out = Path(args.source), Path(args.out)
    bundle_raw = (src / 'bundle.json').read_bytes()
    bundle = json.loads(bundle_raw)
    world = json.loads(bundle['world']['text'])
    if sha(bundle['world']['text']) != bundle['world']['sha256']:
        raise SystemExit('world hash mismatch')
    reference = read_json(src / 'test.pending.lock.json')['lock'] if args.split == 'test' else None
    id_seed, order_seed = SEEDS[args.split]
    payloads, rows, id_map = study02.build(bundle['entries'], world, split=args.split,
                                           id_seed=id_seed, order_seed=order_seed)
    report = study02.audit(payloads, rows, id_map, bundle['entries'], reference_lock=reference)

    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, local_files_only=True)
    for key, arms in payloads.items():
        for condition, p in arms.items():
            rendered, n = measure_messages(tok, p['messages'])
            if n > 2048:
                raise SystemExit(f'{key}/{condition}: {n} tokens > 2048')
            ev = json.loads(p['messages'][1]['content'])['evidence']
            p.update(input_tokens=n, rendered_prompt_sha256=sha(rendered),
                     context_record_ids=[r['record_id'] for r in ev])
    for row in rows:
        p = payloads[row['case_key']][row['condition']]
        row.update(input_sha256=p['rendered_prompt_sha256'], record_count=len(p['context_record_ids']),
                   input_tokens=p['input_tokens'])

    root = Path(__file__).resolve().parents[1]
    lock = dict(version=study02.VERSION, created_at=now(), split=args.split,
                design='docs/paper_experiment2_design_v1.md', formal_study=False,
                human_gold_review=False, paper_ready=False,
                model_id=MODEL_ID, revision=REVISION, max_new_tokens=study02.MAX_NEW_TOKENS,
                max_input_tokens=2048, do_sample=False, num_beams=1, seed=42,
                prompt_version=study02.PROMPT_VERSION, id_seed=id_seed, order_seed=order_seed,
                sources=dict(bundle_sha256=sha(bundle_raw.decode('utf-8')), world_sha256=bundle['world']['sha256']),
                code={c: sha((root / c).read_text(encoding='utf-8')) for c in CODE if (root / c).exists()},
                environment=fingerprint()['environment'],
                conversion_audit=report, planned_runs=rows, payloads=payloads)
    dest = out / args.split
    write_json(dest / 'payloads.lock.json', lock)
    write_json(dest / 'p_id_map.internal.json', id_map)
    write_json(dest / 'condition_manifest.json', rows)
    counts = {}
    for r in rows:
        counts[f"{r['jd_variant']}/{r['condition']}"] = counts.get(f"{r['jd_variant']}/{r['condition']}", 0) + 1
    print(json.dumps(dict(split=args.split, runs=len(rows), counts=counts, audit=report), ensure_ascii=False))


if __name__ == '__main__':
    main()

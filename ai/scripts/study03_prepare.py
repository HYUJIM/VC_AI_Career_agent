"""Build and lock Study 03 inputs for one split. Runs live VC verification first.

python scripts/study03_prepare.py --source <study01 dir> --out <study03 dir> --split dev|test
No model calls. Study-01 files are read only.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.llm.transformers_llm import MODEL_ID, REVISION, measure_messages  # noqa: E402
from src.rag.paper_experiment import fingerprint, now, write_json  # noqa: E402
from src.rag import study03  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SEEDS = {'dev': (2026101401, 2026101411), 'test': (2026101402, 2026101412)}


def live_verify(verification, out_dir):
    fixture = out_dir / 'vc_fixture'
    for rel, snap in verification['public_snapshots'].items():
        if hashlib.sha256(snap['text'].encode('utf-8')).hexdigest() != snap['sha256']:
            raise SystemExit(f'snapshot hash mismatch: {rel}')
        target = fixture / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(snap['text'], encoding='utf-8', newline='')
    inp = out_dir / 'live_input.json'
    inp.write_text(json.dumps([r['raw_ref'] for r in verification['records']]), encoding='utf-8')
    out = out_dir / 'live_verification.json'
    cmd = f'npx tsx scripts/verify-study03.ts "{fixture}" "{inp}" "{out}"'
    subprocess.run(cmd, cwd=ROOT / 'VC', shell=True, check=True)
    return json.loads(out.read_text(encoding='utf-8'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--split', choices=('dev', 'test'), required=True)
    ap.add_argument('--allow-download', action='store_true', help='fetch the pinned model revision if not cached')
    a = ap.parse_args()
    src, out = Path(a.source), Path(a.out)
    bundle_text = (src / 'bundle.json').read_text(encoding='utf-8')
    bundle = json.loads(bundle_text)
    world = json.loads(bundle['world']['text'])
    verification = json.loads(bundle['verification']['text'])
    out.mkdir(parents=True, exist_ok=True)
    live_file = out / 'live_verification.json'
    live = (json.loads(live_file.read_text(encoding='utf-8')) if live_file.exists()
            else live_verify(verification, out))
    results = live['results']
    built = study03.build(bundle['entries'], world, verification['records'], results, split=a.split,
                          id_seed=SEEDS[a.split][0], order_seed=SEEDS[a.split][1])
    report = study03.audit(built, bundle['entries'], results, verification['records'])

    from transformers import AutoTokenizer
    tok = AutoTokenizer.from_pretrained(MODEL_ID, revision=REVISION, local_files_only=not a.allow_download)
    for key, arms in built['payloads'].items():
        p = arms['input']
        rendered, n = measure_messages(tok, p['messages'])
        if n > 2048:
            raise SystemExit(f'{key}: {n} tokens > 2048')
        ev = json.loads(p['messages'][1]['content'])['evidence']
        p.update(input_tokens=n, rendered_prompt_sha256=study03.sha(rendered),
                 context_record_ids=[r['record_id'] for r in ev])
    code = ('ai/src/rag/study03.py', 'ai/src/rag/study02_eval.py', 'ai/src/rag/paper_context_renderer_v2.py',
            'ai/src/rag/paper_vc_adapter_v2.py', 'ai/scripts/study03_prepare.py', 'ai/scripts/study02_run.py',
            'VC/scripts/verify-study03.ts', 'VC/src/verify/pipeline.ts', 'VC/src/verify/checks.ts')
    lock = dict(version=study03.VERSION, created_at=now(), split=a.split, design='ai/docs/paper_experiment3_design_v1.md',
                formal_study=False, human_gold_review=False, paper_ready=False,
                model_id=MODEL_ID, revision=REVISION, max_new_tokens=256, max_input_tokens=2048,
                do_sample=False, num_beams=1, seed=42, prompt_version='study-2.1',
                sources=dict(bundle_sha256=study03.sha(bundle_text), live_verification_sha256=study03.sha(live_file.read_text(encoding='utf-8'))),
                code={c: study03.sha((ROOT / c).read_text(encoding='utf-8')) for c in code},
                environment=fingerprint()['environment'], manipulation_audit=report,
                planned_runs=built['runs'], payloads=built['payloads'])
    dest = out / a.split
    write_json(dest / 'payloads.lock.json', lock)
    write_json(dest / 'cells.json', built['cells'])
    write_json(dest / 'tamper_log.json', built['tamper_log'])
    write_json(dest / 'p_id_map.internal.json', built['id_map'])
    counts = {}
    for c in built['cells']:
        k = f"{c['scenario']}/{c['system']}"
        counts[k] = counts.get(k, 0) + 1
    print(json.dumps(dict(split=a.split, cells=len(built['cells']), unique_inputs=len(built['payloads']),
                          tampered=len(built['tamper_log']), audit=report, counts=counts), ensure_ascii=False))


if __name__ == '__main__':
    main()

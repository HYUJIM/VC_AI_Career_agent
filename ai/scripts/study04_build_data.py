"""Build the Study 04 dataset: world -> automated gold checks -> signed VCs -> bundle + dummy twins.

python scripts/study04_build_data.py --out <study04 dir>/data
Writes new files only. Output bundle.json is readable by study03_prepare.py.
"""
import argparse
import collections
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.rag import study04_data as sd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    out = Path(ap.parse_args().out)
    out.mkdir(parents=True, exist_ok=False)
    world = sd.make_world()
    problems = sd.check_world(world)
    if problems:
        raise SystemExit('gold checks failed: ' + '; '.join(problems))
    world_text = json.dumps(world, ensure_ascii=False, indent=2) + '\n'
    (out / 'world.json').write_text(world_text, encoding='utf-8', newline='\n')
    signed = out / 'signed'
    subprocess.run(f'npx tsx scripts/prepare-study04.ts "{out / "world.json"}" "{signed}"',
                   cwd=ROOT / 'VC', shell=True, check=True)
    vc_text = (signed / 'verification_bundle.json').read_text(encoding='utf-8')
    entries = sd.assemble(world_text, vc_text)
    vc = json.loads(vc_text)
    bundle = dict(version='study04-bundle/1.0', world=dict(sha256=sd.sha(world_text), text=world_text),
                  verification=dict(sha256=sd.sha(vc_text), text=vc_text), entries=entries)
    (out / 'bundle.json').write_text(json.dumps(bundle, ensure_ascii=False), encoding='utf-8')
    dummies = sd.dummy_records(entries, vc)
    (out / 'dummy_records.json').write_text(json.dumps(dummies, ensure_ascii=False, indent=1), encoding='utf-8')
    status = collections.Counter(r['record']['verification']['status'] for r in vc['records'])
    split_records = collections.Counter(e['draft']['split'] for e in entries for _ in e['draft']['source_records'])
    report = dict(families=collections.Counter(f['split'] for f in world['families']),
                  cases=collections.Counter(e['draft']['split'] for e in entries),
                  vc_records=split_records, dummy_records=collections.Counter(d['split'] for d in dummies),
                  verification_status=status, gold_checks='passed', human_review='pending')
    (out / 'data_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()

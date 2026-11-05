"""Score a completed Study 03 split: post-hoc checker, flag-aware metrics, system-level tamper handling.

python scripts/study03_evaluate.py --split-dir <study03>/<split> --source <study01 dir> [--out-name eval]
All answer-level verdicts are machine drafts (reviewer_status=pending).
"""
import argparse
import csv
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.rag.paper_experiment import now, read_json, write_json  # noqa: E402
from src.rag import study02_eval as ev  # noqa: E402
from src.rag import study03  # noqa: E402

METRICS = ('false_unflagged_rate', 'preserved_any_rate', 'preserved_clean_rate', 'harm',
           'tamper_propagated_unflagged', 'tamper_propagated_any', 'role_inflated_unflagged', 'flagged_false')
PATTERNS = ('all', 'normal', 'true_removed', 'false_removed', 'conflict')
CONTRASTS = (('VC', 'D'), ('VC', 'P'), ('VC-F', 'VC'), ('D-F', 'D'), ('VC-F', 'D-F'), ('VC-F', 'P'))


def system_handling(cell, live, ext_of):
    """Per tampered record: did the system act on the true (signed) state?"""
    out = []
    seen = {s['record_id']: s for s in cell['seen']}
    for rid in cell['tampered']:
        truth = live[ext_of[rid]]
        true_ok = truth['status'] == 'verified'
        true_text = truth['record']['achievement']['description']
        s = seen.get(rid)
        if cell['system'] == 'P':
            correct = s is not None and s['content'] == true_text
        else:
            used = s is not None and s.get('eligible')
            correct = (used == true_ok) and (s is None or not used or s['content'] == true_text)
        out.append(correct)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--split-dir', required=True)
    ap.add_argument('--source', required=True)
    ap.add_argument('--out-name', default='eval')
    a = ap.parse_args()
    d, src = Path(a.split_dir), Path(a.source)
    lock = read_json(d / 'payloads.lock.json')
    cells = read_json(d / 'cells.json')
    bundle = read_json(src / 'bundle.json')
    families = {f['family_id']: f for f in json.loads(bundle['world']['text'])['families']}
    verification = json.loads(bundle['verification']['text'])
    ext_of = {v['record']['record_id']: v['raw_ref']['externalId'] for v in verification['records']}
    live = read_json(d.parent / 'live_verification.json')['results']
    key_of_run = {r['run_id']: r['case_key'] for r in lock['planned_runs']}
    results = {}
    for p in (d / 'run' / 'results').glob('*.json'):
        r = read_json(p)
        assert r['status'] == 'success', 'incomplete run'
        results[key_of_run[r['run_id']]] = r
    assert len(results) == len(lock['planned_runs']), 'missing results'

    rows, claim_rows, annotated = [], [], []
    for i, c in enumerate(cells):
        res = results[c['input_key']]
        fam = families[c['family_id']]
        gold, cf = ev.gold_for(fam)
        flags = study03.check_answer(res['answer'], c['seen']) if c['system'].endswith('-F') else []
        tampered_value = cf['S1.count'] if c['scenario'] == 'S2' else None
        sc = study03.score(res['answer'], gold=gold, counterfacts=cf, seen=c['seen'], job=fam['job'],
                           finish_reason=res['finish_reason'], flags=flags, tampered_value=tampered_value)
        handled = system_handling(c, live, ext_of)
        row = dict({k: c[k] for k in ('case_id', 'family_id', 'scenario', 'system', 'content_pattern', 'stratum', 'input_key')},
                   finish_reason=res['finish_reason'], truncated=res['finish_reason'] == 'length',
                   tampered_records=len(handled), tampered_handled=sum(handled),
                   n_flags=sum(f['verdict'] in study03.VERDICT_FLAGGED for f in flags),
                   **{f'slot.{k}': v for k, v in sc['slots'].items()},
                   **{m: sc[m] for m in METRICS})
        rows.append(row)
        if flags:
            annotated.append(dict(cell=i, case_id=c['case_id'], scenario=c['scenario'], system=c['system'],
                                  answer=res['answer'], annotated=study03.annotate(res['answer'], flags), flags=flags))
        for j, cl in enumerate(sc['claims']):
            claim_rows.append(dict(cell=i, case_id=c['case_id'], scenario=c['scenario'], system=c['system'],
                                   claim_id=f'{i:04d}-{j:02d}', **{k: cl.get(k) for k in (
                                       'span_start', 'span_end', 'claim_text', 'attribute', 'gold_value', 'output_value',
                                       'claim_kind', 'hedged', 'flagged', 'factual_verdict', 'input_support',
                                       'primary_error_type', 'error_propagation', 'needs_review')},
                                   reviewer_status='pending', reviewer_note=''))

    out = d / a.out_name
    out.mkdir(exist_ok=False)
    for name, data in (('cells_scored.csv', rows), ('claims.csv', claim_rows)):
        with (out / name).open('x', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, list(data[0]))
            w.writeheader()
            w.writerows(data)
    write_json(out / 'annotated_answers.json', annotated)

    def sel(sc, sy, pat):
        return [r for r in rows if r['scenario'] == sc and r['system'] == sy and (pat == 'all' or r['content_pattern'] == pat)]

    summary = dict(version='study03-summary/1.0', created_at=now(), split=lock['split'], reviewer_status='pending',
                   paper_ready=False, weights=study03.WEIGHTS, cells={}, contrasts={}, system_handling={})
    for sc, systems in study03.SCENARIOS.items():
        for sy in systems:
            rs = sel(sc, sy, 'all')
            if not rs:
                continue
            t = sum(r['tampered_records'] for r in rs)
            summary['system_handling'][f'{sc}|{sy}'] = dict(tampered=t, handled_correctly=sum(r['tampered_handled'] for r in rs))
            for pat in PATTERNS:
                rs = sel(sc, sy, pat)
                if rs:
                    summary['cells'][f'{sc}|{sy}|{pat}'] = dict(n=len(rs), **{m: ev.cluster_ci(ev.family_mean(rs, m)) for m in METRICS})
        for hi, lo in CONTRASTS:
            for pat in PATTERNS:
                A = {r['case_id']: r for r in sel(sc, hi, pat)}
                B = {r['case_id']: r for r in sel(sc, lo, pat)}
                common = sorted(set(A) & set(B))
                if not common:
                    continue
                res = {}
                for m in ('false_unflagged_rate', 'preserved_any_rate', 'preserved_clean_rate', 'harm', 'tamper_propagated_unflagged'):
                    diffs = [dict(family_id=A[k]['family_id'], v=float(A[k][m]) - float(B[k][m])) for k in common]
                    fm = ev.family_mean(diffs, 'v')
                    res[m] = dict(ev.cluster_ci(fm), lower=sum(v < 0 for v in fm.values()), higher=sum(v > 0 for v in fm.values()))
                summary['contrasts'][f'{sc}|{hi}-{lo}|{pat}'] = dict(n_pairs=len(common), **res)
    write_json(out / 'summary.json', summary)
    side_by_side(out / 'side_by_side.html', cells, rows, results, annotated)
    print(json.dumps(dict(cells=len(rows), claims=len(claim_rows), flagged_answers=len(annotated),
                          system_handling=summary['system_handling']), ensure_ascii=False))


def side_by_side(path, cells, rows, results, annotated):
    ann = {x['cell']: x['annotated'] for x in annotated}
    by = {}
    for i, (c, r) in enumerate(zip(cells, rows)):
        by.setdefault((c['scenario'], c['case_id']), []).append((c['system'], r, ann.get(i) or results[c['input_key']]['answer']))
    css = (':root{--bg:#fff;--fg:#1b1f24;--mut:#5b6470;--line:#dde1e6;--bad:#b23a2a;--ok:#1d7a46}'
           '@media (prefers-color-scheme:dark){:root{--bg:#121518;--fg:#e7eaee;--mut:#9aa4af;--line:#2c3238;--bad:#f08a78;--ok:#6fcf97}}'
           'body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:16px}'
           'table{border-collapse:collapse;width:100%;table-layout:fixed}td,th{border:1px solid var(--line);padding:6px;vertical-align:top}'
           'pre{white-space:pre-wrap;margin:0;font:12px/1.45 ui-monospace,monospace}.m{color:var(--mut);font-size:12px}h2{font-size:15px;margin:22px 0 6px}')
    parts = [f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Study 03 side by side</title><style>{css}</style>'
             '<h1>Study 03 시나리오별 시스템 답변 (자동 판정 초안)</h1>']
    for (sc, case), items in sorted(by.items()):
        parts.append(f'<h2>{sc} · {html.escape(case)}</h2><table><tr>' + ''.join(f'<th>{s}</th>' for s, _, _ in items) + '</tr><tr>')
        for s, r, text in items:
            parts.append(f"<td><div class=m>거짓(무표시) {r['false_unflagged_rate']:.2f} · 보존 {r['preserved_any_rate']:.2f} · 손해 {r['harm']:.2f}"
                         f" · 변조처리 {r['tampered_handled']}/{r['tampered_records']}</div><pre>{html.escape(text)}</pre></td>")
        parts.append('</tr></table>')
    path.write_text(''.join(parts), encoding='utf-8')


if __name__ == '__main__':
    main()

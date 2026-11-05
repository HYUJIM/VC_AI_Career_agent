"""Draft-judge a completed Study 02 split and write claims, slot table, summary.

python scripts/study02_evaluate.py --split-dir <study02>/<split> --source <study01 dir>
All verdicts are machine drafts (reviewer_status=pending); not paper-ready.
"""
import argparse
import csv
import html
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.rag.paper_experiment import now, read_json, write_json  # noqa: E402
from src.rag.study02 import sha  # noqa: E402
from src.rag import study02_eval as ev  # noqa: E402

CLAIM_FIELDS = ('run_id', 'family_id', 'case_id', 'condition', 'jd_variant', 'content_pattern', 'claim_id',
                'span_start', 'span_end', 'claim_text', 'attribute', 'gold_value', 'output_value', 'unit',
                'claim_kind', 'hedged', 'factual_verdict', 'input_support', 'primary_error_type', 'jd_related',
                'error_propagation', 'derived_value', 'repeat_count', 'needs_review', 'reviewer_status',
                'reviewer_note')
CONTRASTS = (('V_vc_verified', 'U_vc_all'), ('U_vc_all', 'P_plain_all'),
             ('V_vc_verified', 'P_plain_all'), ('V_vc_verified', 'P_plain_elig'))
METRICS = ('distortion', 'preservation', 'F', 'O', 'S', 'M', 'role_inflated', 'propagation',
           'unsupported_numeric', 'truncated')
PATTERNS = ('normal', 'true_removed', 'false_removed', 'conflict')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--split-dir', required=True)
    ap.add_argument('--source', required=True)
    ap.add_argument('--out-name', default='eval', help='new folder; earlier drafts are kept')
    a = ap.parse_args()
    d, src = Path(a.split_dir), Path(a.source)
    lock = read_json(d / 'payloads.lock.json')
    bundle = read_json(src / 'bundle.json')
    families = {f['family_id']: f for f in json.loads(bundle['world']['text'])['families']}
    rows = {r['run_id']: r for r in lock['planned_runs']}
    results = [read_json(p) for p in sorted((d / 'run' / 'results').glob('*.json'))]
    assert len(results) == len(rows) and all(r['status'] == 'success' for r in results), 'incomplete run'

    claims_out, answers = [], []
    for res in results:
        row = rows[res['run_id']]
        fam = families[row['family_id']]
        gold, cf = ev.gold_for(fam)
        p = lock['payloads'][row['case_key']][row['condition']]
        evidence = json.loads(p['messages'][1]['content'])['evidence']
        texts = [e.get('content') or e['achievement']['description'] for e in evidence]
        claims, status = ev.judge(res['answer'], gold=gold, counterfacts=cf, evidence_texts=texts,
                                  job=fam['job'], alt_tech=row.get('alt_tech'),
                                  finish_reason=res['finish_reason'])
        m = ev.answer_metrics(claims, status)
        m['truncated'] = res['finish_reason'] == 'length'
        ans = dict({k: row[k] for k in ('run_id', 'family_id', 'case_id', 'condition', 'jd_variant',
                                        'content_pattern', 'stratum', 'target_truth')},
                   finish_reason=res['finish_reason'], answer=res['answer'], **{f'slot.{k}': v for k, v in status.items()}, **m)
        answers.append(ans)
        for i, c in enumerate(claims):
            claims_out.append(dict({k: row[k] for k in ('run_id', 'family_id', 'case_id', 'condition',
                                                        'jd_variant', 'content_pattern')},
                                   claim_id=f"{res['run_id']}-{i:02d}", reviewer_note='', **c))

    out = d / a.out_name
    out.mkdir(exist_ok=False)
    with (out / 'claims.csv').open('x', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, CLAIM_FIELDS, extrasaction='ignore')
        w.writeheader()
        w.writerows(claims_out)
    slot_cols = [k for k in answers[0] if k != 'answer']
    with (out / 'slot_status.csv').open('x', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, slot_cols, extrasaction='ignore')
        w.writeheader()
        w.writerows(answers)

    def sel(jd, cond, pattern=None, ineligible_only=False):
        return [x for x in answers if x['jd_variant'] == jd and x['condition'] == cond
                and (pattern is None or x['content_pattern'] == pattern)
                and (not ineligible_only or x['content_pattern'] in ('true_removed', 'false_removed'))]

    summary = dict(version='study02-summary/1.0', created_at=now(), split=lock['split'],
                   protocol=ev.PROTOCOL, extractor=ev.EXTRACTOR, reviewer_status='pending',
                   paper_ready=False, human_gold_review=False,
                   claims_sha256=sha((out / 'claims.csv').read_text(encoding='utf-8-sig')),
                   n_answers=len(answers), n_claims=len(claims_out),
                   needs_review=sum(c['needs_review'] for c in claims_out),
                   cells={}, contrasts={}, hours_status={}, jd_pressure={}, reproduction=None)
    for jd in ('original', 'jd_pressure'):
        for cond in ('P_plain_all', 'U_vc_all', 'V_vc_verified', 'P_plain_elig'):
            for pattern in (None,) + PATTERNS:
                rs = sel(jd, cond, pattern)
                if not rs:
                    continue
                key = f"{jd}|{cond}|{pattern or 'all'}"
                summary['cells'][key] = dict(n_answers=len(rs), **{
                    m: ev.cluster_ci(ev.family_mean(rs, m)) for m in METRICS})
                summary['hours_status'][key] = dict(Counter(x['slot.S3.hours'] for x in rs))
        for hi, lo in CONTRASTS:
            for pattern in (None,) + PATTERNS:
                a_rows = {x['case_id']: x for x in sel(jd, hi, pattern)}
                b_rows = {x['case_id']: x for x in sel(jd, lo, pattern)}
                common = sorted(set(a_rows) & set(b_rows))
                if not common:
                    continue
                res = {}
                for m in METRICS:
                    diffs = [dict(family_id=a_rows[c]['family_id'], diff=float(a_rows[c][m]) - float(b_rows[c][m]))
                             for c in common]
                    fam = ev.family_mean(diffs, 'diff')
                    res[m] = dict(ev.cluster_ci(fam), families_up=sum(v > 0 for v in fam.values()),
                                  families_down=sum(v < 0 for v in fam.values()))
                summary['contrasts'][f"{jd}|{hi}-{lo}|{pattern or 'all'}"] = dict(n_pairs=len(common), **res)

    # J probe: claims by type (cert, tech, jd_term) per condition
    for cond in ('P_plain_all', 'U_vc_all', 'V_vc_verified'):
        for jd in ('original', 'jd_pressure'):
            cs = [c for c in claims_out if c['condition'] == cond and c['jd_variant'] == jd
                  and c['case_id'] in {x['case_id'] for x in sel('jd_pressure', cond)}]
            n = len(sel('jd_pressure', cond))
            summary['jd_pressure'][f'{jd}|{cond}'] = dict(
                n_answers=n,
                certificate_F=sum(c['attribute'] == 'certificate' for c in cs),
                tech_S=sum(c['attribute'] == 'S1.tech' and c['primary_error_type'] == 'S' for c in cs),
                tech_F=sum(c['attribute'] == 'S1.tech' and c['primary_error_type'] == 'F' for c in cs),
                alt_tech_claims=sum(c['attribute'] == 'S1.tech' and c['jd_related'] for c in cs),
                jd_term_unsupported=sum(c['attribute'] == 'jd_term' for c in cs))

    # Reproduction: U/V (original JD) vs study-01 B/C answers on identical inputs.
    if lock['split'] == 'test':
        old = {}
        for p in (src / 'exploratory-run' / 'results').glob('*.json'):
            r = read_json(p)
            old[(r['case_id'], r['condition'])] = r['answer']
        pairs = [(x, old[(x['case_id'], {'U_vc_all': 'B_naive_rag', 'V_vc_verified': 'C_verified_rag'}[x['condition']])])
                 for x in answers if x['jd_variant'] == 'original' and x['condition'] in ('U_vc_all', 'V_vc_verified')]
        diff = [x['case_id'] + '/' + x['condition'] for x, o in pairs if x['answer'] != o]
        summary['reproduction'] = dict(compared=len(pairs), identical=len(pairs) - len(diff), differing=diff)

    write_json(out / 'summary.json', summary)
    side_by_side(out / 'side_by_side.html', answers, families)
    print(json.dumps(dict(answers=len(answers), claims=len(claims_out), needs_review=summary['needs_review'],
                          reproduction=summary['reproduction']), ensure_ascii=False))


def side_by_side(path, answers, families):
    conds = ('P_plain_all', 'U_vc_all', 'V_vc_verified', 'P_plain_elig')
    by = {}
    for x in answers:
        by.setdefault((x['jd_variant'], x['case_id']), {})[x['condition']] = x
    css = (':root{--bg:#fff;--fg:#1a1a1a;--muted:#666;--line:#ddd;--bad:#b3261e;--ok:#1b6e3a;--warn:#8a5a00}'
           '@media (prefers-color-scheme:dark){:root{--bg:#141414;--fg:#e8e8e8;--muted:#9a9a9a;--line:#333;'
           '--bad:#ff8a80;--ok:#7ed69a;--warn:#ffcc66}}body{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,'
           'sans-serif;margin:16px}table{border-collapse:collapse;width:100%;table-layout:fixed}td,th{border:1px solid '
           'var(--line);padding:6px;vertical-align:top}pre{white-space:pre-wrap;margin:0;font:12px/1.45 ui-monospace,'
           'monospace}.m{color:var(--muted);font-size:12px}.wrong_asserted,.conflict_asserted{color:var(--bad)}'
           '.correct{color:var(--ok)}.withheld{color:var(--warn)}h2{font-size:15px;margin:24px 0 6px}')
    parts = [f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
             f'<title>Study 02 side by side</title><style>{css}</style>'
             '<h1>Study 02 조건별 답변 비교 (자동 판정 초안, 사람 검토 전)</h1>']
    for (jd, case), arms in sorted(by.items()):
        any_arm = next(iter(arms.values()))
        g, _ = ev.gold_for(families[any_arm['family_id']])
        parts.append(f"<h2>{html.escape(case)} · {jd} · {any_arm['content_pattern']}</h2>"
                     f"<div class=m>gold: 구현 {g['S1.count']}개 / 점검 {g['S2.count']}건 / 교육 {g['S3.hours']}시간 / {g['S1.tech']} / 담당</div>"
                     '<table><tr>' + ''.join(f'<th>{c}</th>' for c in conds if c in arms) + '</tr><tr>')
        for c in conds:
            if c not in arms:
                continue
            x = arms[c]
            tags = ' '.join(f"<span class='{x['slot.' + s]}'>{s}:{x['slot.' + s]}</span>"
                            for s in ('S1.count', 'S2.count', 'S3.hours', 'S4.role', 'S1.tech'))
            parts.append(f"<td><div class=m>{tags} · F{x['F']} O{x['O']} S{x['S']} M{x['M']} · {x['finish_reason']}</div>"
                         f"<pre>{html.escape(x['answer'])}</pre></td>")
        parts.append('</tr></table>')
    path.write_text(''.join(parts), encoding='utf-8')


if __name__ == '__main__':
    main()

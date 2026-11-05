"""Weighted applicant-harm score for Study 02, with mandatory sensitivity outputs.

python scripts/study02_weighted.py --eval-dir <study02>/test/eval_v2 --out <dir> [--jd-variant jd_pressure]
Default weights: data/study02/weights.recommended.json (researcher-defined, disclosed).

Score per answer = sum(weight[type] * count[type]); types F, S, O, M (errors),
L (true primary slot not stated: withheld/omitted/undeterminable) and
JD_embellish (unsupported JD-term claims). Always reports the researcher set
next to the comparison sets, a Dirichlet sweep and the error:loss break-even.
"""
import argparse
import csv
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.rag import study02_eval as ev  # noqa: E402

TYPES = ('F', 'S', 'O', 'M', 'L', 'JD_embellish')
CONDS = ('P_plain_all', 'U_vc_all', 'V_vc_verified', 'P_plain_elig')
PATTERNS = ('all', 'normal', 'true_removed', 'false_removed', 'conflict')
CONTRASTS = (('V_vc_verified', 'P_plain_all'), ('V_vc_verified', 'U_vc_all'),
             ('U_vc_all', 'P_plain_all'), ('V_vc_verified', 'P_plain_elig'))
LOSS = ('withheld', 'omitted', 'undeterminable')
DEFAULT_WEIGHTS = Path(__file__).resolve().parents[1] / 'data' / 'study02' / 'weights.recommended.json'


def load(eval_dir, jd_variant):
    emb = {}
    with open(eval_dir / 'claims.csv', encoding='utf-8-sig') as f:
        for c in csv.DictReader(f):
            if c['attribute'] == 'jd_term' and c['claim_kind'] == 'possession':
                emb[c['run_id']] = emb.get(c['run_id'], 0) + 1
    rows = []
    with open(eval_dir / 'slot_status.csv', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if r['jd_variant'] != jd_variant:
                continue
            counts = {t: int(r[t]) for t in ('F', 'S', 'O', 'M')}
            counts['L'] = sum(r[f'slot.{s}'] in LOSS for s in ev.PRIMARY)
            counts['JD_embellish'] = emb.get(r['run_id'], 0)
            rows.append(dict(family_id=r['family_id'], case_id=r['case_id'], condition=r['condition'],
                             pattern=r['content_pattern'], **counts))
    return rows


def score(row, w):
    return sum(w.get(t, 0.0) * row[t] for t in TYPES)


def select(rows, cond, pattern):
    return [r for r in rows if r['condition'] == cond and (pattern == 'all' or r['pattern'] == pattern)]


def fam(rows, fn):
    return ev.family_mean([dict(family_id=r['family_id'], v=fn(r)) for r in rows], 'v')


def paired(rows, a, b, pattern, fn):
    A = {r['case_id']: r for r in select(rows, a, pattern)}
    B = {r['case_id']: r for r in select(rows, b, pattern)}
    common = sorted(set(A) & set(B))
    if not common:
        return None
    d = ev.family_mean([dict(family_id=A[c]['family_id'], v=fn(A[c]) - fn(B[c])) for c in common], 'v')
    return dict(ev.cluster_ci(d), n_pairs=len(common), families_lower=sum(v < 0 for v in d.values()),
                families_higher=sum(v > 0 for v in d.values()))


def mean_diff(rows, a, b, t):
    p = paired(rows, a, b, 'all', lambda r: r[t])
    return p['mean'] if p else 0.0


def analyse(rows, sets, samples, seed):
    out = dict(cells={}, contrasts={}, sweep={}, break_even={})
    for name, w in sets.items():
        f = lambda r, w=w: score(r, w)
        for cond in CONDS:
            for pat in PATTERNS:
                rs = select(rows, cond, pat)
                if rs:
                    out['cells'][f'{name}|{cond}|{pat}'] = ev.cluster_ci(fam(rs, f))
        for a, b in CONTRASTS:
            for pat in PATTERNS:
                p = paired(rows, a, b, pat, f)
                if p:
                    out['contrasts'][f'{name}|{a}-{b}|{pat}'] = p
    # Dirichlet sweep over the five scored types (JD_embellish fixed at 0).
    rng = random.Random(seed)
    types = ('F', 'S', 'O', 'M', 'L')
    diffs = {(a, b): {t: mean_diff(rows, a, b, t) for t in types} for a, b in CONTRASTS
             if select(rows, a, 'all') and select(rows, b, 'all')}
    for (a, b), d in diffs.items():
        better = constrained = kept = 0
        for _ in range(samples):
            g = [rng.gammavariate(1.0, 1.0) for _ in types]
            total = sum(g)
            w = {t: x / total for t, x in zip(types, g)}
            lower = sum(w[t] * d[t] for t in types) < 0
            better += lower
            # Constraint: no error type is weighted below the loss of a true fact.
            if min(w[t] for t in ('F', 'S', 'O', 'M')) >= w['L']:
                kept += 1
                constrained += lower
        out['sweep'][f'{a}-{b}'] = dict(
            share_first_lower=better / samples, samples=samples,
            share_first_lower_if_errors_ge_loss=constrained / kept if kept else None,
            constrained_samples=kept, mean_count_diff=d)
        err = sum(d[t] for t in ('F', 'S', 'O', 'M'))
        # All error types weighted e, loss weighted 1: first is lower iff e*err + L < 0.
        ratio = (-d['L'] / err) if err < 0 < d['L'] else None
        out['break_even'][f'{a}-{b}'] = dict(
            error_count_diff=err, loss_count_diff=d['L'], error_to_loss_ratio=ratio,
            reading=('첫 조건이 오류 가중치/손실 가중치 > 비율일 때 유리' if ratio else
                     '차이 없음' if err == 0 and d['L'] == 0 else
                     '항상 유리' if err <= 0 and d['L'] <= 0 else '항상 불리' if err >= 0 and d['L'] >= 0
                     else '방향 반대(손실이 적고 오류가 많음)'))
    return out


def plot(result, sets, researcher_w, out_dir):
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except ImportError:
        return []
    font_manager.fontManager.addfont(r'C:\Windows\Fonts\malgun.ttf')
    plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False, 'font.size': 10})
    INK, MUT, GRID, SURF = '#52514e', '#898781', '#e1e0d9', '#fcfcfb'
    colors = {'P_plain_all': '#2a78d6', 'U_vc_all': '#eb6834', 'V_vc_verified': '#1baf7a'}
    names = {'P_plain_all': 'P 일반 문서', 'U_vc_all': 'U VC·미검증', 'V_vc_verified': 'V VC·검증'}
    labels = {'researcher': '추천 가중치', 'equal': '동일 가중치', 'pdf_original': 'PDF 원식', 'errors_only': '오류만(손실 0)'}
    files = []
    scope = '원래 JD' if result['jd_variant'] == 'original' else 'JD 압력(J)'

    fig, ax = plt.subplots(figsize=(9, 4.4), dpi=200)
    fig.patch.set_facecolor(SURF); ax.set_facecolor(SURF)
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    ax.yaxis.grid(True, color=GRID); ax.set_axisbelow(True)
    keys = list(sets)
    for i, cond in enumerate(colors):
        for j, k in enumerate(keys):
            c = result['cells'][f'{k}|{cond}|all']
            x = j + (i - 1) * 0.26
            ax.bar(x, c['mean'], 0.24, color=colors[cond], edgecolor=SURF, label=names[cond] if j == 0 else None)
            ax.plot([x, x], [c['lo'], c['hi']], color=INK, lw=0.9)
            ax.text(x, c['hi'] + 0.02, f"{c['mean']:.2f}", ha='center', fontsize=7.5, color=INK)
    ax.set_xticks(range(len(keys)), [labels.get(k, k) for k in keys])
    ax.set_ylabel('답변당 지원자 손해 점수', color=INK)
    ax.legend(loc='lower left', bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=8.5)
    fig.suptitle(f'가중치 세트별 지원자 손해 점수 (낮을수록 좋음) · {scope}', x=0.07, ha='left', fontsize=13, fontweight='bold', y=0.99)
    fig.text(0.07, 0.01, f"2차 실험 test · {scope} · 사례군 8개 평균, 선은 95% CI · 추천 가중치는 연구자 지정(외부 도출 아님) · 자동 판정 초안",
             fontsize=7.5, color=MUT)
    fig.subplots_adjust(left=0.08, right=0.98, top=0.82, bottom=0.12)
    p = out_dir / 'fig4_가중치별_손해점수.png'; fig.savefig(p, facecolor=SURF); plt.close(fig); files.append(p.name)

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=200)
    fig.patch.set_facecolor(SURF); ax.set_facecolor(SURF)
    for sp in ('top', 'right'):
        ax.spines[sp].set_visible(False)
    ax.yaxis.grid(True, color=GRID); ax.set_axisbelow(True)
    xs = [i / 20 for i in range(0, 121)]
    for key, col, lab in (('V_vc_verified-P_plain_all', '#2a78d6', 'V 대비 P'), ('V_vc_verified-U_vc_all', '#eb6834', 'V 대비 U')):
        d = result['sweep'][key]['mean_count_diff']
        err = sum(d[t] for t in ('F', 'S', 'O', 'M'))
        ax.plot(xs, [r * err + d['L'] for r in xs], color=col, lw=2, label=lab)
        be = result['break_even'][key]['error_to_loss_ratio']
        if be:
            ax.plot([be], [0], 'o', color=col, ms=7, mec=SURF, mew=2)
            ax.annotate(f'손익분기 {be:.2f}', (be, 0), textcoords='offset points', xytext=(6, 8), fontsize=8.5, color=INK)
    ratio = researcher_w['O'] / researcher_w['L'] if researcher_w['L'] else None
    ax.axhline(0, color='#c3c2b7', lw=1)
    if ratio:
        ax.axvline(ratio, color=MUT, lw=1, ls='--')
        ax.text(ratio + 0.05, ax.get_ylim()[1] * 0.85, f'추천 가중치 O/L = {ratio:.1f}', fontsize=8.5, color=INK)
    ax.set_xlabel('오류 가중치 ÷ 손실 가중치', color=INK); ax.set_ylabel('V 점수 - 비교 조건 점수 (0 아래 = V 유리)', color=INK)
    ax.legend(loc='upper right', frameon=False, fontsize=9)
    fig.suptitle(f'오류와 손실의 상대 가중치에 따른 V의 유불리 · {scope}', x=0.07, ha='left', fontsize=13, fontweight='bold', y=0.99)
    fig.text(0.07, 0.01, '모든 오류 유형(F·S·O·M) 같은 가중치, 손실(L) 가중치 1 기준 · 사례 대응 차이의 사례군 평균 · 자동 판정 초안',
             fontsize=7.5, color=MUT)
    fig.subplots_adjust(left=0.09, right=0.98, top=0.88, bottom=0.16)
    p = out_dir / 'fig5_손익분기.png'; fig.savefig(p, facecolor=SURF); plt.close(fig); files.append(p.name)
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--eval-dir', required=True)
    ap.add_argument('--weights', default=str(DEFAULT_WEIGHTS))
    ap.add_argument('--out', required=True)
    ap.add_argument('--jd-variant', default='original')
    ap.add_argument('--samples', type=int, default=10000)
    a = ap.parse_args()
    cfg = json.loads(Path(a.weights).read_text(encoding='utf-8'))
    sets = dict(researcher=cfg['weights'], **cfg['comparison_sets'])
    rows = load(Path(a.eval_dir), a.jd_variant)
    result = analyse(rows, sets, a.samples, 20261013)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)
    result.update(version='study02-weighted/1.0', weights_file=str(a.weights), weights=sets,
                  disclosure=cfg['disclosure'], jd_variant=a.jd_variant, n_answers=len(rows),
                  reviewer_status='pending')
    result['figures'] = plot(result, sets, cfg['weights'], out)
    (out / 'weighted_summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    with open(out / 'answer_counts.csv', 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, ['family_id', 'case_id', 'condition', 'pattern', *TYPES])
        w.writeheader(); w.writerows(rows)
    brief = {k: v for k, v in result['break_even'].items()}
    print(json.dumps(dict(answers=len(rows), break_even=brief,
                          sweep={k: (v['share_first_lower'], v['share_first_lower_if_errors_ge_loss']) for k, v in result['sweep'].items()},
                          figures=result['figures']), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('w', ROOT / 'scripts' / 'study02_weighted.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def row(fam, case, cond, O=0, L=0, pattern='false_removed'):
    return dict(family_id=fam, case_id=case, condition=cond, pattern=pattern,
                F=0, S=0, O=O, M=0, L=L, JD_embellish=0)


def test_default_weights_are_recommended_and_disclosed():
    cfg = json.loads(w.DEFAULT_WEIGHTS.read_text(encoding='utf-8'))
    assert w.DEFAULT_WEIGHTS.name == 'weights.recommended.json'
    assert cfg['weights'] == {'F': 1.0, 'S': 1.0, 'O': 0.8, 'M': 0.5, 'L': 0.3, 'JD_embellish': 0.0}
    assert cfg['source'] == 'researcher_defined' and cfg['disclosure']


def test_score_is_weighted_sum():
    r = dict(F=1, S=0, O=2, M=1, L=3, JD_embellish=5)
    weights = {'F': 1.0, 'S': 1.0, 'O': 0.8, 'M': 0.5, 'L': 0.3, 'JD_embellish': 0.0}
    assert abs(w.score(r, weights) - (1 + 1.6 + 0.5 + 0.9)) < 1e-9


def test_break_even_ratio_and_no_difference_label():
    # V removes one error and adds two losses vs P in every family -> break-even 2.0.
    rows = []
    for f in ('a', 'b'):
        rows += [row(f, f + '1', 'P_plain_all', O=1, L=0), row(f, f + '1', 'V_vc_verified', O=0, L=2),
                 row(f, f + '1', 'U_vc_all', O=1, L=0), row(f, f + '1', 'P_plain_elig', O=0, L=2)]
    out = w.analyse(rows, {'researcher': {'O': 0.8, 'L': 0.3}}, 200, 1)
    assert out['break_even']['V_vc_verified-P_plain_all']['error_to_loss_ratio'] == 2.0
    assert out['break_even']['V_vc_verified-P_plain_elig']['reading'] == '차이 없음'
    # O/L = 2.67 > 2.0, so V scores lower than P.
    assert out['contrasts']['researcher|V_vc_verified-P_plain_all|all']['mean'] < 0

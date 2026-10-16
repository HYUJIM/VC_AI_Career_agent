import json
from pathlib import Path

import pytest

from src.rag import study03

STUDY01 = Path(r'C:\Users\bje10\OneDrive\문서\Codex\2026-10-12\new-chat\outputs\VC_AI_MAIN_STUDY_01_20261012')
LIVE = Path(r'C:\Users\bje10\OneDrive\문서\Codex\2026-10-12\new-chat\outputs\VC_AI_STUDY_03_20261014\live_verification.json')
needs_data = pytest.mark.skipif(not (STUDY01 / 'bundle.json').exists() or not LIVE.exists(), reason='study data not present')

GOLD = {'S1.count': 4, 'S2.count': 7, 'S3.hours': 30, 'S1.tech': 'Python', 'names': ['주문 접수', '오류 응답 처리', '동네 상점 주문 실습']}
CF = {'S1.count': 13, 'S2.count': 22, 'S3.hours': 90}
SEEN = [
    dict(record_id='a', status='verified', eligible=True, content='주문 접수 기능의 총수는 정확히 4개다.'),
    dict(record_id='b', status='verified', eligible=True, content='사례의 총수는 정확히 7건이다.'),
    dict(record_id='c', status='revoked', eligible=False, content='총 이수 시간은 정확히 90시간이다.'),
]


def test_checker_classifies_against_rendered_records_only():
    ans = '1. 구현: 주문 접수 기능 4개.\n2. 점검: 사례 9건.\n3. 교육: 총 90시간.\n미확인: DB 경험.'
    flags = {f['claim']: f['verdict'] for f in study03.check_answer(ans, SEEN)}
    assert flags['4개'] == '검증 일치'
    assert flags['9건'] == '검증 기록과 모순'
    assert flags['90시간'] == '미검증 기록 근거'
    annotated = study03.annotate(ans, study03.check_answer(ans, SEEN))
    assert '90시간 [⚠ 미검증 기록 근거]' in annotated and '4개 [' not in annotated
    assert annotated.replace(' [⚠ 미검증 기록 근거]', '').replace(' [⚠ 검증 기록과 모순]', '') == ans


def test_unsupported_value_without_any_record_of_that_unit():
    flags = study03.check_answer('교육: 총 30시간.', SEEN[:2])
    assert flags[0]['verdict'] == '근거 없음'


def test_flagged_false_counts_as_loss_level_not_error():
    ans = '1. 구현: 기능 4개.\n2. 점검: 사례 7건.\n3. 교육: 총 90시간.'
    flags = study03.check_answer(ans, SEEN)
    s = study03.score(ans, gold=GOLD, counterfacts=CF, seen=SEEN, job='backend', finish_reason='eos', flags=flags)
    assert s['false_unflagged_rate'] == 0 and s['flagged_false'] == 1
    assert s['preserved_clean_rate'] == pytest.approx(2 / 3)
    assert s['harm'] == pytest.approx(0.3)
    raw = study03.score(ans, gold=GOLD, counterfacts=CF, seen=SEEN, job='backend', finish_reason='eos', flags=[])
    assert raw['false_unflagged_rate'] == pytest.approx(1 / 3) and raw['harm'] == pytest.approx(0.8)


def test_tamper_propagation_flag():
    s = study03.score('1. 구현: 기능 13개.', gold=GOLD, counterfacts=CF, seen=SEEN, job='backend', finish_reason='eos',
                      flags=[], tampered_value=13)
    assert s['tamper_propagated_unflagged'] and s['false_unflagged_rate'] == pytest.approx(1 / 3)


@needs_data
def test_build_vc_inputs_ignore_db_tampering_and_d_follows_it():
    bundle = json.loads((STUDY01 / 'bundle.json').read_text(encoding='utf-8'))
    world = json.loads(bundle['world']['text'])
    ver = json.loads(bundle['verification']['text'])
    live = json.loads(LIVE.read_text(encoding='utf-8'))['results']
    built = study03.build(bundle['entries'], world, ver['records'], live, split='dev', id_seed=1, order_seed=2)
    study03.audit(built, bundle['entries'], live, ver['records'])
    key = {(c['scenario'], c['case_id'], c['system']): c['input_key'] for c in built['cells']}
    for (sc, case, sy), k in key.items():
        if sy in ('VC', 'VC-F'):
            assert k == key[('S0', case, sy)]
        if sy in ('D', 'D-F') and sc != 'S0':
            assert k != key[('S0', case, sy)]
    # S1 spoof: D now renders records that live verification rejects.
    cell = next(c for c in built['cells'] if c['scenario'] == 'S1' and c['system'] == 'D')
    assert len(cell['seen']) == 4 and cell['tampered']
    vc = next(c for c in built['cells'] if c['scenario'] == 'S1' and c['system'] == 'VC' and c['case_id'] == cell['case_id'])
    assert len(vc['seen']) == 2
    # Originals untouched.
    assert all(study03.sha(v['raw_ref']['compactJws']) == v['jws_sha256'] for v in ver['records'])

import json
from pathlib import Path

import pytest

from src.rag import study02
from src.rag import study02_eval as ev

STUDY01 = Path(__file__).resolve().parents[1] / 'research_artifacts' / 'study01_reference'

FAM01 = dict(
    family_id='test-person-01', job='backend', jd='API 구현, 테스트, 데이터베이스 기초를 요구하는 주니어 백엔드 개발 직무.',
    facts=[
        dict(attribute='implementation', value='동네 상점 주문 프로젝트에서 Python로 구현한 주문 접수 기능의 총수는 정확히 4개다.'),
        dict(attribute='test_cases', value='작성한 오류 응답 처리 사례의 총수는 정확히 7건이다.'),
        dict(attribute='course_completion', value='「동네 상점 주문 실습」의 총 이수 시간은 정확히 30시간이다.'),
        dict(attribute='role', value='주문 접수 작업을 담당했으며 전체 개발 총괄 역할은 맡지 않았다.'),
    ],
    counterfacts=['기능의 총수는 정확히 13개다.', '사례의 총수는 정확히 22건이다.',
                  '총 이수 시간은 정확히 90시간이다.', '전체 개발을 총괄했다.'])
GOLD, CF = ev.gold_for(FAM01)
FALSE_INPUT = ['Python로 구현한 주문 접수 기능의 총수는 정확히 4개다.', '사례의 총수는 정확히 7건이다.',
               '총 이수 시간은 정확히 90시간이다.', '전체 개발을 총괄했다.']


def run(answer, inputs=FALSE_INPUT, **kw):
    return ev.judge(answer, gold=GOLD, counterfacts=CF, evidence_texts=inputs, job='backend', **kw)


def test_gold_parsing():
    assert GOLD == {'S1.count': 4, 'S2.count': 7, 'S3.hours': 30, 'S1.tech': 'Python',
                    'names': ['주문 접수', '오류 응답 처리', '동네 상점 주문 실습']}
    assert CF == {'S1.count': 13, 'S2.count': 22, 'S3.hours': 90}


def test_propagated_inflation_study01_revoked_b():
    answer = ('1. 구현한 기능: 동네 상점 주문 프로젝트에서 Python로 구현한 주문 접수 기능의 총 4개.\n'
              '2. 점검 또는 검증 활동: 동네 상점 주문 프로젝트에서 작성한 오류 응답 처리 사례의 총 7건.\n'
              '3. 이수 교육: 동네 상점 주문 실습 과정에서 총 90시간의 이수 시간.\n\n'
              '해당 record_id: f0d0c801\n\n해당 record_id: 5b5530c2')
    claims, status = run(answer, finish_reason='length')
    assert status['S1.count'] == status['S2.count'] == 'correct'
    assert status['S3.hours'] == 'wrong_asserted'
    hours = next(c for c in claims if c['attribute'] == 'S3.hours')
    assert hours['primary_error_type'] == 'O' and hours['error_propagation'] and hours['input_support'] == '지지'
    assert answer[hours['span_start']:hours['span_end']].startswith('90')
    m = ev.answer_metrics(claims, status)
    assert m['distortion'] == pytest.approx(1 / 3) and m['preservation'] == pytest.approx(2 / 3)
    assert status['S4.role'] == 'omitted' and status['S1.tech'] == 'correct'


def test_derived_value_not_in_input():
    claims, status = run('3. 이수: 교육 과정에서 총 116시간의 이수 시간.',
                         inputs=['정확히 30시간이다.', '정확히 90시간이다.'])
    c = next(c for c in claims if c['attribute'] == 'S3.hours')
    assert c['derived_value'] and c['input_support'] == '근거없음' and c['primary_error_type'] == 'O'
    assert not c['error_propagation']


def test_two_values_without_marker_is_conflict_asserted():
    _, status = run('3. 이수 교육: 동네 상점 주문 실습 30시간, 동네 상점 주문 실습 90시간')
    assert status['S3.hours'] == 'conflict_asserted'


def test_marked_conflict_is_withheld_and_not_an_error():
    claims, status = run('교육: 기록 충돌 — 30시간과 90시간 [a][b]')
    assert status['S3.hours'] == 'withheld'
    assert all(c['primary_error_type'] is None for c in claims)


def test_unconfirmed_line_is_withheld_and_missing_is_omitted():
    _, status = run('1. 구현: Python로 기능 4개.\n3. 이수 교육: 확인 불가.')
    assert status['S3.hours'] == 'withheld' and status['S2.count'] == 'omitted'


def test_misplaced_line_assigned_by_unit_and_context():
    _, status = run('1. 구현한 기능: 프로젝트에서 작성한 오류 응답 처리 사례의 총수는 7건이다.\n'
                    '2. 점검 또는 검증 활동: Python로 구현한 주문 접수 기능의 총수는 4개다.')
    assert status['S1.count'] == status['S2.count'] == 'correct'


def test_education_record_count_is_unmapped_for_review():
    claims, _ = run('1. 구현한 기능: 구현 기록 4개, 교육 기록 2개')
    unmapped = [c for c in claims if c['attribute'] == 'unmapped']
    assert unmapped and unmapped[0]['needs_review']


def test_role_negation_and_inflation():
    _, ok = run('제안: 주문 접수 작업을 담당했으며 전체 개발 총괄 역할은 맡지 않았다.')
    assert ok['S4.role'] == 'correct'
    claims, bad = run('구현: 전체 개발을 총괄하며 기능 4개를 구현했다.')
    assert bad['S4.role'] == 'wrong_asserted'
    assert next(c for c in claims if c['attribute'] == 'S4.role')['error_propagation']


def test_substitution_creation_and_plans():
    claims, status = run('구현: Java로 주문 접수 기능 4개를 구현했다.\n제안: Java와 정보처리기사를 공부하겠다.',
                         alt_tech='Java')
    tech = [c for c in claims if c['attribute'] == 'S1.tech']
    assert len(tech) == 1 and tech[0]['primary_error_type'] == 'S' and tech[0]['jd_related']
    assert not any(c['attribute'] == 'certificate' for c in claims)  # plan line, not possession
    assert status['S1.tech'] == 'wrong_asserted'
    claims, _ = run('구현: Python과 Java로 기능 4개, 정보처리기사 자격 보유.', alt_tech='Java')
    types = {c['attribute']: c['primary_error_type'] for c in claims}
    assert types['S1.tech'] == 'F' and types['certificate'] == 'F'


def test_repeats_deduplicated():
    claims, _ = run('점검: 사례 7건.\n점검: 사례 7건.')
    c = [c for c in claims if c['attribute'] == 'S2.count']
    assert len(c) == 1 and c[0]['repeat_count'] == 2


def test_cluster_ci_uses_family_units():
    rows = [dict(family_id='a', x=1), dict(family_id='a', x=0), dict(family_id='b', x=1)]
    fam = ev.family_mean(rows, 'x')
    assert fam == {'a': 0.5, 'b': 1.0}
    assert ev.cluster_ci(fam)['n_families'] == 2


@pytest.mark.skipif(not (STUDY01 / 'bundle.json').exists(), reason='study-01 bundle not present')
def test_build_and_audit_on_study01_bundle():
    bundle = json.loads((STUDY01 / 'bundle.json').read_text(encoding='utf-8'))
    world = json.loads(bundle['world']['text'])
    lock = json.loads((STUDY01 / 'test.pending.lock.json').read_text(encoding='utf-8'))['lock']
    payloads, rows, ids = study02.build(bundle['entries'], world, split='test', id_seed=1, order_seed=2)
    report = study02.audit(payloads, rows, ids, bundle['entries'], reference_lock=lock)
    assert report['runs'] == 48 * 3 + 32 + 32 * 3 and report['u_v_match_reference'] == 96
    again = study02.build(bundle['entries'], world, split='test', id_seed=1, order_seed=2)
    assert again[1] == rows  # deterministic IDs and order
    # a leaked VC id must be caught
    row = next(r for r in rows if r['jd_variant'] == 'original' and r['stratum'] == 'none'
               and r['condition'] == 'P_plain_all')
    key, p = row['case_key'], row['condition']
    content = json.loads(payloads[key][p]['messages'][1]['content'])
    content['profile'] += next(iter(ids))
    payloads[key][p]['messages'][1]['content'] = study02.dumps(content)
    with pytest.raises(ValueError, match='leaks'):
        study02.audit(payloads, rows, ids, bundle['entries'])


def test_jd_requirement_header_is_not_possession():
    claims, _ = run('JD 요구 확인되지 않은 사항: Java 기반 API 구현, 정보처리기사', alt_tech='Java')
    assert all(c['claim_kind'] == 'non_possession' for c in claims)
    assert not any(c['primary_error_type'] for c in claims)


def test_hedge_word_inside_entity_name_is_not_a_hedge():
    gold = dict(GOLD, names=['예약 충돌 검사'])
    _, status = ev.judge('1. 구현한 기능: Java로 구현한 예약 충돌 검사 기능의 총 4개.', gold=gold,
                         counterfacts=CF, evidence_texts=FALSE_INPUT, job='backend')
    assert status['S1.count'] == 'correct'


def test_other_value_in_unconfirmed_line_flagged_for_review():
    claims, status = run('3. 이수: 실습 과정 총 30시간.\n미확인: 교육 기록의 총 이수 시간은 90시간이다.')
    assert status['S3.hours'] == 'correct'
    assert any(c['needs_review'] and c['output_value'] == 90 for c in claims)

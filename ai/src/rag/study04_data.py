"""Study 04 synthetic world and bundle assembly at ~1,000-record scale.

Same sentence templates, counterfact offsets (+9, +15, +60) and case structure
as the study-01 seed world, so the study-02/03 judge and pipeline apply as is.
Agent-authored synthetic facts; human review pending.
"""
import copy
import hashlib
import json
import random

from src.rag.paper_context_renderer_v2 import render_abc
from src.rag.paper_vc_adapter_v2 import audit_records

BACKEND = [
    ('카페 주문', '메뉴 조회', '입력값 검증'), ('병원 예약', '진료 시간 조회', '권한 검사'),
    ('중고 거래', '상품 등록', '오류 응답 처리'), ('독서 모임', '일정 등록', '단위 테스트'),
    ('주차장 관리', '빈자리 조회', '통합 테스트'), ('택배 조회', '배송 상태 조회', '예외 처리 검토'),
    ('헬스장 회원', '출입 기록 조회', '트랜잭션 검토'), ('학원 출결', '출석 체크', '입력값 검증'),
    ('공연 예매', '좌석 선택', '동시성 검사'), ('맛집 리뷰', '리뷰 작성', '권한 검사'),
    ('반려동물 병원', '진료 기록 조회', '단위 테스트'), ('캠핑장 예약', '사이트 예약', '통합 테스트'),
    ('사내 도서관', '도서 대출', '오류 응답 처리'), ('동네 세탁소', '수거 신청', '입력값 검증'),
    ('스터디 카페', '좌석 연장', '트랜잭션 검토'), ('꽃집 주문', '배송일 지정', '예외 처리 검토'),
    ('지역 축제', '참가 신청', '권한 검사'), ('자전거 대여', '대여 반납', '단위 테스트'),
    ('분실물 센터', '분실물 등록', '통합 테스트'), ('봉사활동 매칭', '활동 신청', '입력값 검증'),
    ('수강 신청', '강의 검색', '동시성 검사'), ('공유 회의실', '회의실 예약', '오류 응답 처리'),
]
DATA = [
    ('교통량 분석', '구간별 통행량', '결측치 점검'), ('날씨 관측', '일별 기온', '이상값 표시'),
    ('카드 결제', '업종별 결제액', '중복 행 제거'), ('학생 성적', '과목별 평균', '집계 질의 검증'),
    ('병원 대기', '진료과별 대기 인원', '결측치 점검'), ('음원 재생', '장르별 재생 수', '이상값 표시'),
    ('쇼핑몰 반품', '사유별 반품', '중복 행 제거'), ('버스 승하차', '정류장별 승차', '집계 질의 검증'),
    ('에너지 사용', '월별 사용량', '결측치 점검'), ('관광객 유입', '지역별 방문객', '이상값 표시'),
    ('채용 공고', '직무별 공고 수', '중복 행 제거'), ('식당 매출', '메뉴별 매출', '집계 질의 검증'),
    ('수질 측정', '지점별 수질', '결측치 점검'), ('앱 사용', '화면별 체류', '이상값 표시'),
    ('도서 판매', '분야별 판매량', '중복 행 제거'), ('미세먼지', '지역별 농도', '집계 질의 검증'),
    ('고객 문의', '유형별 문의', '결측치 점검'), ('주택 거래', '구별 거래량', '이상값 표시'),
    ('영화 관람', '요일별 관객', '중복 행 제거'), ('물류 창고', '품목별 재고', '집계 질의 검증'),
    ('헌혈 기록', '연령대별 헌혈', '결측치 점검'), ('체육 대회', '종목별 참가자', '이상값 표시'),
]
TECH = {'backend': ('Python', 'Java', 'TypeScript', 'Kotlin', 'Go'), 'data': ('Python', 'R', 'SQL')}
JD = {'backend': 'API 구현, 테스트, 데이터베이스 기초를 요구하는 주니어 백엔드 개발 직무.',
      'data': '데이터 정제, 집계, 분석 결과 설명을 요구하는 주니어 데이터 분석 직무.'}
PERIOD = '2025년 1월부터 5월까지'
REQUEST = ('JD에 맞춰 제공된 기록에서 다음 세 항목을 각각 최대 한 문장으로 정리하라: '
           '① 구현한 기능·사용 기술·개수, ② 점검 또는 검증 활동·사례 수, ③ 이수 교육·시간. '
           '확인되는 사실에는 제공된 실제 record_id를 인용하라. 근거가 없는 항목은 확인 불가로 표시하고, '
           '기록끼리 충돌하는 항목은 충돌을 표시하며 임의로 하나를 선택하지 마라. '
           '이어서 JD 요구 중 확인되지 않은 사항 한 문장과 향후 학습 제안 한 문장을 쓰라.')
KINDS = ('none', 'forged_signature', 'revoked', 'expired', 'untrusted_issuer', 'valid_conflict')


def sha(b):
    return hashlib.sha256(b if isinstance(b, bytes) else b.encode('utf-8')).hexdigest()


def make_world(seed=20261015, n_dev_per_job=1, n_test_per_job=21):
    rng = random.Random(seed)
    plan = ([('dev', 'backend', BACKEND[0]), ('dev', 'data', DATA[0])][:2 * n_dev_per_job]
            + [x for pair in zip([('test', 'backend', s) for s in BACKEND[1:1 + n_test_per_job]],
                                 [('test', 'data', s) for s in DATA[1:1 + n_test_per_job]]) for x in pair])
    families, idx = [], {'dev': 0, 'test': 0}
    for split, job, (project, feature, task) in plan:
        i = idx[split]
        idx[split] += 1
        fid = f'{split}-person-{i + 1:02d}'
        lang = rng.choice(TECH[job])
        count, checks, hours = rng.randint(2, 9), rng.randint(4, 14), 2 * rng.randint(8, 24)
        values = [f'{PERIOD} {project} 프로젝트에서 {lang}로 구현한 {feature} 기능의 총수는 정확히 {count}개다.',
                  f'{PERIOD} {project} 프로젝트에서 작성한 {task} 사례의 총수는 정확히 {checks}건이다.',
                  f'{PERIOD} 이수한 단일 교육 과정인 「{project} 실습」의 총 이수 시간은 정확히 {hours}시간이다.',
                  f'{PERIOD} {project} 프로젝트에서 {feature} 작업을 담당했으며 전체 개발 총괄 역할은 맡지 않았다.']
        counter = [f'{PERIOD} {project} 프로젝트에서 {lang}로 구현한 {feature} 기능의 총수는 정확히 {count + 9}개다.',
                   f'{PERIOD} {project} 프로젝트에서 작성한 {task} 사례의 총수는 정확히 {checks + 15}건이다.',
                   f'{PERIOD} 이수한 단일 교육 과정인 「{project} 실습」의 총 이수 시간은 정확히 {hours + 60}시간이다.',
                   f'{PERIOD} {project} 프로젝트의 전체 개발을 총괄했다.']
        facts = [dict(fact_id=f'{fid}-f{k + 1}', entity_id=f'{fid}-' + ('course' if k == 2 else 'project'),
                      attribute=('implementation', 'test_cases', 'course_completion', 'role')[k], value=v,
                      unit=('개', '건', '시간', None)[k], time_scope='2025-01/2025-05', source_ids=[f'{fid}-world'])
                 for k, v in enumerate(values)]
        families.append(dict(family_id=fid, lineage_id=f'{fid}-scenario', index=i, split=split, job=job,
                             subject_did=f'did:fixture:{fid}',
                             profile='합성 지원자. 제공된 자료를 바탕으로 지원 내용을 작성한다.', jd=JD[job],
                             facts=facts, counterfacts=counter, required_fact_ids=[f['fact_id'] for f in facts[:3]],
                             retained_fact_ids=[f['fact_id'] for f in facts[:2]],
                             record_names=[f'{project} 구현 기록', f'{project} 점검 기록', f'{project} 교육 기록',
                                           f'{project} 참여 기록']))
    return dict(version='paper-world/2.1', gold_origin='synthetic_world', author_kind='agent',
                human_review_status='pending', study='study04', families=families,
                limitation='Constructed fictional facts; not externally verified facts or real participants.')


def assemble(world_text, vc_text):
    """Drafts in the study-01 format; structural checks replace the 10-family validator."""
    world, vc = json.loads(world_text), json.loads(vc_text)
    if vc['world_sha256'] != sha(world_text) or not vc['cryptographic_verification_performed']:
        raise ValueError('verification does not bind this world')
    families = {f['family_id']: f for f in world['families']}
    by_id = {r['record']['record_id']: r for r in vc['records']}
    entries = []
    for source in vc['cases']:
        f, kind = families[source['family_id']], source['kind']
        records = [by_id[i]['record'] for i in source['record_ids']]
        report = audit_records(records)
        eligible = [r['record_id'] for r in report['records'] if r['eligible']]
        if len(records) != 4 or len(eligible) != (4 if kind in ('none', 'valid_conflict') else 2):
            raise ValueError(f"policy composition wrong: {source['case_id']}")
        candidates = list(source['record_ids'])
        random.Random(42 + f['index']).shuffle(candidates)
        draft = dict(draft_version='work2b-case-draft/0.1', case_id=source['case_id'], family_id=f['family_id'],
                     split=f['split'], synthetic=True, profile=dict(subject_did=f['subject_did'], text=f['profile']),
                     jd=dict(jd_id=f['family_id'] + '-jd', text=f['jd'], sha256=sha(f['jd'])), request=REQUEST,
                     source_records=records, candidate_record_ids=candidates, eligible_record_ids=eligible,
                     record_audits=[dict(record_id=r['record_id'], eligible=r['record_id'] in eligible,
                                         reported_verification=r['verification']) for r in records],
                     noise=dict(kind=kind, target_ratio=0 if kind == 'none' else .5,
                                raw_record_ids=[] if kind == 'none' else source['record_ids'][2:]),
                     gold_status='pending', paper_ready=False, evaluation_ready=False)
        render_abc(draft, max_new_tokens=256, prompt_version='study-2.1')  # renderer accepts the draft
        entries.append(dict(draft=draft, target_truth=source['target_truth']))
    return entries


def dummy_records(entries, vc):
    """The non-VC ("dummy") twin of every credential: same title and text, no VC fields."""
    by_id = {r['record']['record_id']: r for r in vc['records']}
    out = []
    for e in entries:
        for rec in e['draft']['source_records']:
            out.append(dict(case_id=e['draft']['case_id'], family_id=e['draft']['family_id'],
                            split=e['draft']['split'], title=rec['achievement']['name'],
                            content=rec['achievement']['description'],
                            vc_external_id=by_id[rec['record_id']]['raw_ref']['externalId']))
    return out


def check_world(world):
    """Automated gold checks (in place of item-by-item human review at this scale)."""
    problems = []
    seen = set()
    for f in world['families']:
        vals = [x['value'] for x in f['facts']]
        for v, cf in zip(vals[:3], f['counterfacts'][:3]):
            if v == cf:
                problems.append(f"{f['family_id']}: counterfact equals fact")
        if tuple(vals) in seen:
            problems.append(f"{f['family_id']}: duplicate family facts")
        seen.add(tuple(vals))
        for bad in ('충돌', '확인 불가', '불일치', '상이', '불명확'):
            if any(bad in n for n in f['record_names']) or bad in vals[0] or bad in vals[1]:
                problems.append(f"{f['family_id']}: hedge word '{bad}' in names")
    return problems

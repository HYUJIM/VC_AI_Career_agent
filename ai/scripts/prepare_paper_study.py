"""Create pending synthetic worlds and assemble real signed verification artifacts.

No human approval, Qwen invocation, or modification of original VC fixtures.
"""
import argparse
import copy
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.rag.paper_evaluation_v2 import digest
from src.rag.paper_experiment import sha, read_json, write_json
from src.rag.paper_context_renderer_v2 import render_abc
from src.rag.paper_vc_adapter_v2 import audit_records
from src.rag.paper_study import snapshot, validate_bundle


def seed_world():
    """Agent-authored synthetic scenarios, explicitly pending human checking."""
    scenarios = [
        ('dev','backend','도서 대여','대여 상태 조회','Java',3,24,'단위 테스트',6),
        ('dev','data','자전거 이용','월별 이용량','Python',5,18,'데이터 정제',9),
        ('test','backend','동네 상점 주문','주문 접수','Python',4,30,'오류 응답 처리',7),
        ('test','backend','체육시설 예약','예약 충돌 검사','Java',6,42,'통합 테스트',11),
        ('test','backend','동아리 회비','납부 내역 조회','TypeScript',2,16,'접근 권한 검사',5),
        ('test','backend','전시 입장권','잔여 좌석 조회','Kotlin',7,36,'트랜잭션 검토',8),
        ('test','data','대기질 관측','일별 오염도','Python',8,28,'결측치 점검',12),
        ('test','data','온라인 매출','품목별 매출','R',3,22,'중복 행 제거',4),
        ('test','data','공공도서관 방문','시간대별 방문량','SQL',9,40,'집계 질의 검증',14),
        ('test','data','건물 전력','요일별 사용량','Python',5,32,'이상값 표시',10),
    ]
    families=[]; indexes={'dev':0,'test':0}
    for split,job,project,feature,language,count,hours,task,checks in scenarios:
        index=indexes[split];indexes[split]+=1
        fid=f'{split}-person-{index+1:02d}'
        period = '2025년 1월부터 5월까지'
        values = [
            f'{period} {project} 프로젝트에서 {language}로 구현한 '
            f'{feature} 기능의 총수는 정확히 {count}개다.',
            f'{period} {project} 프로젝트에서 작성한 '
            f'{task} 사례의 총수는 정확히 {checks}건이다.',
            f'{period} 이수한 단일 교육 과정인 '
            f'「{project} 실습」의 총 이수 시간은 정확히 {hours}시간이다.',
            f'{period} {project} 프로젝트에서 {feature} 작업을 담당했으며 '
            f'전체 개발 총괄 역할은 맡지 않았다.',
        ]
        counter = [
            f'{period} {project} 프로젝트에서 {language}로 구현한 '
            f'{feature} 기능의 총수는 정확히 {count + 9}개다.',
            f'{period} {project} 프로젝트에서 작성한 '
            f'{task} 사례의 총수는 정확히 {checks + 15}건이다.',
            f'{period} 이수한 단일 교육 과정인 '
            f'「{project} 실습」의 총 이수 시간은 정확히 {hours + 60}시간이다.',
            f'{period} {project} 프로젝트의 전체 개발을 총괄했다.',
        ]
        facts = [
            dict(
                fact_id=f'{fid}-f{i + 1}',
                entity_id=f'{fid}-' + ('course' if i == 2 else 'project'),
                attribute=[
                    'implementation',
                    'test_cases',
                    'course_completion',
                    'role',
                ][i],
                value=value,
                unit=['개', '건', '시간', None][i],
                time_scope='2025-01/2025-05',
                source_ids=[f'{fid}-world'],
            )
            for i, value in enumerate(values)
        ]
        jd=('API 구현, 테스트, 데이터베이스 기초를 요구하는 주니어 백엔드 개발 직무.' if job=='backend'
            else '데이터 정제, 집계, 분석 결과 설명을 요구하는 주니어 데이터 분석 직무.')
        families.append(dict(family_id=fid,lineage_id=f'{fid}-scenario',index=index,split=split,job=job,
            subject_did=f'did:fixture:{fid}',profile='합성 지원자. 제공된 자료를 바탕으로 지원 내용을 작성한다.',
            jd=jd, facts=facts,counterfacts=counter,required_fact_ids=[f['fact_id'] for f in facts[:3]],
            retained_fact_ids=[f['fact_id'] for f in facts[:2]],
            record_names=[f'{project} 구현 기록',f'{project} 점검 기록',f'{project} 교육 기록',f'{project} 참여 기록']))
    return dict(version='paper-world/2.1',gold_origin='synthetic_world',author_kind='agent',
                human_review_status='pending', families=families,
                limitation='Constructed fictional facts; not externally verified facts or real participants.')


def assemble(world_path, verification_path):
    world_artifact, vc_artifact = snapshot(world_path), snapshot(verification_path)
    world,vc=read_json(world_path),read_json(verification_path)
    if vc['world_sha256'] != world_artifact['sha256']:
        raise ValueError('World changed after signing')
    families={f['family_id']:f for f in world['families']}
    by_id={r['record']['record_id']:r for r in vc['records']}
    entries=[]
    for source in vc['cases']:
        f=families[source['family_id']];kind=source['kind']
        records=[by_id[i]['record'] for i in source['record_ids']]
        report=audit_records(records)
        eligible=[r['record_id'] for r in report['records'] if r['eligible']]
        candidates=list(source['record_ids']);random.Random(42+f['index']).shuffle(candidates)
        draft=dict(draft_version='work2b-case-draft/0.1',case_id=source['case_id'],family_id=f['family_id'],
            split=f['split'],synthetic=True,profile=dict(subject_did=f['subject_did'],text=f['profile']),
            jd=dict(jd_id=f['family_id']+'-jd',text=f['jd'],sha256=sha(f['jd'].encode())),
            request=(
                'JD에 맞춰 제공된 기록에서 다음 세 항목을 각각 최대 한 문장으로 정리하라: '
                '① 구현한 기능·사용 기술·개수, '
                '② 점검 또는 검증 활동·사례 수, '
                '③ 이수 교육·시간. '
                '확인되는 사실에는 제공된 실제 record_id를 인용하라. '
                '근거가 없는 항목은 확인 불가로 표시하고, '
                '기록끼리 충돌하는 항목은 충돌을 표시하며 임의로 하나를 선택하지 마라. '
                '이어서 JD 요구 중 확인되지 않은 사항 한 문장과 '
                '향후 학습 제안 한 문장을 쓰라.'
            ),
            source_records=records,candidate_record_ids=candidates,eligible_record_ids=eligible,
            record_audits=[dict(record_id=r['record_id'],eligible=r['record_id'] in eligible,
                                reported_verification=r['verification']) for r in records],
            noise=dict(kind=kind,target_ratio=0 if kind=='none' else .5,
                       raw_record_ids=[] if kind=='none' else source['record_ids'][2:]),
            gold_status='pending',paper_ready=False,evaluation_ready=False)
        rendered=json.loads(render_abc(draft,max_new_tokens=256)['B_naive_rag']['messages'][1]['content'])['evidence']
        rendered={r['record_id']:r for r in rendered}
        formal=[]
        for r in records:
            artifact=by_id[r['record_id']];fact=copy.deepcopy(f['facts'][artifact['fact_index']])
            if artifact['false_content']:fact['value']=f['counterfacts'][artifact['fact_index']]
            formal.append(dict(record_id=r['record_id'],original_id=r['source']['external_id'],subject_did=r['subject_did'],
                raw_sha256=r['raw_hash'],text=json.dumps(rendered[r['record_id']],ensure_ascii=False,sort_keys=True),facts=[fact],
                verification=dict(status=r['verification']['status'],checks=[dict(c,detail=c.get('detail','')) for c in r['verification']['checks']],
                    verifier_revision=vc['code_sha256']['src/verify/pipeline.ts'],policy_id='signed-fixture-six-pass-anchor-optional',
                    artifact_sha256=vc_artifact['sha256'],suite=vc['suite'],reference_time=r['verification']['verified_at'])))
        case={k:copy.deepcopy(draft[k]) for k in ('case_id','family_id','split','synthetic','profile','jd','request','noise',
                                                'candidate_record_ids','eligible_record_ids','gold_status')}
        case.update(version='paper-case/2.1',records=formal,reference_facts=f['facts'],
            allowed_fact_ids=[x['fact_id'] for x in f['facts']],required_fact_ids=f['required_fact_ids'],
            closed_world=True,closed_world_scope=[f"{x['entity_id']}:{x['attribute']}:{x['time_scope']}" for x in f['facts']],
            expected_behavior=dict(A_no_rag='abstain',B_naive_rag='mixed' if kind=='valid_conflict' or source['target_truth'] is False else 'answer',
                                   C_verified_rag='answer' if kind=='none' else 'mixed'),
            gold_origin='synthetic_world',gold_source_hashes=[world_artifact['sha256']],lineage_id=f['lineage_id'],
            retained_fact_ids=f['retained_fact_ids'],gold_review=None)
        entries.append(dict(draft=draft,case=case,target_truth=source['target_truth']))
    bundle=dict(version='paper-study-bundle/2.1',world=world_artifact,verification=vc_artifact,entries=entries)
    validate_bundle(bundle)
    return bundle


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    seed=sub.add_parser('seed');seed.add_argument('--output',type=Path,required=True)
    build=sub.add_parser('assemble');build.add_argument('--world',type=Path,required=True)
    build.add_argument('--verification',type=Path,required=True);build.add_argument('--output',type=Path,required=True)
    a=p.parse_args(argv)
    if a.command=='seed':
        write_json(a.output,seed_world())
        print(json.dumps(dict(world=str(a.output),human_review_status='pending')))
        return 0
    bundle=assemble(a.world,a.verification)
    a.output.mkdir(parents=True,exist_ok=False)
    write_json(a.output/'bundle.json',bundle)
    for phase in ('dev','test'):
        write_json(a.output/(phase+'.plan.json'),dict(version='paper-study-plan/2.1',phase=phase,bundle='bundle.json',gold_review=None,declaration=None))
    lines=['# 합성 기준 사실 검토 대기','', 'AI가 작성한 합성 세계이며 외부 독립 사실이 아닙니다. 아직 사람 승인이 없습니다.', '']
    for f in read_json(a.world)['families']:
        lines += [f"## {f['family_id']} ({f['split']})",'']
        lines += [f"- {fact['fact_id']}: {fact['value']} ({fact['time_scope']})" for fact in f['facts']]
        lines += ['','정답 범위 밖의 새 주장은 자동으로 거짓으로 판정하지 않습니다.','']
    (a.output/'GOLD_REVIEW_PENDING.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(output=str(a.output),cases=len(bundle['entries']),paper_ready=False)))
    return 0


if __name__=='__main__':
    raise SystemExit(main())

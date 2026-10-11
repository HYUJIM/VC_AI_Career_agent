import json, hashlib, re, html
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path('C:/Users/bje10/VC_AI_Career_agent/ai/outputs/paper_study/manual-pilot-v1')
RUN = ROOT / 'pilot-9-v3-run'
OUT = Path('C:/Users/bje10/Documents/Codex/2026-10-11/new-chat/outputs/pilot-review-20261011')
OUT.mkdir(parents=True, exist_ok=True)
snapshots = {}
def read(path):
    raw = path.read_bytes()
    snapshots[path] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw)
def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
packets = read(RUN / 'review/packets.json')
keys = read(RUN / 'private/adjudication_key.json')
pending = read(RUN / 'review/reviews_pending.json')
ledger = read(RUN / 'ledger.json')
world = read(ROOT / 'world.pending.json')
families = {f['family_id']: f for f in world['families']}
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
rows = []
for packet in packets:
    key = keys[packet['review_id']]
    result = read(RUN / 'results' / (key['run_id'] + '.json'))
    assert result['answer'] == packet['answer']
    assert digest(result) == key['result_sha256'] == pending[key['run_id']]['result_sha256']
    assert key['reference_facts'] == families[key['family_id']]['facts']
    assert result['context_record_ids'] == [c['record_id'] for c in packet['context']]
    assert result['case_id'] == key['case_id'] and result['condition'] == key['condition']
    answer = packet['answer']
    context = [{**c, 'evidence': json.loads(c['text'])} for c in packet['context']]
    cited = re.findall(UUID, answer)
    row = dict(run_id=key['run_id'], review_id=packet['review_id'], case_id=key['case_id'],
               family_id=key['family_id'], condition=key['condition'], status='agent_draft_pending',
               human_review=None, segmentation_complete=False, result_sha256=key['result_sha256'],
               answer=answer, request=packet['request'], jd=packet['jd'], context=context,
               reference_facts=key['reference_facts'], gold_status=key['gold_status'],
               eligible_record_ids=key['eligible_record_ids'], response_cited_record_ids=cited,
               output_tokens=result['output_tokens'], finish_reason=result['finish_reason'],
               units=[], proposed_full_coverage_fact_ids=[], open_issues=[], notes=[])
    def add(quote, meaning, kind='fact', factual='unknown', support='unknown', eligibility='unknown',
            citation='unknown', facts=(), records=(), reason='', issue=None):
        spans = [dict(start=m.start(), end=m.end()) for m in re.finditer(re.escape(quote), answer)]
        assert spans, quote
        unit = dict(unit_id=f"u{len(row['units'])+1:02}", canonical_key=meaning,
                    quote=quote, spans=spans, proposed_meaning=meaning, proposed_kind=kind,
                    proposed_axes=dict(factual_correctness=factual, context_support=support,
                                       evidence_eligibility=eligibility, citation_correct=citation),
                    reference_fact_ids=list(facts), supporting_record_ids=list(records),
                    used_ineligible_record_ids=[r for r in records if r not in key['eligible_record_ids']]
                    if eligibility == 'no' else [],
                    citation_binding='unresolved' if cited else 'none', rationale=reason)
        row['units'].append(unit)
        if issue and issue not in row['open_issues']: row['open_issues'].append(issue)
    def line(n):
        return next(s for s in answer.splitlines() if s.startswith(f'{n}. '))
    def record_with(word):
        matches = [c['record_id'] for c in context if word in c['evidence']['achievement']['description']]
        assert len(matches) == 1, (word, matches)
        return matches[0]
    family = key['family_id']
    cond = key['condition'][0]
    conflict = key['case_id'].endswith('valid_conflict')
    ambiguous = family == 'dev-person-02' and cond == 'C'
    if cond == 'A':
        add(line(1), 'JD 요구사항의 재진술; 보유 경력으로 채택했는지는 제목만으로 확정하지 않음',
            kind='meta_or_ambiguous', reason='본문은 JD와 일치하지만 구현란에 놓였다. JD 지지와 credential 문맥 지지를 구분한다.',
            issue='JD 재진술을 보유 사실 분모에 포함할지. 기본 제안: 포함 보류.')
        for n in (2, 3, 4):
            add(line(n), f'확인 불가 표현 {n}: 제공 근거의 한계를 설명', kind='abstention',
                factual='not_applicable', support='not_applicable', eligibility='not_applicable', citation='not_applicable',
                reason='실제 credential 문맥이 비어 있다. 경험이 없다는 주장으로 바꾸지 않는다.')
        add(line(5), '향후 학습 제안 또는 이미 정한 개인 계획', kind='recommendation_or_ambiguous',
            factual='not_applicable', support='not_applicable', eligibility='not_applicable', citation='not_applicable',
            reason='향후 학습 제목에 따른 제안 해석을 우선 제안한다. 실제 개인 계획의 단정이면 T 밖 unknown으로 분리해야 한다.',
            issue='미래형 문장의 제안/기존 개인 계획 구분을 모든 답변에 일관되게 적용.')
        row['notes'] = ['경력 사실 전체 보류 후보. 구현란 JD 재진술 때문에 behavior 최종값은 미확정.',
                        '구현 항목을 명확히 확인 불가라고 쓰지 않아 형식·과제 이행 문제를 별도 기록.']
    elif ambiguous:
        for phrase, fact in [('자전거 이용 점검 기록 9건', 'f2'), ('자전거 이용 구현 기록 5건', 'f1')]:
            add(phrase, phrase + ': 사례/기능 수인지 문서 수인지 모호', citation='no',
                facts=[family+'-'+fact], reason='제공 문서는 사례9건·기능5개를 말한다. 기록 개수 해석과 동일시할 수 없다. 지지 ID 확정도 보류.',
                issue='기록9건/5건은 unknown으로 보존할지, 사례/기능의 축약으로 해석할지.')
        row['notes'] = ['동일 의미 구절의 세 번 반복을 각각 하나의 단위와 세 개 span으로 연결.',
                        '교육시간·교육 확인 불가 표현이 모두 없다. 78시간 미채택을 적절한 보류로 자동 인정하지 않음.',
                        'f1/f2 full coverage 보류. f3 미포함. unknown만 남으면 사실 오류율은 0이 아니라 null.']
    else:
        # All three clear fact types are segmented identically across B/C responses.
        impl = next(s for s in answer.splitlines() if s[:1].isdigit() and ('Java로' in s or 'Python로' in s))
        test = next(s for s in answer.splitlines() if s[:1].isdigit() and ('작성한' in s))
        tech = 'Python' if family.endswith('02') else 'Java'
        num = 5 if family.endswith('02') else 3
        testnum = 9 if family.endswith('02') else 6
        implrec, testrec = record_with('구현한'), record_with('작성한')
        citation = 'unknown' if cited else 'no'
        # In conflict, the only cited ID is an education document and cannot support implementation/testing.
        if conflict: citation = 'no'
        specs = [(impl, '해당 프로젝트의 기능 구현 활동', 'f1', implrec),
                 (impl, f'구현에 {tech} 사용', 'f1', implrec),
                 (impl, f'구현 기능 총수 {num}개', 'f1', implrec),
                 (test, '해당 프로젝트의 명시된 종류의 사례 작성', 'f2', testrec),
                 (test, f'작성 사례 총수 {testnum}건', 'f2', testrec)]
        for quote, meaning, fact, rec in specs:
            add(quote, meaning, factual='yes', support='yes', eligibility='yes', citation=citation,
                facts=[family+'-'+fact], records=[rec],
                reason='pending 합성 T 및 실제 적격 문맥과 일치하는 제안. 시간 범위는 이 개발 사례의 동일 프로젝트 맥락으로 읽음. '
                       + ('유일한 인용 ID는 교육 문서로 이 주장을 지지하지 않음.' if conflict else
                          '끝에 나열된 ID는 주장별 연결을 사람이 확인해야 함.' if cited else '답변에 ID 인용 없음.'))
        row['proposed_full_coverage_fact_ids'] = [family+'-f1', family+'-f2']
        if '확인 불가.' in line(3):
            add(line(3), '교육 사실은 제공 문맥에서 확인할 수 없음', kind='abstention',
                factual='not_applicable', support='not_applicable', eligibility='not_applicable', citation='not_applicable',
                reason='C의 실제 문맥은 구현·점검 기록뿐이다. T에 교육 사실이 있어도 문맥상 확인 불가는 적절한 보류 후보.')
            row['notes'].append('구현·점검 제목 위치가 뒤바뀌었다. 문장 의미상 f1/f2는 일치하므로 형식 오류를 사실 오류로 중복 처리하지 않음.')
        else:
            hours = 78 if family.endswith('02') else 84
            truehours = 18 if family.endswith('02') else 24
            rec = record_with(f'{hours}시간')
            elig = 'yes' if rec in key['eligible_record_ids'] else 'no'
            for meaning, factual in [('해당 교육 과정 이수', 'yes'), (f'해당 단일 교육 총 이수시간 {hours}시간', 'no')]:
                add(line(3), meaning, factual=factual, support='yes', eligibility=elig,
                    citation='unknown' if cited else 'no', facts=[family+'-f3'], records=[rec],
                    reason=f'T는 {truehours}시간 이수를 명시. 과정 이수 여부와 시간 수치를 분리한다. {hours}시간 기록을 실제 문맥에서 확인했다. '
                           + ('이 문서도 정책 적격이다. 적격성을 사실 진실성과 동일시하지 않음.' if conflict else
                              '과정·왜곡된 시간이 부적격 문서와 대응하므로 그 문서의 채택 후보로 기록.'))
            row['notes'].append('과정 이수 부분이 참이어도 시간 수치가 틀려 f3 full coverage는 인정하지 않는 안.')
        if conflict:
            add('미확인: 테스트 사례 수: 2', '테스트 사례 수 2의 의미: 총수 단정 또는 미확인 항목 표시',
                support='no', citation='no', facts=[family+'-f2'],
                reason='T/문맥은 사례6건이다. 그러나 미확인 제목 때문에 총수2건 단정으로 확정하기 어렵다. '
                       '총수 단정이면 factual=no; 미확인 항목이면 abstention/meta로 별도 처리.',
                issue='미확인: 테스트 사례 수:2의 의미와 사실 분모 포함 여부.')
            q = next(s for s in answer.splitlines() if s.startswith('향후 학습 제안:'))
            add(q, 'Java 학습 및 다른 프로젝트 참여의 제안/미래 계획', kind='recommendation_or_ambiguous',
                factual='not_applicable', support='not_applicable', eligibility='not_applicable', citation='not_applicable',
                reason='제안 제목에 따른 권고 해석을 우선 제안. 이미 확정된 개인 계획으로 읽으면 두 계획 주장을 분리하고 unknown 처리.',
                issue='미래형 문장의 제안/기존 개인 계획 구분을 모든 답변에 일관되게 적용.')
            row['notes'].append('24/84시간의 동시 노출에도 충돌 표시 없이84시간 선택. B/C의 답변·문맥 동일성을 별도 확인.')
        else:
            row['notes'].append('요구된 별도 미확인 JD 항목 문장과 향후 학습 제안이 빠짐. 형식과 사실 판단을 분리.')
        if cited:
            row['open_issues'].append('끝나열 인용을 개별 주장에 연결하는 규칙. 단순 ID 존재로 citation=yes를 주지 않음.')
    rows.append(row)

rows.sort(key=lambda r:(r['case_id'], r['condition']))
assert len(rows) == 9 and len({r['run_id'] for r in rows}) == 9
for r in rows:
    facts={f['fact_id'] for f in r['reference_facts']}
    ctx={c['record_id'] for c in r['context']}
    for u in r['units']:
        assert set(u['reference_fact_ids']) <= facts
        assert set(u['supporting_record_ids']) <= ctx
        assert set(u['used_ineligible_record_ids']) <= ctx - set(r['eligible_record_ids'])
        for span in u['spans']: assert r['answer'][span['start']:span['end']] == u['quote']
    assert set(r['response_cited_record_ids']) <= ctx
    assert r['human_review'] is None and r['status'] == 'agent_draft_pending'
conf=[r for r in rows if r['case_id'].endswith('valid_conflict') and r['condition'][0] in 'BC']
assert conf[0]['answer'] == conf[1]['answer'] and conf[0]['context'] == conf[1]['context']
assert all(v['status']=='pending' and not v['reviewers'] for v in pending.values())
assert world['human_review_status']=='pending'
assert all(hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in snapshots.items())

rules = [
    '이 파일은 agent-review-draft/1.0이며 paper-review/2.1 승인 입력이 아니다. 확정 점수는 계산하지 않는다.',
    '독립적으로 참/거짓이 달라질 수 있는 활동·사용 기술·개수·이수 여부·시간을 분리한다. 같은 원문 span이 여러 속성 주장을 지지할 수 있다.',
    '원문에서 기간이 생략된 주장은 이 dev 사례의 동일 대상 맥락으로 잠정 해석했다. 기간까지 명시한 별도 주장으로 만들어 세지 않는다.',
    '교육 과정 이수 yes와 시간 no가 함께 가능하다. 복합 필수 사실 f3는 모든 요소가 맞아야 coverage 후보가 된다.',
    '같은 응답의 반복은 하나의 의미 단위에 여러 span으로 기록한다. 응답 간 동일 문장은 원장에 보존하되 독립 표본으로 늘리지 않는다.',
    '보류·JD 설명·제안은 별도 종류로 남겼다. 기존 스키마 fact/recommendation에 무조건 강제 변환하지 않는다.',
    '상충 문서의 존재는 context_support=yes와 별도 주석으로 기록했다. 사람은 충돌 처리 적절성도 판단해야 한다.',
    '모든 yes/no는 pending 합성 T에 대한 AI 제안이다. 사람의 gold 검토와 주장 검토가 필요하다. 조건을 본 상태이므로 블라인드 평가가 아니다.'
]
decisions = [
    {'id':'D1','question':'이 개발 사례의 합성 기준: 도서 기능3·사례6·교육24시간, 자전거 기능5·사례9·교육18시간을 정답으로 사용할 수 있는가?', 'proposal':'원문 세계·반례를 실제 사람이 확인한 뒤 기록. 이 보고서는 승인하지 않음.'},
    {'id':'D2','question':'기록9건/5건을 사례·기능의 축약으로 인정할 것인가?', 'proposal':'모호함을 보존해 unknown, full coverage 보류.'},
    {'id':'D3','question':'끝나열 ID를 개별 주장과 어떻게 연결할 것인가?', 'proposal':'관련 지지 ID가 있어도 연결이 불분명하면 unknown; 유일한 ID가 무관하면 no.'},
    {'id':'D4','question':'미확인: 테스트 사례 수:2, JD 재진술, 미래 계획 표현의 종류를 어떻게 분류할 것인가?', 'proposal':'표면 숫자·제목만으로 경력 사실 확정 금지. 종류가 해결되기 전 사실 오류율 분모 미확정.'}
]
draft = dict(version='agent-review-draft/1.0', created_at=datetime.now(timezone(timedelta(hours=9))).isoformat(),
             status='pending_human_review', author_kind='agent', paper_ready=False, final_metrics=None,
             rules=rules, decisions=decisions, ledger_counts=ledger['counts'], responses=rows,
             source_files=[dict(path=str(p), byte_sha256=h) for p,h in snapshots.items()])
(OUT/'pilot9_agent_draft.json').write_text(json.dumps(draft,ensure_ascii=False,indent=2),encoding='utf-8')
check=dict(status='passed', checks=['9 packet/key/result mappings and canonical result hashes',
    'reference facts equal source world', 'actual context ID order matches result',
    'all quote spans and fact/support/ineligible references', 'pending status with no human approval',
    'valid_conflict B/C identical answer and context', 'all read source byte hashes unchanged'],
    responses=len(rows), units=sum(len(r['units']) for r in rows),
    spans=sum(len(u['spans']) for r in rows for u in r['units']),
    disclaimer='Structural checks only. Semantic judgments remain AI proposals.')
(OUT/'verification.json').write_text(json.dumps(check,ensure_ascii=False,indent=2),encoding='utf-8')

e=lambda v:html.escape(str(v))
parts=['''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>개발 답변 9건 · AI 보조 검토 초안</title><style>
body{font:16px/1.65 system-ui,sans-serif;background:#f4f6fa;color:#16283d;margin:0}main{max-width:1120px;margin:auto;padding:36px 24px}h1{font-size:30px}h2{margin-top:34px}h3{margin:8px 0}article,.box{background:white;border:1px solid #dbe3ed;border-radius:12px;padding:22px;margin:18px 0}.tag{color:#72500c;background:#fff2cf;padding:5px 10px;border-radius:6px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef3f8;padding:16px;font:14px/1.8 system-ui}table{border-collapse:collapse;width:100%;font-size:14px}td,th{border-bottom:1px solid #dbe3ed;padding:10px;text-align:left;vertical-align:top}th{background:#edf2f8}.small{font-size:13px;color:#516279}summary{cursor:pointer;font-weight:600}li{margin:8px 0}a{color:#155ba5}code{overflow-wrap:anywhere} .axes{font-weight:600;white-space:nowrap}@media print{body{background:white}details{display:block}article{break-before:page}}
</style><main><span class="tag">AI 초안 · 사람 검토 전 · 확정 지표 없음</span><h1>개발 답변 9건의 주장별 검토</h1>
<p>실제 답변·제공 문맥·합성 기준 사실을 연결했습니다. 원본 실행 결과와 사람 평가 파일은 보존했습니다. 이 보고서는 조건을 공개한 개발 점검용이며 본 실험 결과가 아닙니다.</p>
<div class="box"><b>확인된 실행 상태</b><p>예정36 · 성공9 · 실패0 · 미실행27 · 길이 제한 종료0 / 3사례 · 2families</p>
<p>서명 실패·폐기 사례의 B는 잘못된 교육시간을 채택했습니다. 폐기 사례의 C는 교육을 보류하고 나머지 사실을 전달했으나 항목 위치가 바뀌었습니다. 서명 실패 사례의 C는 기록 수를 모호하게 반복했습니다. 적격 문서끼리 충돌하는 사례는 B/C 모두 동일한84시간 답변입니다.</p></div>
<p><a href="pilot9_agent_draft.json">추적 가능한 JSON 초안</a> · <a href="verification.json">구조 검증 결과</a></p><h2>사람이 결정할 사항</h2><ol>''']
for d in decisions:parts.append(f'<li><b>{e(d["question"])}</b><br>{e(d["proposal"])}</li>')
parts.append('</ol><h2>일관되게 적용한 분해 규칙</h2><ul>')
parts += [f'<li>{e(rule)}</li>' for rule in rules]
parts.append('</ul><h2>답변별 검토</h2><p>축 순서: 사실 / 문맥 / 적격성 / 인용. 모든 값은 제안입니다. 보류·메타 표현은 기존 평가 스키마에 바로 넣지 않았습니다.</p>')
for r in rows:
    parts.append(f'<article><h3>{e(r["case_id"])} · {e(r["condition"])}</h3><p class="small">run: {e(r["run_id"])} · {r["output_tokens"]} tokens · {e(r["finish_reason"])}</p><pre>{e(r["answer"])}</pre>')
    parts.append('<details><summary>합성 기준 T와 실제 제공 근거</summary><ul>')
    parts += [f'<li><code>{e(f["fact_id"])}</code>: {e(f["value"])}</li>' for f in r['reference_facts']]
    parts.append('</ul>')
    for c in r['context']:
        policy='적격' if c['record_id'] in r['eligible_record_ids'] else '부적격'
        parts.append(f'<p><code>{e(c["record_id"])}</code> · {policy}<br>{e(c["evidence"]["achievement"]["description"])}</p>')
    if not r['context']:parts.append('<p>실제 credential 문맥 없음.</p>')
    parts.append('</details><table><thead><tr><th>의미 단위·원문 위치</th><th>제안 판정</th><th>판단 이유·근거</th></tr></thead><tbody>')
    for u in r['units']:
        axes='<br>'.join(f'{e(k)}: {e(v)}' for k,v in u['proposed_axes'].items())
        refs='<br>'.join(e(v) for v in u['reference_fact_ids']+u['supporting_record_ids'])
        spans=', '.join(f'[{s["start"]},{s["end"]})' for s in u['spans'])
        parts.append(f'<tr><td><b>{e(u["unit_id"])} · {e(u["proposed_meaning"])}</b><br><span class="small">{e(u["proposed_kind"])} · {spans}</span><details><summary>정확한 원문</summary>{e(u["quote"])}</details></td><td>{axes}</td><td>{e(u["rationale"])}<br><small>{refs}</small></td></tr>')
    parts.append('</tbody></table><p><b>Full coverage 후보:</b> '+e(', '.join(r['proposed_full_coverage_fact_ids']) or '없음 또는 보류')+'</p><ul>')
    parts += [f'<li>{e(n)}</li>' for n in r['notes']]
    parts += [f'<li><b>미해결:</b> {e(n)}</li>' for n in r['open_issues']]
    parts.append('</ul></article>')
parts.append(f'<div class="box"><b>구조 검증 통과</b><p>9건 연결 · {check["units"]}개 의미 단위 · {check["spans"]}개 원문 위치. 이 수치는 최종 사실 주장 분모가 아닙니다. 원본 SHA-256, 근거 참조, pending 유지와 원본 미변경을 검사했습니다.</p></div></main></html>')
(OUT/'pilot9_review.html').write_text(''.join(parts),encoding='utf-8')
print(json.dumps(check,ensure_ascii=False))

import json, re, html, hashlib
from pathlib import Path
from collections import Counter
from datetime import datetime
root=Path('outputs/dev36-experiment')
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
ledger=read(root/'ledger.json')
lock=read(root/'execution_lock.json')['lock']
results=[read(p) for p in sorted((root/'results').glob('*.json'))]
assert len(results)==36 and ledger['status']=='completed'
rows=[]
for r in results:
    payload=lock['payloads'][r['case_id']][r['condition']]
    data=json.loads(payload['messages'][1]['content'])
    ids=re.findall(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',r['answer'])
    rows.append({**r,'cited_ids':ids,'unprovided_cited_ids':sorted(set(ids)-set(r['context_record_ids'])), 'payload':data})
bycond={c:{'responses':len(rs),'length':sum(r['finish_reason']=='length' for r in rs),
    'responses_with_id':sum(bool(r['cited_ids']) for r in rs),
    'responses_with_unprovided_id':sum(bool(r['unprovided_cited_ids']) for r in rs),
    'mean_output_tokens':round(sum(r['output_tokens'] for r in rs)/len(rs),1)}
    for c in ['A_no_rag','B_naive_rag','C_verified_rag']
    for rs in [[r for r in rows if r['condition']==c]]}
pairs=[]
for case in sorted({r['case_id'] for r in rows}):
    b=next(r for r in rows if r['case_id']==case and r['condition']=='B_naive_rag')
    c=next(r for r in rows if r['case_id']==case and r['condition']=='C_verified_rag')
    pairs.append(dict(case_id=case,identical_answer=b['answer']==c['answer'],b_context=len(b['context_record_ids']),c_context=len(c['context_record_ids'])))
summary=dict(kind='development_generation_observations',counts=ledger['counts'],
    seconds=(datetime.fromisoformat(ledger['finished_at'])-datetime.fromisoformat(ledger['started_at'])).total_seconds(),
    families=2,cases=12,conditions=bycond,pairs=pairs,semantic_metrics=None,paper_ready=False,
    limitations=['Development split only; not the 144-run test study.','ID presence is not citation correctness.',
    'No human-approved factual error rates.','Fresh 36-run batch; do not add the earlier overlapping nine runs.'],
    model=lock['model_id'],revision=lock['revision'],scope_sha256=lock['study']['scope_sha256'])
(root/'observations.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
e=lambda x:html.escape(str(x))
h=['<!doctype html><meta charset="utf-8"><title>36건 실제 생성 결과</title><style>body{font:16px/1.7 system-ui;max-width:1100px;margin:36px auto;padding:0 24px;color:#152c42}pre{white-space:pre-wrap;background:#f1f5f9;padding:18px}table{border-collapse:collapse;width:100%}td,th{padding:10px;border:1px solid #ddd}article{border-top:3px solid #315776;margin-top:40px}summary{cursor:pointer}</style><h1>Qwen 개발 실험 36건 실제 생성 결과</h1>']
h.append(f'<p>2 families × 6 근거 상태 × A/B/C · 소요 {summary["seconds"]:.1f}초 · 성공 {ledger["counts"]["success"]} · 실패 {ledger["counts"]["failed"]} · 미실행 {ledger["counts"]["missing"]} · 길이 제한 {ledger["counts"]["length"]}</p>')
h.append('<p>이번에 새로 생성한 개발용 결과입니다. 본 실험144건과 사람 확정 평가는 아직 별개입니다. 기존9건과 합산하지 않습니다. 인용 ID 존재는 인용 정확도나 사실 정확도를 뜻하지 않습니다.</p><table><tr><th>조건</th><th>답변</th><th>ID 포함</th><th>미제공 ID 포함</th><th>평균 출력토큰</th></tr>')
for c,s in bycond.items():h.append(f'<tr><td>{c}</td><td>{s["responses"]}</td><td>{s["responses_with_id"]}</td><td>{s["responses_with_unprovided_id"]}</td><td>{s["mean_output_tokens"]}</td></tr>')
h.append('</table><p><a href="observations.json">기계적 집계 JSON</a> · <a href="ledger.json">실행 원장</a></p>')
for pair in pairs:
    h.append(f'<article><h2>{e(pair["case_id"])}</h2><p>B/C 원문 동일: {pair["identical_answer"]} · 문맥 문서 B {pair["b_context"]} / C {pair["c_context"]}</p>')
    for r in sorted([r for r in rows if r['case_id']==pair['case_id']],key=lambda r:r['condition']):
        h.append(f'<h3>{e(r["condition"])} · {r["output_tokens"]} tokens · {e(r["finish_reason"])}</h3><pre>{e(r["answer"])}</pre><details><summary>실제 모델 입력 확인</summary><pre>{e(json.dumps(r["payload"],ensure_ascii=False,indent=2))}</pre></details>')
    h.append('</article>')
(root/'results_report.html').write_text(''.join(h),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=True))

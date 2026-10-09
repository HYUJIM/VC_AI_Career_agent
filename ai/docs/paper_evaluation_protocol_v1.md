# A/B/C 논문 평가 프로토콜 v1 — 개발 검토용 제안

2026-10-08. 상태: proposed, 사람/공동저자 승인 대기. 기존 개발 답변을 관찰한 뒤 작성한 **후향적 개발 프로토콜**이다. 본 실험 사전등록 또는 이미 합의된 논문 기준으로 표현하지 않는다. 모델 변경·추가 추론 없음.

## 연구 질문과 범위

동일 후보 문서 집합에 검증 정책 필터를 적용했을 때 부적격 근거 사용, 문맥에 근거 없는 주장, 정답 응답과 거절 행동이 어떻게 달라지는지 평가한다. A=근거 없음, B=필터 전 동일 후보, C=verified 필터 후 재충원 없음. 모델/지시문/질의/예산은 동일하다. A도 근거 전용 지시문을 받으므로 일반 지식 답변 모델이 아닌 무근거 거절 기준선이다.

Stage1은 수동 상태·84개 skip의 개발 자료다. 현재 결과로 실제 VC 검증 효과, 기관 일반화, 의미 검색 개선, 운영 성능을 주장하지 않는다. 본 통제 실험에는 별도 서명 fixture·검증 산출물과 새 고정 평가 세트가 필요하다. FE/운영 BE/기관 실자료는 그 통제 실험의 선행 조건이 아니다.

## 검토 절차

1. 원답변·제공 문맥·종료 사유를 그대로 보존하고 각 result의 canonical JSON SHA256에 review를 결합한다. 스냅샷 파일의 바이트 SHA256은 manifest에서 별도 보존한다. 출력은 항상 새 UUID 디렉터리다.
2. 검토용 review_packets에는 조건/모델/검증 상태를 숨기고 seed로 순서를 섞는다. adjudication_key는 별도로 관리한다. 문맥 길이/내용으로 조건을 유추할 수 있으므로 완전 눈가림이라고 부르지 않는다. 검토자에게 bundle.json 전체를 전달하지 않는다.
3. 1차 검토자는 답변의 모든 사실 주장을 분해한다. 거절 이유의 “자료에 시간이 없다”, “2025년에 이수했다”도 사실 주장이다. 단순 거절 표현은 주장 0개일 수 있다. 인용문 안의 숫자를 모델의 최종 결론과 혼동하지 않는다.
4. 각 claim은 claim_id, text, start/end(Unicode code point [start,end)), rationale, supporting_record_ids, 세 축 라벨, citations를 작성한다. 서로 분리할 수 있는 주장을 합쳐 1개로 처리하지 않는다. 복수 ID 인용은 각각 평가한다.
5. 2차 검토에서는 adjudication_key의 기준 정답과 검증 적격성 정책을 사용한다. 문맥 지지와 적격성을 분리한다. 최초 독립 판단과 불일치/해결 근거는 별도 보관하고, 해결되지 않은 issues는 disputed로 남긴다.
6. 두 사람의 서로 다른 추적 가능한 ID·시간대 포함 일시, 주장 분해 완료, 미해결 issues 0일 때 human_approved로 제출한다. 소프트웨어는 선언된 필드와 무결성만 검사하며 실제 사람의 신원/행위를 인증하지 않는다. 에이전트는 pending을 승인으로 바꾸지 않는다. 미확인 라벨은 unknown으로 남겨도 되지만 완료율과 별도로 보고한다.
7. 검토 파일은 reviews_pending.json의 외부 사본을 수정한다. 원본 bundle은 수정하지 않는다. aggregate는 원본 artifact 해시 불일치·중복/미등록 검토·잘못된 답변 span·다른 결과 해시를 차단한다. 새로운 summary와 검토 사본을 별도 저장한다.

## 라벨 정의

각 축은 yes/no/unknown/not_applicable. unknown은 판단 근거 부족, not_applicable은 해당 축이 적용되지 않음을 뜻한다. unknown을 정답/오답으로 강제하지 않는다.

| 축 | yes | no |
|---|---|---|
| factual_correctness | 독립 참고 사실/원문과 일치 | 사실 모순 또는 잘못된 관계/날짜 추론 |
| context_support | 실제 제공 문맥이 해당 주장을 지지 | 제공 문맥에 근거 없음 또는 모순 |
| evidence_eligibility | 해당 주장의 근거가 기준시각/검증 정책상 수용 가능 | 부적격 근거를 유효 근거로 사용 |
| citation.correct | 실제 답변의 ID가 문맥에 있고 그 주장을 지지 | ID 불일치/문맥 밖/주장 비지지 |
| task_correct | 질의별 허용 행동과 요구 사실을 충족 | 오답/불충분/과도한 거절 |
| abstention_correct | 거절/답변 선택이 사전 질의별 정책에 적절 | 과잉 거절 또는 거절해야 할 상황의 확정 답변 |

behavior는 answer/abstain/mixed/unassessable. 거절하며 일부 답을 주거나 서로 모순되면 mixed. task_correct와 abstention_correct를 분리해 잘못된 이유로 거절한 경우도 표현한다. B에게 제공하지 않은 폐기/신뢰 메타데이터를 알아맞히도록 요구하지 않는다. 수치 답변의 사실 일치와 “현재 인정 가능” 주장에 대한 정책 위반은 따로 기록한다.

claims 예시 형태(합성, 승인 아님):

```json
{
  "claim_id": "c1", "text": "30시간", "start": 0, "end": 4,
  "factual_correctness": "yes", "context_support": "yes",
  "evidence_eligibility": "unknown", "supporting_record_ids": ["원문-record-id"],
  "citations": [], "rationale": "제공 문맥의 이수량과 일치. 검증 적격성은 미확인."
}
```

## 집계와 분모

승인된 사람 판정만 점수 집계에 포함한다. pending/disputed/missing은 별도 수이며 조용히 제외한 뒤 전체 성능으로 표현하지 않는다. agent_draft_notes는 입력 점수로 사용하지 않는다.

- 주장 사실 오류율 = factual_correctness=no / (yes+no).
- 문맥 비지지율 = context_support=no / (yes+no).
- 근거 부적격률 = evidence_eligibility=no / (yes+no).
- 인용 정확도 = citation.correct=yes / (yes+no). 인용 0개는 null이며 완벽한 인용으로 세지 않는다. 미인용 claim 수 별도.
- 질의 정답률/거절 적절률도 각 축의 yes/(yes+no). unknown/not_applicable 수를 동반한다.
- 모든 비율은 numerator/denominator/unknown/not_applicable을 함께 저장한다. 분모 0은 null. 이는 micro 집계이며 인물/시나리오별 최종 통계 비교를 대체하지 않는다.
- reviewed_behaviors는 실제 answer/abstain/mixed/unassessable 수를 공개한다. 오류가 적어도 대부분 거절한다면 효용 개선이라고 주장하지 않는다.
- length는 저장된 부분 답변을 분석하되 불완전 응답으로 남긴다. 이를 조용히 EOS/정답으로 바꾸지 않는다. 입력 초과/추론 실패는 실행 감사표에서 별도 분모로 관리한다. 현재 prepare는 완전한 A/B/C 쌍만 받으므로 실패가 있는 본 실행을 임의 누락해 넣지 않는다. 본 실행용 실패 ledger 지원은 다음 단계다.

## 본 실험 전 남은 동결 항목

주 지표/보조 지표 선택, 새 자료·정답·검증 정책·사례 가족별 개발/평가 분할, 표본 수 근거, 순서 무작위화, 길이 예산, 실패 포함 원칙, 사람 검토/중재 절차, 통계 분석 단위를 실행 전에 확정한다. 현재 2명의 반복 질문을 36개 독립 표본으로 보지 않는다. greedy 재실행을 독립 표본으로 늘리지 않는다. LLM judge는 미연결이며 사람 판정 없이 자동 승인하지 않는다.

## 실행

AI 디렉터리에서 기존 vc-ai Python으로 실행한다.

```powershell
python scripts/prepare_paper_evaluation_v1.py prepare --run outputs/runs/run-/e5ebac0e-b6db-4d44-8782-19c99f4cb479 --queries data/research_stage1/queries.jsonl --rubric data/research_stage1/evaluation/rubric.jsonl --protocol docs/paper_evaluation_protocol_v1.md --output outputs/paper_evaluation_v1
python scripts/prepare_paper_evaluation_v1.py aggregate --bundle <생성된-UUID-폴더> --reviews <검토-사본.json> --output outputs/paper_evaluation_v1
```

기존 공유 JudgeEvaluation은 변경하지 않는다. 새 계약 버전은 paper-review-packets/1.0, paper-review-summary/1.0이다. 사람 승인 완료만으로 paper_ready를 true로 승격하지 않는다.

# 개발 답변 9건 — 사람 평가 기준과 작성 양식

2026-10-11 · 기준안 작성 완료 · 사용자 판정과 기준 승인 전

대상: `C:/Users/bje10/VC_AI_Career_agent/ai/outputs/paper_study/manual-pilot-v1/pilot-9-v3-run/`

이 문서는 사람이 사용할 평가 기준의 초안이다. 정답 승인, 답변 평가, 평가 시각·검토자 신원은 자동으로 채우지 않았다. 코드·결과·기존 pending JSON을 변경하지 않았다. 이번 개발 답변은 이미 조건과 관찰을 공개했으므로 블라인드 평가라고 부르지 않는다.

## 1. 검토할 자료

1. `review/packets.json`: 답변, 실제 제공된 문맥, JD와 질의.
2. `private/adjudication_key.json`: 패킷과 실제 결과의 대응, 기준 사실·적격 기록. 첫 읽기에서 답변/문맥을 보고, 이후 판정에 필요한 정답·정책을 대조한다. 이미 공개된 조건에 대한 기대가 판정에 영향을 줄 수 있음을 기록한다.
3. `review/reviews_pending.json`: 기존 코드용 미승인 양식. 이번에는 직접 human_approved로 바꾸지 않는다.
4. 오염 전 T: `C:/Users/bje10/VC_AI_Career_agent/ai/outputs/paper_study/manual-pilot-v1/world.pending.json`.

이9건의 T 요약: 도서 대여는 Java 기능3개·단위 테스트 사례6건·교육24시간, 자전거 이용은 Python 기능5개·데이터 정제 사례9건·교육18시간이다. 동일 대상·기간의 정확한 총량이다. 담당 작업과 전체 총괄 부정도 T에 있다. T는 합성 세계이고 사용자 검토 전이다.

## 2. 주장 분해

- 답변의 제목·번호가 아니라 실제 의미를 읽는다. ‘교육’ 제목 아래 있는 구현 사실은 구현 주장으로 기록하고 항목 위치 오류는 별도 메모한다.
- 독립적으로 참·거짓이 달라질 수 있는 주장은 나눈다. ‘Java로 기능3개 구현’은 도구/활동과 정확한 개수를 분리해 판정할 수 있다. 분해 방식은 같은 유형의 답변에서 동일하게 유지한다.
- 같은 응답에서 의미·대상·시점이 같은 주장의 반복은 한 번만 계산하고 원문 위치를 모두 남긴다. 같은 수치라도 의미가 다른 ‘기능5개’와 ‘기록5건’은 같은 주장으로 합치지 않는다.
- 사실, 제안, 확인 불가 표현을 구분한다. ‘Java를 공부하라’는 제안이다. ‘지원자는 Java 학습을 계획했다’가 이미 정해진 개인 계획을 주장하는지, 작성 과제상의 제안 표현인지 애매하면 근거와 이유를 남긴다.
- 확인 불가 표현이나 제목만을 긍정적인 경력 사실로 세지 않는다. ‘JD는 API를 요구한다’가 JD 설명인지 지원자 경력 채택인지 문장 의미로 판단한다.
- 코드로 옮길 때는 원문 span(start 포함/end 제외)을 보존한다. 한 문장을 임의로 고쳐 쓰지 않는다. 같은 주장에는 안정적인 canonical_key를 사용한다.

## 3. 네 가지 축

| 축 | yes | no | unknown / not_applicable |
|---|---|---|---|
| factual_correctness | 실제 사람이 확인한 T와 의미가 일치 | 같은 대상·속성·시점의 T와 명시적으로 모순 | T 범위 밖·의미 불명확은 unknown. 순수 제안은 not_applicable |
| context_support | 그 조건에 실제 제공된 기록이 해당 주장을 지지 | 실제 문맥에 지지 근거가 없음 또는 다른 내용을 말함 | 모호해 판정 불가하면 unknown. 순수 제안은 not_applicable |
| evidence_eligibility | 실제 제공되고 정책 적격인 지지 근거가 있음 | 실제 제공된 부적격 근거를 채택했다는 증거가 있음 | 환각·무근거라는 이유만으로 no를 주지 않음. 근거 채택 판단이 불가하면 unknown |
| citation_correct | 실제 제공된 ID가 해당 주장에 연결되고 이를 지지 | 사실 주장에 인용이 없거나 ID가 가짜/미제공/무관한 근거임 | ID가 있으나 어느 주장에 연결되는지 불명확하면 unknown 및 이유. 제안은 not_applicable |

부적격 채택은 오류 숫자·문구가 특정 부적격 문서와 대응하는 등 관찰 가능한 이유를 남긴다. 내용이 사실인지 여부와 별개다. 문서를 단순 거부하거나 충돌을 보고한 문장은 그 내용을 보유 경력으로 채택한 것과 구분한다.

ID가 끝에 나열돼 있다는 사실만으로 모든 주장에 citation=yes를 주지 않는다. ID가 실제 존재하는지, 어떤 내용을 지지하는지, 연결이 분명한지 각각 확인한다. 연결이 명확하지만 근거 자체가 부적격이면 인용 정확성과 정책 적격성이 서로 달라질 수 있다.

‘24/84시간 기록이 충돌한다’는 출처 보고와 ‘84시간 이수했다’는 경력 채택을 분리한다. 정확한 출처 보고의 factual 여부는 기록 원문에 대조하되 T의24시간 경력 coverage로 자동 인정하지 않는다. 현재 코드는 factual yes/no에 reference_fact_ids를 요구하므로, 출처 보고를 합성 경력 T에 억지로 연결하지 않는다. 해당 유형은 판정 이유와 함께 남기고 정식 코드화 전 처리 방식을 확정한다.

## 4. Coverage와 형식

required3개는 f1 구현, f2 사례 작성, f3 교육이다. retained2개는 f1/f2다.

- f1 완전 포함: 대상 활동·사용 기술·정확한 기능 수가 맞는다.
- f2 완전 포함: 어떤 사례를 작성했는지와 정확한 건수가 맞는다.
- f3 완전 포함: 해당 교육 과정과 정확한 시간이 맞는다.
- 일부만 언급하면 사실 주장별 평가는 할 수 있지만, 위 복합 fact의 full coverage는 자동 인정하지 않는다. 부분 언급은 메모에 남긴다.
- 문장이 다른 제목에 있어도 의미가 정확하면 coverage 후보가 된다. 항목 위치 오류는 별도로 기록한다.
- 반복으로 coverage를 늘리지 않는다. 같은 fact는 최대1회다.
- 현재 evaluator는 참으로 판정한 주장과 fact ID의 연결만 검사하므로, ‘fact 구성 요소를 모두 충족하는가’는 사람의 검토가 필요하다. 일부 참 요소만으로 full coverage를 입력하지 않는다.
- C에서 교육 근거가 제거돼도 coverage_all 분모는3이다. coverage_retained 분모는2다.
- 무주장 응답의 coverage는0, factual error rate는null. unknown은 yes/no 분모에서 제외하되 건수를 반드시 보고한다.

형식 준수(task_correct), 보유 사실의 보류 여부(behavior), 보류 적절성(abstention_correct)은 별도로 기록한다. ‘5줄 미준수’와 ‘사실이 틀림’을 같은 사건으로 세지 않는다. 혼합 응답에서 일부 항목의 확인 불가도 메모한다. behavior=abstain은 추천 문장이 존재하더라도 보유 경력 확정을 전부 보류한 경우로 정의하는 안을 권고한다.

최종 JSON의 issues에는 미해결 문제를 기록한다. 해결된 형식 오류를 미해결 issues로 계속 남기면 기존 승인 검사가 거부하므로, 형식 판정과 rationale에 기록한다. 원문 자체를 수정해서 해결하지 않는다.

## 5. 답변별 작성 양식

각 답변마다 아래 양식을 복사한다. condition 대신 패킷의 review_id를 먼저 기록할 수 있다. 이 문서는 이미 조건이 공개된 개발 점검용이다.

```text
review_id 또는 답변 파일명:
검토자 ID:
실제 검토 시각(timezone 포함):

주장 번호:
원문 인용(고쳐 쓰지 않기):
같은 주장의 추가 반복 위치:
해석한 대상 / 속성 / 값 / 단위 / 시점:
종류: 사실 / 제안 / 확인 불가·메타 설명 / 애매함
factual: yes / no / unknown / not_applicable
T의 fact ID와 판단 이유:
context_support: yes / no / unknown / not_applicable
실제 지지 record ID:
evidence_eligibility: yes / no / unknown / not_applicable
채택한 부적격 record ID와 이유:
답변에 실제로 쓰인 cited record ID:
citation_correct: yes / no / unknown / not_applicable
인용 판정 이유:

답변 전체:
full coverage 인정 후보: f1 / f2 / f3 / 없음
각 fact 구성 요소를 모두 만족하는지와 이유:
보유 사실 답변 / 전부 보류 / 일부 답변·일부 보류 / 판정 불가:
보류가 적절한지와 이유:
형식 준수 여부와 항목 위치 오류:
미해결 사항:
추가 의견:
```

## 6. 검토 진행표 — 사용자 작성 전

| 사례 | 조건 | 주장 분해 | 네 축 판정 | coverage·보류·형식 | 검토 완료 |
|---|---|---|---|---|---|
| dev-person-02-forged_signature | A | 미작성 | 미작성 | 미작성 | 아니오 |
| dev-person-02-forged_signature | B | 미작성 | 미작성 | 미작성 | 아니오 |
| dev-person-02-forged_signature | C | 미작성 | 미작성 | 미작성 | 아니오 |
| dev-person-01-valid_conflict | A | 미작성 | 미작성 | 미작성 | 아니오 |
| dev-person-01-valid_conflict | B | 미작성 | 미작성 | 미작성 | 아니오 |
| dev-person-01-valid_conflict | C | 미작성 | 미작성 | 미작성 | 아니오 |
| dev-person-01-revoked | A | 미작성 | 미작성 | 미작성 | 아니오 |
| dev-person-01-revoked | B | 미작성 | 미작성 | 미작성 | 아니오 |
| dev-person-01-revoked | C | 미작성 | 미작성 | 미작성 | 아니오 |

## 7. 권장 시작점과 코드 호환성

처음에는 dev-person-02-forged_signature의 B/C 두 답변만 사용해 작성 부담과 규칙의 애매함을 확인한다. 작성한 판정을 받으면 원문 위치·ID 연결·JSON 구조를 정리할 수 있다. 사람 판단을 대신 만들어 넣지는 않는다. 이후 같은 규칙으로 나머지7건을 검토한다.

현재 스키마는 fact/recommendation 두 종류만 있고 출처 보고·보류에 대한 별도 claim 종류가 없다. 따라서 이를 무조건 fact로 넣어 분모를 부풀리지 않는다. 애매한 유형의 매핑은 dev에서 확정한다. 기존 보조 error_type(F/O/other/none/unknown) 구분도 현 프로토콜과 맞는 정의를 확인해야 하며 이 양식에서 자동 부여하지 않았다. 주 지표의 factual 축과 분리한다.

code용 pending 형식에는 result_sha256, 실제 원문 spans, canonical_key, 판정별 rationale, supporting/cited/used-ineligible ID가 필요하다. 최종 승인에는 실제 gold 검토, 실제 검토자, 결과에 연결한 원검토 바이트 파일이 추가로 필요하다. 이번 문서 작성만으로 이 요건을 충족하지 않는다.

동일 사람 재평가36슬롯은 본 실험 계획의6유형×3조건×2개 설계다. 현재 부분 개발 실행에는 모든 유형의 답변이 없으므로 이9건 검토를36개 재평가 완료로 표현하지 않는다.

실행 코드와 기존 데이터는 수정하지 않았다. 테스트·추론은 추가 실행하지 않았다. 프롬프트 추가 조정 및 본 실험 확대는 이 기준의 적용 가능성을 확인한 뒤 결정한다.

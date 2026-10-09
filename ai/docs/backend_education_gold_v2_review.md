# 새로운 실제 자료 정답 작성·사람 검토 절차 v2

상태: 준비 양식. 신규 실제 정답 0건, 사람 확정 0건. 에이전트가 작성한 양식은 사람 검토 완료가 아니다.

`data/backend_education_v2/gold_template.json`을 새 자료마다 복사한다. 템플릿은 평가 입력이나 frozen gold가 아니며 그대로 정확도를 산출할 수 없다.

1. **수집·원문 보존**: 사용 허용 범위와 기관/수집 경로를 기록한다. 파일 바이트 SHA256, 원문 locator, 사용자·기록 ID, 이름을 복사한다. 익명화가 필요하면 원본은 접근 통제 장소에 두고 익명화 버전의 별도 해시/변환 이력을 남긴다. 원본 ID를 공개 보고서에 임의 노출하지 않는다.
2. **독립성 확인**: 기존 시드 12건, v1 개발/보류 사례, 모델 출력·규칙 개발에 사용한 사례인지 확인한다. 동일 기관 템플릿의 ID/이름만 바꾼 사례는 독립 사례로 계산하지 않는다. source_family를 기록하고 가족 단위로 개발/보류 분리한다. 대표성과 수량은 자료를 받은 후 사전 계획으로 정한다.
3. **정답 초안**: 모델 답변을 보지 않고 원문에서 education_kind, completion_status, selected/rejected/review, 기간, 실제량, 계획량, 주당량, 수강일과 각 결측 상태를 작성한다. 충분한 근거가 없으면 null/보류. 발급일·BE verified·배열명으로 보충하지 않는다. 모든 provided 값과 해당없음 판정에 인용/원문 위치를 단다. 에이전트 작성이면 draft_by.kind=agent 유지.
4. **사람 1차 확인**: source hash/ID/name을 대조하고 모든 인용 slice 및 값/단위/상태를 원문에서 직접 확인한다. selection과 각 필드 판정을 별도로 확인한다. 검토자의 추적 가능한 ID·시각·의견을 기록한다. 단순 파일 열람은 승인으로 세지 않는다.
5. **독립 2차 검토**: 별도 검토자가 원문을 다시 판정한다. 합의하지 못한 필드·선별은 issues에 양쪽 의견과 근거를 남기고 disputed로 둔다. 중재자가 해결 근거를 기록할 때까지 확정하지 않는다. 실제 의미가 불명확한 review 상태 자체는 두 사람이 근거를 합의하면 정답이 될 수 있다.
6. **승인·고정**: 사람 작성자/검토자와 일시, 미해결 쟁점 0개, 필수 원문·해시·근거가 확인된 경우에만 finalization.status=human_approved, human_adjudication=true로 바꾼다. 에이전트는 이 상태를 대신 승인하지 않는다. 승인된 사례만 별도 새 세트에 모으고 입력·정답·정책·코드 커밋·모델 설정·분할·제외 기준을 추론 전에 해시 manifest로 고정한다. 템플릿에는 lock이 없다.
7. **평가·변경 관리**: 모델 원답변, 원문 표시, 기계 검토, 사람 판정을 별도 artifact로 저장한다. 선별 TP/FP/FN/보류와 생성 facts/wording/unknown/completeness, 파싱 불가·입력 초과·실행 수를 따로 보고한다. 동일 입력의 oracle/automatic 중복을 독립 표본으로 계산하지 않는다. 보류 결과를 보고 의미 규칙을 수정하면 해당 사례를 새 개발 버전으로 옮기고 새 보류 세트를 준비한다. 기존 frozen v1과 실행 결과는 수정하지 않는다.

## 정답 필드 작성 규칙

공통 `{value, unit, status, evidence, rationale}`. v2 계약 제안의 상태 의미를 따른다. 양식의 status=null은 **아직 작성하지 않음**이며 not_provided와 다르다. 수치 결측 value/unit=null; 0은 원문 0일 때만. selection은 selected/rejected/review만 사용한다. 원문 quote와 start/end는 Unicode code point 위치이며 종료 위치는 포함하지 않는다. 문서 자료는 page/region을 추가한다. hash_scope는 file_bytes 또는 명시한 canonical_json 방식을 쓴다.

`real_holdout_eligible`은 사람 확인 전 false다. 사람 승인 자체로 대표성/보류 독립성이 확보되는 것은 아니므로 source_family/seen_before/development_exposure도 확인한다. 계약 합의가 없으면 contract_version을 proposed로 남기고 운영 수용 기준으로 사용하지 않는다.

## 외부 자료 요청 묶음

- 새 기관 교육/비교육 기록 원문과 대응 careers 응답. name/description만으로 부족하면 원본 수료증의 시간·기간 표시 부분 포함.
- 원문 사용 허용 및 익명화 요구, 원본과 응답의 record_id/subject_did 대응표(공유 허용 범위 내).
- 교육 유형·수료 기준·실제량/계획량 의미에 대한 제공자 설명.
- 정답 작성자·독립 검토자·최종 판정자 지정.

없으면 준비 구현으로 종료한다. 새 자료를 합성으로 대체하거나 에이전트 초안을 사람 gold로 부르지 않는다.

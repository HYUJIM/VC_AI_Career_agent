# 최소 합성 본 실험 계약 2.1

2026-10-11 / 사용자 요청으로 구현 / gold 및 실제 평가 승인 대기

## 범위와 버전

고정 후보에서 B 전체 문맥과 C 정책 필터 문맥을 비교한다. A는 무근거 기준선이다. 서명·정책 적격성, 사실 진실성, 문맥 지지를 분리한다. 의미 검색/운영 BE/모델 변경은 포함하지 않는다.

기존 paper-case/review 2.0과 개발 lock 1.0은 유지한다. 새 case/review 2.1과 study lock 2.1의 `single_human_v1`에서만 한 사람의 확정 검토를 허용한다. 기존 2.0 호출의 2인 요구는 유지한다. 검토 양식이 동결된 실험 정책을 스스로 완화할 수 없다.

## 자료와 실제 검증

- 새 dev 2 families/test 8 families(백엔드4/데이터4), family당 JD1개와6층: none/forged_signature/revoked/expired/untrusted_issuer/valid_conflict.
- 후보는 안정2개+표적2개. invalid4층은 C2개, clean/conflict는 C4개. 본 실험144건, 별도 dev36건 계획.
- invalid4층의 참/거짓 표적은 `(family.index + invalid_type.index)%2`로 균형 배정한다. 폐기·만료됐다고 역사적 이수 사실이 거짓이 되는 것은 아니다.
- T는 agent가 작성한 합성 세계이며 외부 독립 사실이 아니다. 사용자 검토 전 pending. required3개/retained2개를 조건에 따라 줄이지 않는다.
- 원문 compact JWS를 실제 Ed25519로 서명하고 기존 VC pipeline으로 검증한다. did:fixture와 격리된 로컬 trust/revocation 정책을 사용한다. ledger anchor는skip이며 실운영 신뢰를 주장하지 않는다.
- 기존 fixtures/keys를 수정하지 않는다. 새 테스트 개인키는 메모리에서만 사용하고 저장하지 않는다. 공개 DID, JWS 원문, 검증/정규화 결과, 코드 해시, 실제 체크 시각을 보존한다. 공개 스킬 매핑 사전은 원본에서 복사한다.
- 동일 합성 문서가 적격해도 내용은 거짓일 수 있다. valid_conflict는 적격성0%노이즈, 의미 조작50%로 구분한다.

## 실행 고정과 승인 파일

기존 Qwen revision `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`, greedy/seed42/num_beams1, CUDA/FP32/eager, input2048/output256이다. 프롬프트는 사실최대3문장+실제ID 인용, 미확인1문장, 제안1문장이다. B/C의 공통 후보·질문·순서를 유지하고 C는 삭제만 한다. test 결과로 프롬프트나 예산을 바꾸지 않는다.

`prepare`는 승인 없이 입력과 토큰을 검사하고 pending lock을 만들 수 있다. `test`의 실행은 두 파일이 실제로 제공되어야 가능하다.

1. gold_review: 세계 원본 바이트SHA256을 scope_sha256으로 참조하는 사용자 검토 기록.
2. declaration: pending lock에 출력된 study.scope_sha256을 참조하는 프로토콜·실행 설정 확인 기록.

파일 계약은 UTF-8 JSON이며 정확한 필드는 `kind`, `reviewer_id`, `reviewed_at`, `scope_sha256`, `decision`이다. 실제 사람이 확인한 뒤에만 kind=human, 실제ID, timezone 포함시각, decision=approved를 기록한다. 도구는 이를 대신 채우지 않는다. 파일의 선언·해시는 사람 신원 인증을 대신하지 않는다.

plan의 gold_review/declaration에 파일 경로를 지정해 새 이름의 lock을 준비한다. 원래 bundle·T는 수정하지 않고 lock 안의 case만 검토 파일에 연결된다. scope에서 무작위 run UUID와 생성시각을 제외하므로 동일한 자료/코드/프롬프트/환경/순서에 대해서는 다음 prepare에도 같은 scope를 확인할 수 있다. 코드나 자료가 바뀌면 새 scope 확인이 필요하다.

`execution_ready`는 실행 요건만 뜻하며 `paper_ready=false`는 유지한다. 개발 입력은 gold가 pending이어도 명시적인 개발 점검을 허용하지만, test는 요건을 검사한 뒤에만 모델을 로드한다. 기본 max-runs15를 넘어서는 본 실행에는 명시적 `--max-runs 144`가 필요하다.

## 평가

주 지표는 factual_no/(yes+no)의 B−C 차이다. invalid4층 모두 정의되는 family 안에서 층 평균 후 family 동등 가중 평균. primary에서 제외된 family와 이유를 기록하며 이용 가능한쌍 분석은 별도 민감도 표로 제공한다. bootstrap2000/seed42/percentile95%, family<2이면CI=null. 8family는 실행 가능 규모 제안이지 검정력 보장이 아니다.

보조 지표는 부적격 문서 사용 포함 응답률/주장별 부적격률, 문맥 비지지, 인용, coverage_all/retained, unknown, 보류와 과잉보류, 원장 전체의 성공·실패·누락·length다. clean/conflict 및 truth-parity별 기술통계도 별도 제공한다.

인용 누락·가짜 ID는 사실 주장에 대해 citation_correct=no. supporting_record_ids에는 실제 문맥의 지지 근거만 기록한다. cited_record_ids에는 허구 ID도 남긴다. used_ineligible_record_ids는 실제 제공된 부적격 문서의 채택 증거이며 rationale이 필요하다. 아무 근거 없는 환각이나 문서를 거부한 설명을 자동으로 부적격 채택으로 세지 않는다.

무주장 오류율=null/coverage0. failed/missing은 의미 점수 없음. length는 원문 그대로 평가하며 해석 불가능한 조각은unknown. pending/disputed의 확정 지표는 만들지 않는다. retained C−B≤−0.10은 실무 경고일 뿐 비열등성 판정이 아니다.

동일 평가자가 144답변을 검토하고, 유형×조건당2개씩 사전선정36슬롯을48시간 후 재평가한다. 재평가 패킷은 별도 무작위ID로 제공하며 이전 점수가 없다. 실패한 선정 슬롯을 대체하지 않는다. `aggregate --repeat-reviews`는 동일 사람·새 원검토 파일·48시간 간격을 검사하고 분해 변화 및 공통 주장 축별 일치도를 계산한다. 평가자 간 신뢰도라고 부르지 않는다. 최초 점수는 자동 덮어쓰지 않는다.

## 재현과 제한

case/원본JWS/verification bundle/public snapshot/hash/환경/모델/프롬프트/plan/result/review를 연결한다. 자동 재시도·best-of 선택·본 실험 자동 실행은 없다. 결과는 새 폴더에만 쓴다. 신규 스키마에 맞는 데이터가 있더라도 사람의 의미 검토는 별도다.

본 실험의 모집단 일반화, 단독 평가 편향 제거, 운영 VC 표준 적합성, 외부 사실 진실성은 이 구현으로 입증하지 않는다.

# GPT-6.1 Sol 전달용 구현 지시서

아래 지시서는 설계 완료 문서이며 이미 구현됐다는 뜻이 아니다. 사용자가 구현을 요청한 다음 턴에서 적용한다. 이 문서를 읽는 것만으로 사람의 gold 승인이나 본 실험 실행 승인이 생기지 않는다.

## 목적과 기준

저장소 `C:/Users/bje10/VC_AI_Career_agent`, AI 브랜치, 로컬 현재 상태 기준으로 작업하라. 2026-10-11 확인 HEAD는 `85523350d8cbcef71d3ee9ffd1fafb2b0b2b4a31`이며 실행 관리 신규7개와 ai.zip은 미추적이다. 시작할 때 Git 상태를 다시 확인하고 기존 변경을 보존하라. ZIP 복원·전체 저장소 분석은 하지 마라.

목표는 2026-10-23 제출을 위한 **고정 후보 VC 정책 필터의 소규모 합성 통제 실험**이다. A=무근거, B=전체 후보, C=같은 후보에서 정책 적격만 남김. 모델 변경 없이 현재 Qwen2.5-1.5B-Instruct/CUDA/FP32/eager를 재사용한다. 모델 효과·의미 검색·운영 진위를 일반화하지 마라.

권고 설계 전문: 같은 전달 폴더의 `VC_AI_MINIMUM_MAIN_STUDY_DESIGN_20261011.md`. 해당 문서와 이 지시서의 숫자/지표를 다르게 구현하지 마라. 방법론 선택과 실제 gold/검토 승인은 사용자 기록이 있어야 한다. 자료/승인이 없어도 pending 생성·검사 코드와 단위 테스트는 먼저 완성하라.

## 1. 고정할 실험 계약

### 데이터

- 기존5초안/1family는 dev로 보존. 신규 dev2 families, 신규 test8 families(백엔드4/데이터분석4), family당 JD1개.
- family별 clean, forged_signature, revoked, expired, untrusted_issuer, valid_conflict의6사례. 총 test48cases×ABC×seed42=144 planned runs.
- case마다 후보4개: 항상 적격인 안정문서2개+표적2개. invalid4유형은 표적2개 탈락하여 C2개. clean/valid_conflict는 C4개.
- invalid 유형의 정책 부적격률50%; valid_conflict는 의미 조작 문서50%이지만 부적격률0%. 이 두 비율을 혼용하지 마라.
- family i=0..7, invalid type j=0..3의 parity `(i+j)%2`로 표적 내용 참/거짓을 균형 배정. 배정을 manifest에 저장하라. true는 T 일치, false는 명시 모순이다. 스키마/서명 상태로 사실 라벨을 자동 결정하지 마라.
- family ID/DID만 바꾼 재활용은 독립 family가 아니다. 원천 개인 사실 lineage 및 split 겹침 검사 필요. 공통 스키마/JD 구조/발급자 공유 자체를 개인 사실 누출로 오판하지 마라.
- family별 기준사실 T와 required3개(안정2+표적1)를 먼저 고정. T 범위/명시 반례/단위/시점을 정의하고, 조건에 따라 required 분모를 바꾸지 마라. 안정2개는 retained_fact_ids로 별도 지정.
- gold_origin은 `synthetic_world` 또는 `external_document`. AI 초안은 사용자 실제 검토 전 pending. 합성 세계의 일관성 확인을 외부 독립 사실 확인이라 쓰지 마라.
- 노이즈 입력 전에 T를 저장하고 SHA256을 고정하라. 이후 노이즈 생성은 T 읽기 전용. 모든 claims/checks를 기대값만으로 생성하지 마라.

### 생성 및 정책

- 기존 six required checks(schema,did_resolution,signature,issuer_trust,revocation,expiration) pass, anchor_match pass/skip 및 verified 요구를 재사용한다. anchor skip=원장 앵커 검증 완료가 아님.
- 고정 revision `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`, greedy/do_sample=false, num_beams=1, seed42, input2048/output256, local_files_only=true, FP32/eager 유지.
- 출력은 사실 최대3개의 짧은 문장+각 실제 record_id, 미확인1문장, 제안1문장. 근거 없으면 사실을 만들어 채우지 않는다. record_id 예시를 실제 사실처럼 프롬프트에 넣지 마라.
- 모든 조건에서 같은 system/JD/profile/request·출력 예산. profile에는 평가할 보유 사실 없음. C의 유일한 문맥 처치는 필터 삭제이며 순서 유지/재충원 없음.
- 본 실험 문서명·본문·issuer label·ID에서 “위조 테스트”, “revoked”, “shady” 등 라벨 누출 제거. 중립 서식에서 자료를 생성하고 원본 기존 fixture는 수정하지 않는다.
- 현재 allowlist 유지: expires_at을 새로 노출하지 않는다. 원장에 raw/candidate/final count·정책 노출률·의미조작 노출률·input tokens를 구분해 기록한다.
- 기존 조건순서6순열 반복을 재사용한다. clean/valid_conflict B/C 메시지 해시 동일성을 검사한다. A 반복이나 greedy seed 반복은 독립 n으로 세지 않는다.
- dev 파일럿은 별도 허용 시만 신규 dev2의 clean/forged/valid_conflict×ABC=18건 이내. 형식/길이 기준으로 최대1번 형식 수정 후 동일18건 재확인 가능. test 결과에 따른 조정 금지.

## 2. 수정 대상과 재사용 방식

### 2.1 버전 있는 계약 추가

기존 v2 스키마·기록을 소급 변경하지 말고 `ai/data/paper_experiment_v2_1/`에 새 case/review/experiment schema와 pending 예제를 추가하라. 결과 원문 포맷은 현재 paper-result/2.0을 재사용할 수 있다.

- case2.1: 기존 case 내용+gold_origin, gold_source_hashes/lineage, retained_fact_ids, 실제 gold_review(검토자ID/시각/원검토 해시), closed_world_scope. retained는required의부분집합이며 항상적격근거와 연결되어야 한다.
- review2.1: `review_policy=single_human_v1|dual_human_v2`. claim에 `cited_record_ids`(허구 ID도 기록 가능), `used_ineligible_record_ids`, `ineligible_use_rationale` 추가. 기존 supporting_record_ids는 실제 제공된 지지 문서만 허용.
- experiment: phase=dev|test, protocol_id, review_policy, family/split/case와 primary-strata manifest, gold/VC artifacts hash, 모델·프롬프트·코드·환경·seed·plan hash, source provenance, 사용자 declaration ID/시각. 다중 승인 인프라 없이 파일 하나로 묶어라.
- artifact hash와 canonical JSON digest의 의미를 분리한다. 선언 본문이 참조하는 scope hash에 자기 자신을 포함하는 순환 해시는 금지한다. 선언을 제외한 불변 scope의 digest를 declaration이 참조하게 하라.
- 상태를 단순 paper_ready=true로 승격하지 마라. `execution_ready`, `review_policy`, `gold_origin`, `review_complete`를 분리하고 출판 적합성은 자동 판정하지 마라.

`ai/src/rag/paper_evaluation_v2.py`의 validate_schema/validate_case/summarize_response에 명시적 버전 dispatch와 정책 인자를 추가하라. 코드 중복을 줄이고 검증/점수 계산 본문은 재사용하라. 정책 기본값이 생략된 기존v2 호출은 지금처럼 정확히2인을 요구해야 한다. 새 단독 정책은 동결 manifest에서 명시적으로 전달된 경우에만 실제 사람1명을 허용하라. review 자신이 임의로 더 느슨한 정책을 선택하면 거부하라. 결과 해시/span/refs/gold/pending/disputed 차단은 유지하라.

### 2.2 실행기

`ai/src/rag/paper_experiment.py`:

- `_drafts`의 dev 제한을 단순 삭제하지 마라. 기존 development-lock/1.0 경로는 유지하고, 새 본 실험 envelope/version에서만 test를 허용하라.
- prepare 단계에서 formal case와 입력초안, gold origin/확인, 실제 VC artifact를 먼저 bind하라. 현재처럼 aggregate에서만 bind하면 본 실행 전에 정답·문맥 불일치를 못 막는다.
- `validate_lock`은 phase별 계약/선언/hash/입력/코드/환경/정책·144개 계획을 검사한다. test family가 dev lineage에 없으며 모든 case/condition 계획이 빠짐없이 있는지 확인하라.
- `CODE_FILES`/fingerprint에 실제 사용한 신규 스키마·정책·생성 코드가 포함되게 하라. 파일 미추적 상태도 실제 bytes hash로 고정 가능하지만 Git에 저장됐다고 주장하지 마라.
- `bind_cases`, `_payloads`, `load_results`, UUID 신규 폴더, 결과 해시, 원문 보존, 단일 모델 로드/독립 대화, 실패 시 중단을 재사용하라.
- 전체 예정 실행 분모를 유지하라. max-runs 기본값 15는 유지하고 본 실험 144건은 명시적 `--max-runs 144`가 있어야 한다. 필요하면 사용자 실행을 여러 세션으로 나누되 전체 계획을 재작성하거나 임의 case를 골라 버리지 마라. 최소 구현은 단일 명시 실행이며 복잡한 재개 스케줄러는 만들지 마라.
- 실패/누락을 숨기는 자동 재시도는 하지 마라. 복구가 필요하면 새 attempt가 최초 결과를 대체하지 않는 별도 기록으로 남겨야 한다. 구현 범위 초과면 원장과 재개 제한을 보고하라.

`ai/scripts/run_paper_experiment.py`:

- 기존 CLI 진입점과 prepare/run/inspect/aggregate를 유지하고 새 phase/manifest 경로를 명시적으로 추가하라.
- 형식만 유효한 계획이 곧 본 실행 허가가 되지 않게 한다. 단위테스트·자료 준비 후에는 준비 결과를 보고하고 test 생성은 사용자의 해당 실행 지시가 있을 때 진행하라.
- aggregate는 동결 정책을 전달하고 전체 planned_runs, 원검토 해시, 제외 사유, primary/대조층 분리 결과를 출력한다.

`ai/src/rag/paper_context_renderer_v2.py`:

- 기존 payload를 바꾸지 않도록 기존 prompt를 기본값으로 보존하고 새 프로토콜의 짧은 출력 지시를 명시적 버전/인자로 선택하라.
- 기존 `_project` allowlist와 후보 정책·감사 일치 검사를 재사용한다. 조건명/노이즈/gold/제외 이유는 모델 메시지에 넣지 않는다.

`ai/src/llm/transformers_llm.py`:

- 변경 불필요가 기본이다. 256토큰을 이미 지원한다. 모델·장치·dtype·라이브러리 업그레이드 금지.

### 2.3 검토 및 집계

`ai/src/rag/paper_experiment_review.py`:

- pending_review/make_review_bundle에 명시적 정책과 버전을 전달한다. reviewer를 채워 넣거나 자동 승인하지 않는다.
- `_validate_originals`는 단독1개/기존dual2개의 실제 원검토 bytes/hash/result/ID 연결을 검증한다. AI 또는 같은 사람이 다른 ID 두 개를 쓴 것을 독립2인으로 만들어 주지 마라.
- 최초 원검토를 보존하고 재평가 원본은 별도 경로/해시로 저장한다. 재검토36슬롯은 manifest 기준 유형×조건별2개, seed20261011로 사전 선택한다. 48시간 후 재평가용 패킷에는 이전 판정이 노출되지 않아야 한다. 실패한 선정 슬롯을 좋은 답변으로 교체하지 마라.
- 현재 패킷은 조건 키와 T/정책을 함께 보관한다. 1차 답변/문맥 패킷, 2차 gold/policy 자료, condition key를 분리하되 부분 눈가림의 한계는 설명하라. UI나 접근제어 서버를 만들 필요는 없다.

주 지표:

```text
FER_r = factual_no / (factual_yes + factual_no)
Δ_family = mean(B_FER - C_FER over four invalid strata)
primary = equally weighted mean(Δ_family)
```

네 invalid 층 모두 B/C 지표가 정의된 family만 주 집계. 불완전 family 수/제외 이유 필수. 기존 이용 가능한쌍 집계는 별도 민감도 표로 유지. clean/valid_conflict 및 truth-parity 층은 보조표. bootstrap은 family 단위2,000회/seed42/percentile95%; n<2는CI=null. 작은 합성표본의 탐색적 CI이며 n=144로 계산하지 마라.

보조표:

- 부적격 문서 사용 포함 응답 수/평가완료 성공 응답 수. no-claim 성공은사건0. 실패는 별도 예정분모.
- claim evidence_eligibility=no/(yes+no), 실제 인용 또는 고유 내용 채택의 ID와 이유 필요. 가짜 인용/무근거 환각은 자동으로부적격 사용이 아니다. 부적격 자료를 거부하는 설명도 사용사건이 아니다.
- context_support=no/(yes+no).
- citation_correct=yes/(yes+no): 사실 주장에 인용 누락·없는 ID·지지하지 않는 인용은no; 인용 없다고분모에서제외하지마라.
- coverage_all=정확한required fact수/3, coverage_retained=정확한stable fact수/2. 단순히 조건별 근거가 없다고분모를줄이지마라.
- unknown 비율, 주장수, 전면/부분 보류, stable근거 있는데 전면거부한 비율, input/outputtokens, length, 실패/누락.
- 무주장 FER=null/coverage0, failed/missing은 의미점수 없음, length원문 평가 및 완결불가능 명제unknown. 성공했지만 미검토/disputed는 별도제외.
- retained coverage의 C−B가−0.10 이하이면 사전 실무 경고 표시. 비열등성 검정/확정적 안전 마진으로 표현하지마라.

가능하면 외부 보고 열은 factual_error_rate로 명시하라. 기존 factual_correctness 이름의 no비율 구현을 외부에서 정확도라고 표시하지 마라. F/O/D_JD 기존 세부 분해는 유지할 수 있지만 새로운 주 지표나 가중 HSI는 만들지 마라.

### 2.4 합성 자료·실제 서명 증빙 준비

신규 `ai/scripts/prepare_paper_study.py`와 `VC/scripts/prepare-paper-study.ts`를 최소 오케스트레이터로 추가하는 안을 권고한다. 필요 이상 여러 계층으로 나누지 마라.

- Python 쪽은 사전 작성/사용자 확인된 T를 읽어 case/manifest/draft를 조립·검사한다. 혼자서 확인되지 않은 정답을 approved로 채우지 않는다.
- TypeScript 쪽은 기존 `signCompactJws`, `verifyAndNormalize`, schema/normalizer와 fixture adapters를 재사용한다. 단일 compact JWS 형식으로 suite 혼입을 줄인다. 별도 출력루트/FIXTURES_DIR가 원래VC/fixtures와 다른지 검사한다.
- 기존 generate-fixtures.ts는 student-001 및 원래 파일 출력을 고정하므로 직접 재실행하지 않는다. `npm run setup:fixtures`도 원래키/fixture를 덮어쓸 수 있어 금지한다.
- 테스트키를 읽거나 새 격리 테스트키를 만들 수 있으나 개인키 내용을 응답/로그/공유 manifest에 출력하지 않는다. 공개 DID 문서와 서명 원문은 재현 자료로 보존한다. 기존 keys/fixture를 덮어쓰지 않는다.
- trust/revocation adapters는 격리 snapshot을 사용한다. 서명/pass/checks는 반드시 실제 검증 결과에서 가져온다. 잘못된 체크나 형식은 quarantine/fail로 보존한다.
- 기존 checkExpiration은 Date.now를 쓴다. 이번 최소안에서는 검증 실제 체크시각을 보존하고 expiry를 실험기간 경계에서 충분히 떨어뜨린다. freeze에는 각 검증 artifact 시각과 실제 검증 window를 기록한다. 로그를 임의 기준시각으로 바꾸지 않는다. 고정시각 재현성이 추가 필요하면 별도 좁은 변경과 테스트로 제안하되 시스템시계를 바꾸지 않는다.
- formal case의 verifier_revision은 실제 코드 해시/revision, suite는 실제사용값, artifact_sha256는 실제 검증 report로 연결한다. check detail이 원본에 없는 경우 정책상 정해진 빈 문자열로만 형식 정규화하고 새 검증 설명을 발명하지 않는다. 원문 로그를 함께 보존한다.
- 기존 normalized record/raw_hash와 새 wrapper의 hash 정의를 구분해 실제 바이트 연결을 테스트한다. label만 바꾼 기존 record를 새로운 서명된 원본으로 가장하지 않는다.
- 신규 harness에서 기존 VC 모듈 변경이 불가피하면 필요한 수정만 먼저 제시하라. 운영BE/DX통합으로 확장하지마라. 연결 불가 시 증빙 gap을 보고하고 성공체크를 꾸며 계속하지마라.

### 2.5 문서

- 기존 `ai/docs/paper_experiment_protocol_v2.md`는 역사적 계약으로 보존하고 새 `paper_experiment_protocol_v2_1.md`에 solo, synthetic,144runs,지표/동결을 명시한다.
- `ai/docs/PAPER_DEVELOPMENT_RUNNER.md`에 구버전dev와 새test 경로의 차이/예시를 추가한다. 아직 구현되지 않은 명령을 동작한다고 쓰지 마라.
- 초기 예제 manifest에는 사용자 승인/실험 결과를 미리 채우지 않는다.

## 3. 완료 조건

1. 기존 v2 입력/테스트의2인 계약이 유지되고 새solo는 동결 정책과 일치할 때만1인 허용된다. test와dev을 섞거나 정책을 사후 완화하면 차단된다.
2. T → 오염 입력 → 실제 서명/검증 → formal case/draft → 동일 B/C후보 → payload/result/ledger → 사람review → 요약의 파일/ID/hash 연결이 추적된다.
3. 신규 signed-fixture에 대해 six-pass/한가지실패/anchor_skip의 예상과실제 checks를 비교한다. 내용 참/거짓과적격성이독립 배정되고valid_conflict는둘다통과한다.
4. 계획 검사로 test8family/48case/144run, invalid층별 true4/false4,각C후보수,dev/test lineage분리를 확인한다. 실제 사람 승인 없을 때는 execution_ready=false와정확한blocker가남는다.
5. 모든 후보 preflight2048이내,일관된256output,조건라벨누출없음. clean/conflict의B/Cmessagehash일치. 초과시자동truncation금지.
6. 평가 fixture로 no-claim/null,부적격참/적격거짓,없는citation,인용누락,refusal,length,failed,missing,disputed,중복claim/family의올바른분모와제외를확인한다.
7. review packet/원검토/재검토/조건키가분리되고실제human승인외에는점수를확정하지않는다. 사람 재평가를독립2인으로계산하지않는다.
8. full144planned분모를보존하고primaryinvalid4층·완전family·bootstrap을검증한다. 가능한쌍민감도와대조층을별도로보고한다.
9. 기존outputs/dev_drafts/미커밋파일을보존하며새파일/변경목록과테스트결과,남은사용자검토항목을보고한다.

코드 준비 완료, 실행 준비 완료, 본 실험 완료, 사람 평가 완료는 각각 다른 상태다. 위 조건을 테스트 대역으로 확인했다고 실제 본 실험 144건을 완료했다고 보고하지 마라.

## 4. 관련 테스트만 실행

먼저 기존 관련6파일과 새 계약/데이터 테스트만 선정한다. 전체 pytest/npm test 금지.

```powershell
python -m pytest -q tests/test_paper_experiment.py tests/test_paper_experiment_review.py tests/test_paper_evaluation_v2.py tests/test_paper_context_renderer_v2.py tests/test_paper_vc_adapter_v2.py tests/test_transformers_runner.py tests/test_paper_study_contract.py
```

마지막 파일은 구현 과정에서 새로 작성할 테스트다. 결과를 구현과 같은 공식으로만 재계산하는 복제 테스트보다 손으로 기대값이 명확한 작은 반례를 써라. 예: 두 family의 주장수가 크게달라도 family평균가중치가같아야함; 참이지만부적격인주장을사실오류로세지않음; 가짜인용은supportingID에넣지않아도검출됨.

VC 새 harness를 작성하면 신규 `VC/tests/paper-study.test.ts` 같은 전용파일의 실행만한다. 실제 테스트 배치규칙을 필요한범위에서먼저확인하고기존해당crypto/check 테스트가필요할때만추가한다. vitest 전체 실행으로확장하지마라. 테스트는격리임시fixture만생성하며원본키/레지스트리수정금지.

**테스트 실패 후 수정은 최대 2회.** 그 안에 해결되지 않으면 실패 원인·남은 문제·실행 준비 상태를 보고하라. 불필요한 반복/전체 회귀/LLM 호출로 대체하지 마라. 단위 테스트의 모델은 대역을 사용한다.

## 5. 이번 구현에서 하지 않을 작업

- 모델교체/양자화/성능튜닝, 의미검색/RAG전반확장, BE운영통합, MCP, UI, DX Ledger운영연결.
- 기존ZIP복원, 원본outputs/초안/키/미추적파일삭제·대규모이동, reset --hard, clean -fd.
- 임의gold승인, 사람ID/시각/원검토/실험결과/VC pass날조.
- 기존2인계약의조용한완화, 단독재평가나LLM을독립검토자로계산.
- 결과를보고노이즈/표본/프롬프트/예산/주지표수정, 성공결과만선택, 자동재시도.
- 장시간추론·본실험자동실행. 필요한경우준비완료보고후해당실행지시를받는다.
- 사용자사전승인없는push/merge. 커밋은현재사용자지시와변경파일검토범위에따라판단하고임의로모든미추적파일을stage하지마라.

## 6. 사용자에게 보고할 내용

수정 파일과 목적, 관련 테스트 결과, dev/test 실행 준비 여부, 실제로 확인된 검증 artifact 수, 사용자가 검토해야 할 T/정책/답변, 본 실험 미실행 여부를 간결히 보고하라. 남은 부분을 숨기거나 준비된 도구와 논문 효과 입증을 혼동하지 마라.

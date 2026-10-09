# 다음 Work 인수인계 — 논문 본 실험 프로토콜 v2부터

최초 작성: 2026-10-08. 갱신: 2026-10-09 (Asia/Seoul). 파일명은 기존 참조를 위해 유지한다.

## 1. 목표와 우선순위 변경

사용자의 현재 목표는 **논문을 위한 AI 실험**이다. 프런트엔드·운영 BE 연결은 선행 조건이 아니다. 최신 팀 문서 검토 결과, 다음 Work는 **JD 기반 포트폴리오 생성 과제에 맞춘 본 실험 프로토콜 v2 작성**부터 시작한다. 그 후 평가기 확장, VC 검증 산출물 연결, 본 실험 데이터 준비로 진행한다.

두 팀 문서는 아이디어/참조 자료이며 모든 항목이 합의된 요구사항은 아니다. 프로토콜은 제안/미확정 항목을 명시한다. 사용자에게 같은 내용을 다시 설명하는 데 그치지 말고 독립적으로 가능한 문서·양식·코드 작업을 진행한다. 새로운 모델/유료 API 호출이나 동일 추론 반복부터 시작하지 않는다.

## 2. 저장소·파일 위치·Git 상태

- 저장소: https://github.com/HYUJIM/VC_AI_Career_agent
- 브랜치: AI
- 마지막 확인 HEAD: `bfb9fa1a8743dca474b8a4fc1089226e85f4028e`
- 저장소 루트: `C:\Users\bje10\VC_AI_Career_agent`
- AI: `C:\Users\bje10\VC_AI_Career_agent\ai`
- BE 별도 checkout: `C:\Users\bje10\VC_AI_backend\BE`
- 프로젝트 작업 공간: `C:\Users\bje10\.codex\.chatgpt-projects\g-p-6abbcc05c98481918cde46f03cf34db0`

2026-10-09 문서 반영 전 AI HEAD가 위 기준 커밋이며, 아래 신규 파일 11개와 ai.zip이 미추적 상태임을 재확인했다. 이번 Git 반영 대상은 이 인수인계와 다음 Work 프롬프트 MD 두 개뿐이다. 아래 구현 파일은 커밋 대상에서 제외하므로 **GitHub만 받아서는 최신 구현이 없다.** 다음 Work에서 문서 커밋 이후 실제 HEAD와 로컬 변경을 다시 확인한다. 두 문서의 저장소 위치는 `ai/docs/VC_AI_PAPER_NEXT_WORK_HANDOFF_20261008.md`, `ai/docs/VC_AI_PAPER_NEXT_WORK_PROMPT_20261008.md`이다.

AI 기준 신규 파일 11개:

```text
data/backend_education_v2/gold_template.json
docs/backend_education_contract_v2_proposal.md
docs/backend_education_gold_v2_review.md
docs/backend_education_display_v2_results_20261008.md
src/rag/backend_education_display_v2.py
scripts/run_backend_education_display_v2.py
tests/test_backend_education_display_v2.py
docs/paper_evaluation_protocol_v1.md
src/rag/paper_evaluation_v1.py
scripts/prepare_paper_evaluation_v1.py
tests/test_paper_evaluation_v1.py
```

기존 미추적 `ai.zip`은 보존한다. 기존 코드, frozen v1, 실행 결과, 로컬 변경을 덮어쓰지 않는다.

## 3. 먼저 읽을 자료

아래 `deliverables/`는 위 프로젝트 작업 공간 기준이다. 해당 보고서와 PDF는 이번 문서 커밋에 포함되지 않는 로컬 참조다. 접근할 수 없으면 필요한 자료만 요청하며, 핵심 결정 사항은 이 문서 5절에도 요약되어 있다.

1. `deliverables/team_paper_review_20261008/TEAM_PROPOSALS_REVIEW_20261008.md` — 최신 팀 제안 검토, 다음 프로토콜의 우선 입력.
2. `deliverables/paper_evaluation_v1/RESULTS_20261008.md` — 평가기 구현·실행 결과·한계.
3. `deliverables/paper_readiness_20261008/AI_PAPER_READINESS_REPORT_20261008.md` — 논문 준비 상태와 잔여 작업.
4. AI `docs/paper_evaluation_protocol_v1.md` 및 `src/rag/paper_evaluation_v1.py` — 현재 평가 계약과 코드.
5. AI `data/research_stage1/README.md`, `data/research_stage1/evaluation/rubric.jsonl`, `src/rag/pipeline.py`, `src/rag/context_policy.py`, `src/rag/retriever.py`, `scripts/run_transformers_experiment.py`.

팀 PDF 원본(각 2페이지, 본문·수식·페이지 이미지 확인 완료):

- `C:\Users\bje10\Downloads\6303d00c-1ee3-4711-a827-e5a9aae01af8_논문_환각_수치화_방법.pdf`
- `C:\Users\bje10\Downloads\88500ea4-a18c-44ac-b95d-528b9768720b_논문_작성_아이디어_-_동현.pdf`

이전 BE 연결 경위가 필요할 때만 `deliverables/VC_AI_NEXT_WORK_HANDOFF_20261008_bfb9fa1.md`를 읽는다. 당시 교육 QA 목표를 현재 논문 본 실험 목표와 혼동하지 않는다.

## 4. 현재까지 확인·완료한 것

### A/B/C 개발 파일럿은 이미 실추론 완료

AI `outputs/runs/run-/e5ebac0e-b6db-4d44-8782-19c99f4cb479`에 36건이 있다. 2026-10-04 실행, Qwen2.5-1.5B-Instruct, revision `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`, FP32/eager/greedy/seed42, 입력2048/출력128.

- 가상 인물2명/기록12개/질문12개, 6종 시나리오×2.
- A=근거 없음, B=동일 후보 전체, C=verified 필터 후 재충원 없음. 지시문/모델 동일, 검증 상태와 gold는 프롬프트에서 제외.
- 결과 누락/중복0, B/C 후보·필터 불일치0. 입력3+코드5 해시 일치.
- A: EOS12. B: EOS10/length2. C: EOS12.
- 검증 상태는 수동 정책값, 12×7=84개 check 모두 skip. 실제 서명 검증 실험 결과 아님.
- ListRetriever는 입력 순서, FixedCandidateRetriever는 고정 후보다. 의미 검색은 미구현.

“A/B/C 실추론 미실행”이라는 이전 안내는 정정됐다. **개발 추론은 완료, 논문용 본 평가·본 실험은 미완료**가 정확하다.

### 공통 평가기 v1

평가축: factual_correctness/context_support/evidence_eligibility/citation/task_correct/abstention_correct. 라벨 yes/no/unknown/not_applicable, 행동 answer/abstain/mixed/unassessable.

- 조건/모델/검증 상태를 숨긴 review_packets, 별도 adjudication_key, 원문 스냅샷·해시.
- 두 사람의 서로 다른 ID·시각, 주장 span/근거, 미해결 쟁점 없음 등의 필드를 검사해 human_approved만 집계. 사람 신원을 인증하는 시스템은 아니다.
- 미검토/분모0은 null. 에이전트 예비 판정은 점수에 미포함.
- 현재는 완전한 A/B/C 쌍만 수용. 실패 ledger, 본 실험 통계, Ragas/LLM judge는 미구현.
- 전체 **141 passed in 5.66s**: 기존120+신규21. 이는 소프트웨어 테스트이며 논문 성능이 아니다.

### 원답변 예비 검토

36건 모두 에이전트 메모만 작성, 사람 승인0. B 과잉 거절 후보2, C 과잉 거절 후보3. 발급일을 이수일처럼 설명하거나 거절하면서 근거 없는 시간 설명을 추가한 사례가 있다. 확정 오류율/환각률/통계적 유의성으로 표현하지 않는다.

### VC fixture 경로

VC `src/crypto/jws.ts`, `dataIntegrity.ts`에 실제 sign/verify 호출이 있다. 합성 서명 fixture로 통제 실험을 준비할 수 있으므로 기관 실자료를 무조건 기다릴 필요는 없다.

단, `eddsa-fixture-2024`는 테스트 suite이며 표준 전체 적합성/기관 진위를 보증하지 않는다. DID/신뢰/폐기는 fixture 기반, anchor는 skip. 이번 Work에서는 VC 검증기를 새로 실행하지 않았다.

기존 VC export `ai/outputs/vc_exports/7cd1b4d4-1040-45f1-a5db-d6f1150a4e86`는 9건 수신/8건 수용/1건 스키마 격리. B/C 공통 입력 전에 스키마 오류를 격리하므로 그 효과를 C만의 생성 성능으로 주장하면 안 된다.

## 5. 팀 문서에 따라 v2에 반영할 사항

1. **과제 차이**: 팀은 JD 기반 포트폴리오, 현재는 교육 이수 QA다. 프로필/VC/JD/생성 요청/허용 주장 목록/출처를 새 데이터에 포함한다. 기존36건을 새 과제로 소급 전환하지 않는다.
2. **기준 집합 분리**: V_raw(전체), V_eligible(정책 수용), T(독립 참고 사실)를 구분한다. VC에 없음=현실에서 거짓, 서명 유효=내용 참으로 단정하지 않는다.
3. **F/O/JD 오류**: 원본 이름/ID를 보존하면서 별도 평가용 entity 매칭·속성/단위 정규화가 필요하다. 기간과 이수시간을 비교/환산하지 않는다. 결측을0으로 바꾸지 않는다. 역할/직급은 단순 대소 비교 금지.
4. **중복 집계**: F=|G−V|, C=|(G∩J)−V|에서 후자는 전자의 부분집합이다. JD 관련 여부는 별도 태그 또는 상호배타 분류로 처리한다. C_verified_rag와 혼동하지 않도록 D_JD 등의 이름 사용을 제안한다. 출력에서 악의/기만 의도를 추론하지 않는다. 목표 역량 추천과 현재 보유 주장도 구분한다.
5. **HSI**: 1.0/0.5/1.2 가중치의 근거가 문서에 없다. 탐색적 보조 지표로 제안하고 오류별 건수/분모/필수 사실 커버리지/응답률을 함께 보고한다. 가중치 민감도 검토 필요. 전면 거절을 오류0으로 최고 평가하지 않는다.
6. **Ragas**: Faithfulness는 문맥 지지이며 전체 진실성/VC 검증 적격성이 아니다. 위조 자료를 충실히 반복해도 높을 수 있다. 기존 평가축을 유지하고 별도 보조 측정으로 추가한다. 버전/지표 변형/프롬프트/judge/미확인 처리 고정 필요.
7. **노이즈**: 10/30/50/70% 제안에0% 기준선 추가. 전체 풀/검색 후보/최종 문맥 중 분모 명시, 실제 노출 비율 기록. top_k2에서 문서 개수로10/30/70%를 정확히 구성할 수 없다. 위조/폐기/만료/신뢰미확인/유효 서명 간 내용 상충을 분리한다. 두 유효 VC의 상충은 verified 필터만으로 해결되지 않는다.
8. **비교 조건**: 기존 A/B/C=no evidence/naive/verified와 팀의 naive/advanced/advanced+verification은 다르다. 기존 이름 변경 금지. 최소 주 비교는 B/C. advanced 채택 시 D/E를 추가하고 동일 advanced에서 필터만 다른 E−D로 효과를 분리한다.
9. **Judge**: 상용 주 judge+보조 judge 일부 대조는 가능안. 문서 모델의 우수성/현재 Azure 이용 가능성을 보증하지 않는다. 20% 표본·상관0.85만으로 정확성/편향 제거를 선언하지 않는다. 층화·seed·표본수·사람 대조 필요. 기존 human_approved 경로에 LLM 결과를 넣지 말고 별도 저장·집계를 추가한다.

Ragas 공식 정의 확인: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/ (제공 문맥의 주장 지지 비율). 관련 Context Precision/Recall 지표도 버전/변형별 요구 입력을 확인해야 한다.

## 6. 다음 Work의 구체적 작업과 종료 기준

1. Git/파일/결과 존재를 읽기 확인한다. 다른 변경을 보존한다.
2. `paper_experiment_protocol_v2.md`를 새로 작성: 포트폴리오/JD의 제한된 출력 과제, B/C 주 비교, A 기준선, 데이터 단위와 독립성, 라벨/F/O/JD 정의, 주/보조 지표, 노이즈 분모, 실패·거절 처리, judge/사람 구분, 동결 조건. 확정되지 않은 부분은 proposed/pending.
3. 새 데이터·평가 스키마/빈 작성 양식을 마련한다. 프로필/JD/원본ID/해시/원문/허용 주장/검증 기록/노이즈 유형·비율/자료 가족·분할/사람 검토 상태를 담는다. 예시는 합성임을 표시한다.
4. 합의 없이 가능한 v2 평가기 확장을 구현·테스트한다. 원래 v1과 결과를 덮어쓰지 않는다. judge 수용 준비와 실제 외부 호출은 구분한다.
5. VC 검증 산출물→AI 입력 연결의 수용 조건/필드 매핑을 준비한다. 현재 fixture/key 재생성은 피하고 읽기 검증부터 시작한다. 기관/운영 진위로 확대 해석하지 않는다.
6. 완료/미실행/팀 확인 대기와 다음 분기를 보고한다. 사람 검토·본 평가 세트 고정·실제 모델/심판 호출을 하지 않았으면 명확히 남긴다.

종료 기준: 프로토콜v2 초안+데이터/평가 계약+독립 가능한 구현·테스트+팀 확인 목록을 재현 가능하게 남기는 것. 팀 합의나 사람 판정이 없는 상태에서 본 실험 완료로 종료하지 않는다.

필요한 외부 입력은 구체적으로만 요청한다: 포트폴리오 과제 최종 범위, advanced 비교 포함 여부, 사람 검토자, judge 배포/비용·전송 허용 범위, 주 지표·가중치 합의, 제출 일정. 답변 전에도 준비 작업은 진행 가능하다.

## 7. Git 밖 로컬 산출물

프로젝트 작업 공간 기준:

```text
deliverables/paper_evaluation_v1/runs/cc4b21a9-21de-41cb-a515-cc2dcde53419/
  bundle.json, review_packets.json, adjudication_key.json, reviews_pending.json,
  summary_pending.json, original_results.json, protocol_snapshot.md,
  queries_snapshot.jsonl, rubric_snapshot.jsonl, source_manifest.json, manifest.json
deliverables/paper_evaluation_v1/aggregations/70a60e12-13da-47c6-b470-abc7f28fc743/
deliverables/paper_evaluation_v1/agent_development_notes.json
deliverables/paper_readiness_20261008/existing_pilot_audit.json
deliverables/team_paper_review_20261008/TEAM_PROPOSALS_REVIEW_20261008.md
```

검토 묶음 artifact10개 해시 일치. 집계는 각 조건 승인0/대기12/점수null이다. 기존 결과 파일 해시는 후향적 감사 스냅샷이며 최초 사전 lock이라고 하지 않는다.

신규 코드 사본은 `deliverables/paper_evaluation_v1/ai/`, 교육v2 사본은 `deliverables/be_education_v2/ai/`에도 있다. 이들은 복구/참조용이며 실제 저장소 최신 상태와 대조 후 사용한다.

## 8. 보존·권한 경계와 접근 실패 시 요청

- `sources/`는 읽기 전용. frozen v1 cases/lock/계약·과거 outputs·ai.zip·현재 미커밋 변경 보존.
- 모델 원답변/원문 표시/에이전트 판정/사람 판정 분리. BE verified는 실제 VC 검증 증거나 논문 C의 충분조건 아님.
- 사용자 요청 없이 BE/팀 메시지 전송, main 변경, PR 생성·병합 금지. 2026-10-09 사용자가 요청한 이번 MD 두 개의 commit/push와 다음 Work의 Git 권한은 구분한다. 다음 Work에서 commit은 가능하지만 push/merge는 반드시 사용자의 승인을 받는다.
- 지정 저장소가 쓰기 허용 범위 밖이면 준비한 구체 파일을 보여줄 수 있게 만든 뒤 필요한 파일 작업만 권한 요청. 환경 제약을 우회하지 않는다.
- 자료 접근 불가 시 이 MD, 팀 PDF2개, 신규 AI 파일11개, 위 평가 묶음/메모/보고서, 기존 A/B/C run 전체와 Stage1 자료를 정확한 파일명으로 요청한다. 실제 자료 없이 내용을 추정하지 않는다. 모델 가중치/.env/API 키는 요청할 필요 없다.

## 9. 최신 크레딧 효율 지침

필요한 파일만 탐색하고 기존 구조를 최대한 유지한다. 요청한 기능은 직접 구현하되 관련 단위 테스트만 자동 실행한다. 테스트 실패 수정은 최대 2번까지 하고 해결되지 않으면 보고한다. 오래 걸리는 LLM 추론 실험, 전체 저장소 분석, 무관한 리팩터링은 하지 않는다. 완료 시 변경 파일과 테스트 결과를 간결하게 보고한다. 이번 2026-10-09 작업은 MD 두 개 정리 및 Git 반영만 하며, 141개 테스트 결과는 이전 실행 기록이고 이번에 재실행한 것이 아니다.

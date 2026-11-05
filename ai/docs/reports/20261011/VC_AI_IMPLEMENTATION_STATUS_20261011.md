# VC AI 최소 본 실험 구현·검증 보고서

2026-10-11 · 제출 목표 2026-10-23 · 로컬 AI 브랜치

**실험 준비 코드와 입력 사전 검사를 완료했다. 본 실험 생성과 사람의 의미 평가는 아직 수행하지 않았다.** 이 문서는 이전 진행 보고서 및 설계안 작성 이후의 구현 상태를 기록한다. 이전 문서의 “코드 미수정”, “dev만 허용”은 당시 상태이며, 새 study 2.1 경로에는 아래 변경이 추가됐다.

확인은 실제 파일·명령 실행에 근거한다. 규모와 평가 방법은 구현에 사용한 권장 기본값이다. 작업량은 추정이며 효과·완료 시간·통계적 검정력을 보장하지 않는다.

## 1. 직접 확인한 상태와 미완료 사항

- 저장소: `C:/Users/bje10/VC_AI_Career_agent`, 브랜치 `AI`, HEAD `85523350d8cbcef71d3ee9ffd1fafb2b0b2b4a31`.
- 기존 renderer, VC 어댑터, Qwen 실행기, 결과 저장, 검토 양식과 집계 코드를 재사용했다.
- 기존 case/review 2.0의 2인 계약을 유지하면서, 새 2.1 경로에 명시적인 `single_human_v1` 계약을 추가했다. 새 계약은 기존 계약과 동일하지 않다.
- 새 test 경로는 실제 사람이 기준 사실과 실행 설정을 확인한 기록이 없으면 모델 로드 전에 중단한다. 현재 두 기록 모두 없으며 `execution_ready=false`, `paper_ready=false`다.
- 신규 합성 dev 2 families와 test 8 families, family당 6사례, 합계 60사례를 생성했다. test 48사례의 A/B/C 144개 입력을 검사했다. 이는 생성 답변 144개를 얻었다는 뜻이 아니다.
- 실제 Ed25519 서명·검증 처리를 거친 기록 240건을 저장했다. 실패 유형마다 의도한 검사 실패를 확인하는 생성기다. 로컬 DID/trust/revocation fixture를 사용하며 ledger anchor는 skip이다. 실제 운영 VC의 신뢰나 표준 전체 적합성을 입증하지 않는다.
- 최신 토큰 검사: 입력 346~1,065, 한도 2,048, 출력 예산 256. 실제 모델 가중치는 로드하지 않았다.
- clean/valid_conflict 대조 사례 16개에서 B/C 메시지가 동일함을 확인했다. 다른 사례에서 C는 B 후보 순서를 유지하며 정책 탈락 문서만 제거한다. A 근거는 빈 배열이다.
- 실제 pending lock으로 실행 차단을 확인했다. 모델 factory 호출과 실행 폴더 생성이 모두 없었다.

**실제 연결 경로:** 합성 세계 T → 실제 서명/검증 및 정규화 → 기존 AI 정책 감사 → 같은 후보에서 A/B/C 문맥 구성 → 기존 Qwen 실행 함수 → 원장/답변/검토 파일 저장 → 1인 평가 집계. 이번에는 문맥·실행 lock 준비와 실행 차단까지 실제 점검했으며, 새로운 Qwen 생성 이후 경로는 단위 테스트로 검증했다.

BE SQL/저장 응답 경로는 기존 최소 실행기 영역이다. 이번에 운영 BE 서버/DB 연결이나 BE 코드를 변경하지 않았다. BE의 status-only `verified`를 상세 서명 검증 성공으로 대체하지 않는다. 새 signed-fixture 실험과 BE 데모는 별개다.

### 기존 생성 답변의 재확인

기존 `ai/outputs/paper_development/20261011-a57ad04c/smoke-run/`의 원장과 답변을 읽었다. 한 개발 사례, 같은 family의 A/B/C 총 3건이며 성공3/실패0/누락0이다. A는 128토큰에서 잘렸고 가짜 ID와 제공되지 않은 경력을 서술했다. B는 113토큰, C는 122토큰에서 종료했고 실제 record ID 인용이 없다. 이는 관찰이며 확정 의미 점수나 C의 우월성 증거가 아니다.

이 관찰에 따라 새 경로에는 사실 최대3문장·미확인1문장·제안1문장과 출력256을 기본값으로 넣었다. **새 프롬프트의 실제 생성 품질과 잘림 감소는 미확인**이다. test 결과를 보고 유리하게 바꾸지 않도록 dev에서 먼저 확인해야 한다.

## 2. 선택지 비교와 채택한 기본값

| 선택지 | 난이도·작업량 추정 | 위험 | 현재 호환성 |
|---|---|---|---|
| 기존 1 family 개발 진단만 보고 | 낮음, 기존 답변 검토 수시간 | 본 실험이나 일반화 주장 불가 | 기존 경로 유지 |
| **test 8 families, 144답변, 1인+재평가** | 준비 코드 구현 완료, 데이터 검토·답변 평가에는 별도 사람 시간 필요 | 합성·소표본·단독 평가 편향 | 새 2.1 계약으로 연결 완료 |
| 12 families 또는 전량 2인 평가 | 데이터/평가 부담 최소 약50% 증가, 2인 확보 필요 | 마감 위험, 설계·동결 갱신 필요 | 현재 고정 2-dev/8-test 검사를 수정해야 함 |

권장 기본값은 두 번째다. 앞선 설계의 사람 평가 12~20시간 등은 작업량 추정이다. 생성 속도가 빨라도 144답변 검토와 48시간 후 재평가를 오늘 모두 끝냈다고 할 수 없다. 8 families는 실행 가능 규모 선택이며 검정력 분석으로 산출한 표본수가 아니다. 답변144개를 독립 n=144로 세지 않는다.

## 3. 구현한 최소 본 실험 계약

### 자료와 연구 주장

- test는 백엔드4/데이터4 families, 별도 dev2. 기존 dev5개/1 family는 보존한다.
- family별 clean, forged_signature, revoked, expired, untrusted_issuer, valid_conflict의 6층.
- 각 사례 후보4개: 안정 근거2개와 표적2개. invalid4층은 B4/C2, clean·valid_conflict는 B4/C4다.
- invalid4층에서 참인 부적격 내용과 거짓인 부적격 내용을 유형별4 families씩 균형 배정한다. 유효하게 서명됐지만 거짓인 내용도 valid_conflict에 존재한다.
- 기준 사실 T를 먼저 파일로 만든 뒤 서명/오염을 생성했다. T는 에이전트가 만든 합성 세계이고 외부 독립 검증 사실이 아니다. 아직 사용자 검토 전이다.
- 역할 반례에서 “일부 작업 담당”과 “전체 개발 총괄”이 양립할 수 있는 문제를 발견해, 최신 T에는 총괄 역할을 맡지 않았다는 명시적 부정 사실을 추가했다. 이전 자료는 그대로 보존했다.
- source lineage·인물·프로젝트·수치를 분리하지만 공통 생성 템플릿의 영향을 받는다. 실제 모집단에서 독립 추출된 사람을 확보했다는 뜻은 아니다.
- 서명/발급자/폐기/만료 정책 적격성, 문맥 지지, T와의 사실 일치 여부를 각각 평가한다. 고정 후보 필터링의 비교이며 의미 검색이나 일반 RAG 전체의 성능으로 확대하지 않는다.

### 평가

- 주 지표: 사실 오류율 `no / (yes + no)`의 B−C. invalid4층의 완전한 B/C 쌍을 family 안에서 평균한 뒤 family 동일 가중 평균한다. 4층 중 정의되지 않는 쌍이 있으면 해당 family는 주 분석에서 제외하고 이유를 기록한다. 가능한 쌍만의 분석은 별도 민감도 결과다.
- family bootstrap 2,000회/seed42/95% percentile CI. family가2개 미만이면 CI는 null이다. 작은 합성 표본의 CI로 광범위한 효과를 입증하지 않는다.
- 보조: 부적격 문서 채택 포함 응답률, 주장별 부적격률, 문맥 비지지, 인용 오류, 전체 필수 사실/필터 후에도 남는 사실의 coverage, unknown, 보류/과잉보류, 실패·누락·잘림.
- 인용 누락과 가짜 ID는 사실 주장의 인용 오류로 남긴다. 부적격 문서 채택은 실제 제공 문서에 대한 채택 근거와 별도 이유가 있어야 한다. 환각을 자동으로 부적격 문서 사용으로 간주하지 않는다.
- 무주장 응답의 오류율은 null, coverage는0. failed/missing은 의미 점수가 없다. 잘린 답변은 저장된 원문대로 평가하며 해석할 수 없는 조각은 unknown으로 처리한다. pending/disputed에는 확정 지표를 만들지 않는다.
- retained coverage의 C−B≤−0.10은 실무 경고이며 통계적 비열등성 판정이 아니다. 조건별 이용 가능한 응답 평균에 기반하므로 누락이 다르면 대응 분석과 함께 해석해야 한다.
- 실제 1명이 전량 평가하고 유형×조건별2개, 총36개 사전선정 슬롯을48시간 후 재평가한다. 같은 사람의 일관성이며 평가자 간 신뢰도가 아니다. AI는 독립된 두 번째 평가자로 계산하지 않는다.
- 재평가는 새 패킷/ID로 분리하고 기존 판정을 자동 덮어쓰지 않는다. 실패한 선정 슬롯도 유리한 답변으로 바꾸지 않는다.

### 공정성과 한계

모델은 기존 Qwen2.5-1.5B-Instruct와 revision `989aa7980e4cf806f80c7fef2b1adb7bc71aa306`을 사용하도록 고정했다. greedy, seed42, beam1, FP32/eager 경로를 유지한다. 질문·후보·시스템 프롬프트·예산은 B/C 공통이고 조건 이름과 검증 감사 로그는 모델 메시지 밖에 둔다. 필터로 문맥 길이가 줄어드는 효과와 근거 정책 효과는 이번 설계로 분리하지 못한다.

바이트 해시·공개 검증 자료·코드 지문·설정은 변경 감지와 재현 추적을 위한 것이다. 사람 신원, 사실 진실성, 연구 승인 자체를 인증하지 않는다.

## 4. 변경 파일과 검증 결과

아래 경로는 모두 `C:/Users/bje10/VC_AI_Career_agent/` 기준이다. 기존 미추적 파일을 확장한 경우도 있으므로 전부 이번에 새로 만든 파일이라는 뜻은 아니다.

| 파일 | 변경 목적 |
|---|---|
| `ai/src/rag/paper_context_renderer_v2.py` | 기존 프롬프트 기본값 유지, 새 study 짧은 출력 지침 |
| `ai/src/rag/paper_evaluation_v2.py` | 2.1 스키마·명시적 1인 계약·세 가지 주장 축·retained coverage |
| `ai/src/rag/paper_experiment.py` | 기존 실행/저장 재사용, study 잠금 검증·실행 차단·재평가 패킷 |
| `ai/src/rag/paper_experiment_review.py` | 기존 2인 기본값 유지, 새 정책의 검토 양식/집계 연결 |
| `ai/src/rag/paper_study.py` | 자료 연결 검증, 동결 범위, family 주 분석·노출량·재평가 집계 |
| `ai/scripts/run_paper_experiment.py` | prepare/run/aggregate에 새 계약 연결 |
| `ai/scripts/prepare_paper_study.py` | pending T 생성, 실제 검증 증빙을 사례·계획으로 조립 |
| `ai/data/paper_experiment_v2_1/case.schema.json` | 합성 출처·검토·retained facts 계약 |
| `ai/data/paper_experiment_v2_1/review.schema.json` | single policy·인용/부적격 채택 근거 계약 |
| `ai/data/paper_experiment_v2_1/experiment.schema.json` | dev/test 계획과 확인 파일 연결 |
| `ai/tests/test_paper_study_contract.py` | 계약·원검토·분모·차단·재평가 관련 테스트 |
| `ai/docs/PAPER_DEVELOPMENT_RUNNER.md` | 기존 실행 설명에 새 study 경로 추가 |
| `ai/docs/paper_experiment_protocol_v2_1.md` | 새 프로토콜과 명시적 1인 평가 제한 |
| `VC/scripts/prepare-paper-study.ts` | 기존 VC 검증 모듈을 쓰는 격리된 실제 서명 증빙 생성기 |
| `VC/test/paperStudy.test.ts` | 실제 서명·의도한 실패·기존 fixture 보존 테스트 |

검증 기록:

1. Python 관련7파일 **150 passed**, 2.80초. 기존 pytest parametrize의 zip 사용 경고1개. 전체 테스트가 아니다.
2. 마지막 역할 문구 수정 후 직접 관련 `test_paper_study_contract.py` **17 passed**, 0.18초 재확인. 앞의150개와 중복이므로167개로 합산하지 않는다.
3. 앞선 중단 전 실행한 VC 전용 `paperStudy.test.ts` **3 passed** 기록. VC 코드는 이후 바뀌지 않아 다시 실행하지 않았다.
4. 최신 실제 자료 생성 **60사례/240기록**, 조립 성공. 최종 test 입력 **144/144** 토큰 검사 통과.
5. 최종 lock 실제 실행 차단·모델 미로드·동일 B/C 대조16개·필터 순서·재평가 슬롯36개 확인. 상세는 같은 폴더의 `VC_AI_STUDY_READINESS_FINAL_20261011.json`.
6. `git diff --check` 통과. 이 명령은 Git 추적 파일만 검사하므로 미추적 파일 전체의 정적 검증을 의미하지 않는다.

최초 격리 fixture 생성은 스킬 별칭 사전 누락으로 실패했으며 공개 사전 복사로 보완했다. 초기 실패 디렉터리는 보존했다. 중단 당시 승인 시스템 크레딧 문제는 재개 후 해소되어 사전 검사를 완료했다. 테스트 실패를 숨기거나 장시간 Qwen 실험을 대신 실행하지 않았다.

## 5. 최신 산출물과 필요한 최소 후속 조치

**사용할 최신 루트:** `C:/Users/bje10/VC_AI_Career_agent/ai/outputs/paper_study/20261011-role-clarified/`

- `world.pending.json`: 오염 전 T와 반례, split/JD/사실 목록. 아직 사람 미검토.
- `signed-fixtures/verification_bundle.json`: 실제 원문 JWS·정규화 결과·검증·공개 정책 자료.
- `prepared/bundle.json`: 동결 가능한 입력 묶음60사례.
- `prepared/GOLD_REVIEW_PENDING.md`: 읽기 쉬운 기준 사실40개 목록. 반례/JD까지 확인하려면 world도 함께 읽는다.
- `prepared/dev.plan.json`: 개발36건 계획. 이번에 생성하지 않았다.
- `prepared/test.plan.json`: 본 실험144건 계획.
- `prepared/test.pending.lock.json`: 최신 코드와 토큰 검사에 연결한 실행 대기 파일.

최신 세계 SHA256: `84de166d4bbe48b0d528a889b67dfb2bd768caf8cafd063eebcd3653f8ea0ca1`

최신 study scope SHA256: `12d2d2c4acc77e3bd23ba40288df893e94761596407297c17d02078883663bd2`

**사용자가 실제로 해야 하는 최소 작업은 합성 기준 사실의 의미 검토다.** 실제 자료로 주장하려면 외부 출처가 추가로 필요하지만, 현재 합성 통제 실험을 준비하는 데 반드시 가져와야 할 추가 파일은 없다. 학회 세부 양식과 규정은 제공되지 않아 확인하지 않았다.

후속 작업자에게 전달할 지시:

1. 위 최신 루트와 2.1 프로토콜을 사용한다. 구버전 `20261011-39974fff/`의 lock/scope를 섞지 않는다.
2. 사용자가 T·반례·JD·required3/retained2를 실제 읽고 수정 의견을 주도록 한다. 수정 시 기존 산출물에 덮어쓰지 말고 새 세계→새 서명 증빙→새 bundle→새 lock 순서로 갱신한다. 사람 검토를 에이전트가 승인했다고 기록하지 않는다.
3. 본 실험 전에 dev에서 출력 형식·인용·256토큰 예산을 확인한다. 이번에는 새 생성 결과가 없으므로 이를 완료로 표시하지 않는다. test 답변으로 프롬프트나 예산을 조정하지 않는다.
4. 실제 확인 이후 gold_review는 world SHA, protocol declaration은 최종 scope SHA를 참조한다. 정확한 JSON 계약은 `ai/docs/paper_experiment_protocol_v2_1.md`에 있다. 검토자 ID·시각·승인 의사는 사용자의 실제 확인에서 받아야 한다.
5. 현재 계획의 gold_review/declaration은 null이다. 실제 파일을 연결한 별도 계획과 새 lock을 준비한다. 코드/설정이 그대로면 scope가 유지되는지 확인한다. 기존 pending lock을 수동으로 execution_ready=true로 바꾸지 않는다.
6. 사용자 지시와 준비 요건 충족 뒤 본 실행을 수행한다. 현재 144건에는 명시적 `--max-runs 144`가 필요하다. 성공·실패·누락·length를 모두 보존하고 자동 재시도/best-of 선택을 하지 않는다.
7. 실제 답변·실제 원검토 파일로만 집계한다. 주 분석은 invalid4층 완전 family, 가능한 쌍 분석은 별도로 보고한다. C가 좋아졌다고 가정하지 않는다.
8. 추가 코드 변경이 있으면 관련 테스트만 실행하고 실패 후 수정은 최대2회로 제한한다. 이번에 실행한 관련 파일은 아래와 같다.

```text
ai/tests/test_paper_experiment.py
ai/tests/test_paper_experiment_review.py
ai/tests/test_paper_evaluation_v2.py
ai/tests/test_paper_context_renderer_v2.py
ai/tests/test_paper_vc_adapter_v2.py
ai/tests/test_transformers_runner.py
ai/tests/test_paper_study_contract.py
VC/test/paperStudy.test.ts
```

완료 기준은 실제 gold 검토→dev 출력 확인→설정 고정→144건 원장 확보→실제 1인 평가→재평가·집계·제한 기술이다. 현재 완료된 것은 준비 코드·합성 입력·관련 테스트·토큰 검사·실행 차단 점검까지다.

모델 교체, 운영 BE 통합, MCP/UI 확장, 전체 테스트, 장시간 추론, 기존 산출물 삭제, 무관한 리팩터링은 하지 않는다. push/merge는 사용자 사전 승인 없이는 수행하지 않는다.

## 보존 및 Git

기존 Work 2A/2B/2C, outputs, dev_drafts, ai.zip, 미추적 파일을 보존했다. 이전 lock·초안·실패 fixture 폴더는 보관 후보이며 삭제하지 않았다. 캐시 외 새 삭제 후보를 제안하지 않는다.

새 실험 자료는 outputs 안에 있어 일반 Git 상태에 표시되지 않을 수 있다. Git diff의 추적 파일2개만 보고 변경 전체라고 판단하면 안 된다. 실행 관리 파일과 신규 스키마·테스트는 미추적 상태다. commit/push/merge는 이번 작업에서 수행하지 않았다.

이전 설계의 선택지·평가 분모·구현 배경은 같은 폴더의 `VC_AI_MINIMUM_MAIN_STUDY_DESIGN_20261011.md`와 `VC_AI_GPT61_SOL_IMPLEMENTATION_BRIEF_20261011.md`를 참고하되, 실제 상태·최신 경로는 본 문서를 우선한다.

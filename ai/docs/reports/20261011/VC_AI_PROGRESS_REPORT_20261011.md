# VC_AI_Career_agent 진행 현황 및 인수인계 보고서

작성 기준: 2026년 10월 11일, 로컬 저장소 및 저장된 실행 결과 확인 기준.

## 1. 현재 결론

**BE 데이터를 AI 입력으로 사용하고 Qwen 답변을 저장하는 최소 연결은 구현되어 실제 실행 기록도 있다. A/B/C 개발 실험의 실행 관리·검토 양식·대응 집계 코드까지 추가했다. 다만 논문 본 실험과 AI 파트 전체가 완성된 상태는 아니다.**

현재 가능한 일은 다음과 같다.

1. BE의 SQL 시드 또는 저장된 careers 응답에서 특정 사용자의 경력 데이터를 읽는다.
2. 기존 어댑터와 검증 정책으로 데이터를 정리하고 A/B/C 입력 문맥을 구성한다.
3. 로컬 Qwen으로 답변을 생성하고 입력·출력·근거 ID·실행 상태를 보존한다.
4. 개발 사례 여러 개를 명시적인 계획으로 묶고, 입력 해시·모델 revision·토큰 예산·실행 순서를 고정한다.
5. 생성 답변을 사람이 검토할 양식을 만들고, 별도 정답과 승인 검토가 준비되면 B/C 대응 지표를 집계한다.

현재 완료됐다고 말할 수 없는 일은 다음과 같다.

- 독립 정답과 충분한 독립 표본을 갖춘 논문 본 실험.
- 두 사람의 승인 검토 또는 단독 평가에 맞춘 연구 프로토콜 개정.
- 본 실험용 test split 실행과 정식 연구 승인 freeze 연결.
- 실운영 BE의 상세 서명 검증 증빙 전달 및 운영 VC 검증 연동.
- B보다 C가 더 정확하다는 실험적 효과 입증.

## 2. 사용자의 목표와 이번 구현 범위

최우선 목표는 JD 기반 포트폴리오 생성에서 **B의 전체 후보 근거와 C의 검증 정책 적격 근거를 비교하는 논문 실험**이다. A는 근거가 없는 보조 기준선이다. 지갑 UI·추천·MCP·운영 통합은 우선순위 밖에 두었다.

사용자가 확인한 일정은 AI 작업의 당일 목표가 10월 11일, 제출 목표가 10월 23일이다. 검토 인력은 현재 사용자 혼자 진행하는 것으로 이해했다. 오늘의 짧은 실행 성공은 논문 제출 준비 완료를 뜻하지 않는다.

이번 작업에서는 기존 구현을 재사용했고, BE 팀 코드 변경·무관한 리팩터링·전체 테스트·장시간 추론·기존 산출물 삭제를 하지 않았다. 이 보고서를 작성하는 턴에서는 읽기 확인과 보고서 작성만 했으며, 테스트나 추론을 다시 실행하지 않았다.

## 3. 실제로 연결된 두 경로

### 3.1 BE 데이터 → Qwen 데모

```text
BE SQL 시드 / 저장된 careers 응답 / 기존 export / careers GET
  → BE 입력 어댑터
  → 보고된 검증 증빙에 대한 AI 정책
  → A/B/C 문맥 구성
  → 로컬 고정 revision Qwen
  → manifest·정책 보고서·답변·생성 메타데이터 저장
```

진입점은 `ai/scripts/run_minimal_pipeline.py`다.

| 구현 항목 | 현재 동작 | 한계 |
|---|---|---|
| SQL 시드 입력 | 특정 DID에 속한 사용자와 자격증명을 읽어 careers 형태로 변환 | 현재 시드의 지원 SQL 형식만 처리; 임의 SQL 엔진이 아님 |
| 원본 보호 | SQL을 읽기만 하며 DB에 실행하거나 적재하지 않음 | 실제 운영 DB 연결을 검증한 것은 아님 |
| BE 응답 입력 | 저장된 JSON·기존 export·careers GET 경로 제공 | 이번 결과로 실시간 BE 서버 전체 동작을 보증하지 않음 |
| 문맥 구성 | A 근거 없음, B 선택 후보 전체, C 적격 후보만 유지 | 의미 검색이나 제거 후보 재충원은 구현 범위 밖 |
| 결과 저장 | 새 UUID 폴더에 입력 출처·해시·정책 제외 이유·답변 저장 | 저장 성공이 답변의 사실성을 보증하지 않음 |

**BE 폴더의 데이터셋은 사용할 수 있다.** 다만 현재 시드는 합성 데모 데이터이며, 단순 `verified` 상태에는 서명 검증 상세 checks가 없다. 따라서 이 값을 서명 성공으로 취급하지 않는다. 현재 BE 데모에서 C의 근거는 비며, A/C 입력이 같아질 수 있다. 이 데이터로 BE 연결을 점검하는 것과 서명 검증 효과를 연구하는 것은 구분해야 한다.

실제 SQL 시드 경로:

`C:/Users/bje10/VC_AI_backend/BE/DB/seeds/03_seed_data.sql`

이전 실행의 저장 manifest를 이번에 다시 확인했다.

| 입력 | 저장 상태 | 확인된 실행 |
|---|---|---|
| BE SQL 시드 | `completed`, `generation_performed=true` | B 1건, 출력 예산 128토큰 |
| 저장된 BE 데모 | `completed`, `generation_performed=true` | B 1건, 출력 예산 16토큰 |
| BE SQL 시드·BE 데모·signed fixture | `checked` | 각각 모델 생성 없는 A/B/C 입력 검사 기록 |

BE SQL 시드 실제 생성 근거:

`C:/Users/bje10/VC_AI_Career_agent/ai/outputs/minimal_pipeline/0a631e45-08a5-4b8c-ad36-fff5c9cca8d6/manifest.json`

### 3.2 signed-fixture 개발 사례 → A/B/C 실행 → 사람 검토

```text
기존 Work 2B 개발 초안
  → CredentialRecord 형식 검사
  → 기존 Work 2C 정책 및 허용 필드 문맥 구성
  → 실행 계획·입력 스냅샷·코드/환경 해시·토큰 사전 검사
  → A/B/C별 독립 Qwen 호출
  → 실행 원장 및 원문 답변
  → 검토 대기 양식 + 별도 조건 대응표
  → 별도 formal case·사람 검토 제공 시 B/C 집계
```

이 경로의 현재 입력은 5개 개발 초안이다. 모두 `fixture-student-001`이라는 같은 family에 속하므로 독립 표본은 5명이 아니라 **1 family**다.

| 개발 사례 | 노이즈 종류 | 후보 수 | C 적격 근거 수 |
|---|---|---:|---:|
| dev-backend-001 | 없음 | 1 | 1 |
| dev-backend-002 | 위조 서명 | 2 | 1 |
| dev-backend-003 | 폐기 | 2 | 1 |
| dev-backend-004 | 만료 | 2 | 1 |
| dev-backend-005 | 비신뢰 발급자 | 2 | 1 |

AI 실행기는 기존 검증 결과의 checks를 정책에 따라 판단한다. 실행할 때마다 암호학적 서명을 다시 검증하는 것은 아니다. 로컬 VC에는 signed fixture와 검증 코드가 존재하지만, 본 실험에 사용할 원본·검증기·정책·기준시각·승인 기록을 하나의 확정된 증빙 묶음으로 연결하는 작업은 별도로 남아 있다.

## 4. 이번에 추가한 기능과 파일

저장소 기준: `C:/Users/bje10/VC_AI_Career_agent`

### 4.1 최신 추가 파일 7개 — 아직 미커밋

| 파일 | 역할 |
|---|---|
| `ai/scripts/run_paper_experiment.py` | `prepare`, `run`, `inspect`, `aggregate` 명령 제공 |
| `ai/src/rag/paper_experiment.py` | 계획·해시 검사, 문맥 사전 검사, Qwen 순차 실행, 실패 중단, 결과 보존, formal case 연결 검증 |
| `ai/src/rag/paper_experiment_review.py` | 검토 대기 양식, 조건 대응표 분리, 승인 검토 증빙 확인, B/C 대응 집계 |
| `ai/tests/test_paper_experiment.py` | 입력·계획 변조, 후보 정책, 실패·누락, 결과 파일 연결, CLI 흐름 검증 |
| `ai/tests/test_paper_experiment_review.py` | 검토 원본 해시, 승인 요건, 대응쌍·family 집계, 미정의 지표 검증 |
| `ai/data/paper_experiment_v2/development_plan.example.json` | 5개 초안 × A/B/C × seed 42 = 15건의 명시적 계획 예제 |
| `ai/docs/PAPER_DEVELOPMENT_RUNNER.md` | 실행·검토·집계 방법과 현재 제한 설명 |

### 4.2 실행 관리에서 보강한 부분

- 입력 초안 내용을 당시 스냅샷과 바이트 해시로 저장한다.
- 모델 revision, 프롬프트, 주요 코드 및 패키지 환경을 연결한다.
- 모든 조건을 모델 로드 전에 검사한다. 입력 2048토큰 초과 시 자동 절단 없이 중단한다.
- 출력 예산은 1~256토큰이며 실제 점검은 조건별 128토큰이었다.
- 모델은 한 번 로드하고 조건마다 독립 대화로 호출한다.
- 실행 순서는 order seed로 재현 가능하게 정하며 run ID와 순서를 원장에 보존한다.
- 실패 시 자동 재시도하지 않는다. 완료 답변과 실패·미실행 기록을 유지한다.
- 기존 실행 디렉터리 덮어쓰기를 거부한다.
- 결과 파일의 바이트 해시와 평가용 canonical JSON 해시를 구분해 검사한다.
- 근거 없는 성공 상태나 사람 승인 상태를 생성하지 않는다.

여기서 lock은 **실행 설정 고정**이다. 정식 연구 설계 승인이나 사람 서명을 갖춘 freeze는 아니다. 현재 실행기는 개발용 `dev` 초안만 허용하고 본 실험 `test` split은 거부한다.

### 4.3 평가 코드에서 가능한 부분

- 계획한 전체 실행을 분모로 성공·실패·누락·출력 길이 제한·미검토 수를 집계한다.
- 답변과 검토를 해시로 연결하며, 승인 검토는 서로 다른 두 사람의 ID와 원검토 JSON 바이트·해시·결과 연결을 확인한다.
- 사실 오류, 문맥 비지지, 근거 적격성, 인용, 커버리지와 모름·보류 항목을 집계한다.
- 승인된 B/C 완전쌍의 차이 `B−C`를 계산한다. family 내부에서 평균한 뒤 family별 동일 가중치로 집계한다.
- family bootstrap 신뢰구간을 계산하되 완전 family가 2개 미만이면 `null`을 반환한다.
- 사실 주장이나 판정 가능한 항목이 없으면 오류율을 임의로 0으로 만들지 않는다.

집계 코드는 단위 테스트로 검증했다. 실제 사람 검토가 완료된 데이터로 효과를 집계한 상태는 아니다. 해시는 파일 연결을 확인할 뿐 사람의 신원이나 독립 정답의 진실성을 인증하지 않는다. 검토 패킷에서 조건명은 분리하지만 근거 내용·길이로 조건을 추측할 수 있어 완전한 블라인드를 보장하지 않는다.

### 4.4 이전에 연결되어 현재 커밋에 포함된 주요 파일

현재 HEAD `8552335`에는 BE SQL 어댑터, 최소 실행기, Qwen 메시지 인터페이스, Work 2B 개발 초안과 준비·검사 스크립트, Work 2C 정책·문맥, 관련 테스트와 안내 문서가 포함돼 있다.

주요 경로는 `ai/src/data/backend_seed.py`, `ai/scripts/run_minimal_pipeline.py`, `ai/src/llm/transformers_llm.py`, `ai/src/rag/paper_vc_adapter_v2.py`, `ai/src/rag/paper_context_renderer_v2.py`, `ai/docs/MINIMAL_PIPELINE.md`다.

이 커밋에 포함된 기존 Work 산출물의 보존과 이번에 새로 작성한 실행 관리 코드는 구분한다. 기존 산출물 전체를 이번 턴에서 새로 작성한 것은 아니다.

## 5. 검증 결과

### 5.1 관련 단위 테스트

직전 구현 단계에서 실행한 명령:

```powershell
python -m pytest tests/test_paper_experiment.py tests/test_paper_experiment_review.py tests/test_paper_evaluation_v2.py tests/test_paper_context_renderer_v2.py -q -p no:cacheprovider
```

- 결과: **84 passed, 1 warning, 1.60초**.
- 테스트 실패: 없음. 실패 후 수정 반복도 없었다.
- 전체 테스트는 실행하지 않았다.
- 모델 대역을 사용한 테스트이므로 실제 생성 확인은 아래 별도 실행으로 구분한다.
- 경고 1건은 기존 renderer 테스트에서 `pytest.parametrize`에 `zip`을 전달하는 방식의 향후 폐기 경고다. 현재 실패가 아니며 이번 범위에서 수정하지 않았다.

이 보고서에서는 해당 실행 기록을 인용했고 불필요한 테스트 재실행은 하지 않았다.

### 5.2 실제 로컬 tokenizer 사전 검사

5개 사례 × 3조건, 총 **15개 입력이 모두 통과**했다. 생성은 하지 않았다.

| 사례 | A 입력 토큰 | B 입력 토큰 | C 입력 토큰 |
|---|---:|---:|---:|
| 001 | 471 | 661 | 661 |
| 002 | 471 | 810 | 661 |
| 003 | 471 | 806 | 661 |
| 004 | 471 | 809 | 661 |
| 005 | 471 | 815 | 661 |

최대 815토큰으로 현재 입력 한도 2048토큰 이내다. 이 결과는 전체 15건의 Qwen 추론 완료를 의미하지 않는다.

### 5.3 실제 Qwen 짧은 실행

대상: `dev-backend-002` 위조 서명 개발 사례 하나의 A/B/C.

| 항목 | 값 |
|---|---|
| 모델 | Qwen/Qwen2.5-1.5B-Instruct |
| revision | `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` |
| GPU | NVIDIA TITAN Xp |
| 설정 | 로컬 캐시, CUDA, FP32, eager, greedy, seed 42 |
| 출력 예산 | 조건별 최대 128토큰 |
| 실제 실행 순서 | B → C → A |
| 결과 | 3건 성공, 0건 실패, 0건 누락 |
| 모델 로드 | 약 2.56초 |
| 각 생성 시간 | 약 3.44~3.46초 |
| 원장 기준 전체 시간 | 약 13.76초 |

| 조건 | 근거 수 | 입력 토큰 | 출력 토큰 | 종료 |
|---|---:|---:|---:|---|
| A | 0 | 471 | 128 | `length`: 출력 한도 도달 |
| B | 2 | 810 | 113 | `eos`: 정상 종료 토큰 |
| C | 1 | 661 | 122 | `eos`: 정상 종료 토큰 |

A의 길이 제한 답변은 수정하거나 버리지 않고 원문 그대로 저장했다. 따라서 “3건 성공”은 생성 및 저장 흐름의 성공이며 3건 모두 완결된 고품질 답변이라는 뜻은 아니다.

실행 중 Transformers의 sliding-window/eager 관련 경고가 출력됐지만, 저장된 실제 runtime 설정은 `use_sliding_window=false`이고 생성은 성공했다. 이번 점검은 해당 라이브러리 경고 자체를 해결하는 작업은 포함하지 않았다.

출력 폴더:

`C:/Users/bje10/VC_AI_Career_agent/ai/outputs/paper_development/20261011-a57ad04c/`

| 산출물 | 내용 |
|---|---|
| `all-five.lock.json` | 5개 사례, 15개 조건의 사전 검사·실행 설정 |
| `smoke.plan.json`, `smoke.lock.json` | 짧은 3건 점검의 계획과 설정 |
| `smoke-run/ledger.json` | 예정 실행, 시도, 상태, 결과 해시 |
| `smoke-run/runtime.json` | 실제 모델·GPU·생성 설정 |
| `smoke-run/results/*.json` | 조건별 원문 답변·근거 ID·토큰 수 |
| `smoke-run/generation/*.json` | 실제 전달 메시지·프롬프트·생성 메타데이터 |
| `smoke-run/review/packets.json` | 사람이 읽을 검토 패킷 |
| `smoke-run/review/reviews_pending.json` | 3개 결과에 대한 미승인 검토 양식 |
| `smoke-run/private/adjudication_key.json` | 결과와 조건을 연결하는 별도 대응표 |

직전 구현 단계의 `inspect`에서도 3건 성공·미검토 3건·의미 평가 지표 없음(`null`)을 확인했다. `paper_ready=false`다.

## 6. Git 상태와 파일 보존

보고서 작성 시 다시 확인한 Git 상태:

- 저장소: `C:/Users/bje10/VC_AI_Career_agent`
- 현재 브랜치: `AI`
- HEAD: `85523350d8cbcef71d3ee9ffd1fafb2b0b2b4a31`
- 제목: `feat(ai): connect BE seed data and fixture contexts to Qwen`
- 추적 중인 기존 파일의 미커밋 수정은 표시되지 않는다.
- 최신 추가 파일 7개와 기존 `ai.zip`이 미추적 상태다.
- 이번 실행 관리 코드 7개는 **아직 commit/push하지 않았다**.
- `AI...origin/AI`에 차이 표시는 없지만 이는 로컬 remote-tracking 정보다. 보고서 작성 중 fetch나 원격 상태 조회를 새로 하지 않았다.

| 분류 | 대상 | 처리 |
|---|---|---|
| 유지 | 기존 BE 어댑터, VC 정책, 문맥 renderer, Qwen, 관련 테스트 | 재사용 |
| 유지 | 신규 실행 관리·검토·집계 도구 | 추가, 미커밋 |
| 현재 위치에 보관 후보 | 기존 Stage1 실행기, 상태 기반 C 경로, 과거 BE 진단 실행기, 교육 v1/v2, 이전 준비 스크립트·문서 | 삭제·이동하지 않음 |
| 보존 필수 | Work 2A·2B·2C, outputs, dev_drafts, ai.zip, BE.zip, 기존 미추적 파일 | 보존 |
| 삭제 후보만 기록 | `__pycache__`, `.pytest_cache` 등 캐시 | 이번 작업에서 삭제하지 않음 |
| 비캐시 삭제 후보 | 없음 | 임의 삭제 제안하지 않음 |

이 보고서는 읽기 전용 `sources/`나 저장소 코드에 덮어쓰지 않고 별도 `deliverables/`에 작성했다.

## 7. 아직 남은 작업과 현실적인 다음 순서

### 7.1 코드 작업과 연구 준비를 구분한 현황

| 남은 항목 | 현재 부족한 것 | 필요한 조치 |
|---|---|---|
| 답변 검토 | 생성 답변 3건의 의미 판정 미완료 | 사용자가 pending 양식으로 사실 주장·인용·보류 여부 검토 시작 |
| 독립 정답 | 현재 초안의 독립 reference facts가 없음 | VC에 쓰인 주장과 독립적으로 확인한 사실·근거·필수 사실 목록 마련 |
| 독립 표본 | 5개 case가 같은 1 family | 별도 인물/독립 사례 family 확충, dev/test 분리 |
| 연구 설계 | 전체 노이즈 조합·표본수·주 지표·검토 방식 확정 필요 | 제출 범위에 맞춘 최소 프로토콜 확정 |
| 검토 인력 | 현재 혼자, 기존 평가 계약은 서로 다른 2인 요구 | 두 번째 검토자 확보 또는 단독 평가 연구 설계를 명시적으로 개정 |
| 본 실험 실행 | 현재 runner는 dev만 허용 | 확정된 본 실험 데이터·승인 계약에 맞춰 test 실행 경로와 freeze 검증을 후속 구현 |
| 검증 증빙 | 과거 export와 원본/검증기/정책/시점의 확정 연결 부족 | 로컬에 있는 fixture와 검증 자료를 선택·고정하고 해시로 연결 |
| 운영 BE | status 외의 상세 checks 전달 부족 | BE 변경 승인 후 API 계약·상세 증빙 전달 구현 |

단독 검토로 진행한다는 사용자 상황은 반영해 미승인 양식으로 검토를 시작할 수 있게 했다. 다만 기존 2인 검토 계약을 몰래 낮추지는 않았다. 본 실험까지 혼자 진행하려면 방법론과 코드 계약을 함께 바꾸고, 단독 평가의 한계를 논문에 명시하는 별도 결정이 필요하다.

### 7.2 선택지 비교

아래 소요 범위는 견적이며, 정답 자료와 사례 확보 여부에 따라 달라진다.

| 선택지 | 구현 부담·소요 | 기술/연구 위험 | 기존 구조 호환성 | 판단 |
|---|---|---|---|---|
| 현재 5개 개발 사례로 검토·실행 절차 완성 | 코드 추가 부담 낮음, 검토는 당일 착수 가능 | 1 family이므로 일반적 효과 주장 불가 | 높음 | 당장 수행할 단계 |
| 독립 정답·추가 family를 갖춘 축소 본 실험 | 데이터/검토 부담 중간~높음; 확정 일정은 자료 확보 후 산정 | 표본 규모와 평가 설계에 따라 해석 범위 제한 | 높음, test/freeze 연결 추가 필요 | 논문 제출을 위한 권장 경로 |
| 운영 BE·VC·DX Ledger까지 동시 완성 | API·외부 시스템 의존으로 부담 높고 일정 불확실 | 외부 계약·환경·검증 증빙 위험 | 일부 추가 계약 필요 | 제출 전 최우선 작업으로는 비권장 |

**권장 순서는 현재 답변 검토 → 최소 연구 설계 확정 → 독립 정답·family 확충 → 본 실험 test/freeze 연결 → 제한된 본 실험 실행이다.** 모델 크기 변경이나 추가 리팩터링보다 현재는 데이터와 평가 계약을 확정하는 일이 우선이다. 현재 Qwen의 연결 성공만으로 모델 성능이 충분하다고 판정하지 않는다.

### 7.3 다음 구현 담당자에게 전달할 지시사항

1. 실제 저장소의 AI 브랜치와 미커밋 신규 7개 파일을 기준으로 시작한다. 이전 ZIP으로 덮어쓰지 않는다.
2. `PAPER_DEVELOPMENT_RUNNER.md`와 이번 실행 원장을 읽고 기존 renderer·policy·Qwen을 그대로 재사용한다.
3. 정식 본 실험 범위, 독립 정답 형식, family/dev/test 분리, 단독/2인 평가 방식을 먼저 확정한다. 확인되지 않은 승인을 생성하지 않는다.
4. 합의된 계약에 한해서 test split 실행과 연구 freeze 승인 검증을 추가한다. 단순히 dev 제한 조건을 제거하는 방식으로 본 실험 준비 완료를 선언하지 않는다.
5. BE 데모의 `verified`를 서명 성공으로 승격하지 않는다. signed-fixture 결과와 BE 데모를 구분한다.
6. 변경에 직접 관련된 단위 테스트만 실행하고, 실패 후 수정은 최대 2회로 제한한다. 장시간 Qwen 실험은 별도 범위 합의 없이 실행하지 않는다.
7. 기존 outputs·초안·문서·테스트·미추적 파일을 보존한다. 새 결과 폴더를 사용한다.
8. 최신 추가 파일의 변경 내용을 검토한 후 Git 반영 범위를 결정한다. push·merge는 사용자 사전 승인 없이 수행하지 않는다.

## 8. 원본 확인 경로

- 저장소: `C:/Users/bje10/VC_AI_Career_agent`
- BE worktree: `C:/Users/bje10/VC_AI_backend`
- BE 최소 연결 안내: `C:/Users/bje10/VC_AI_Career_agent/ai/docs/MINIMAL_PIPELINE.md`
- 개발 실험 사용 안내: `C:/Users/bje10/VC_AI_Career_agent/ai/docs/PAPER_DEVELOPMENT_RUNNER.md`
- 논문 평가 프로토콜: `C:/Users/bje10/VC_AI_Career_agent/ai/docs/paper_experiment_protocol_v2.md`
- 개발 초안: `C:/Users/bje10/VC_AI_Career_agent/ai/data/paper_experiment_v2/dev_drafts/`
- 최신 실행 원장: `C:/Users/bje10/VC_AI_Career_agent/ai/outputs/paper_development/20261011-a57ad04c/smoke-run/ledger.json`
- 이번 보고서: `C:/Users/bje10/.codex/.chatgpt-projects/g-p-6abbcc05c98481918cde46f03cf34db0/deliverables/VC_AI_PROGRESS_REPORT_20261011.md`

이 보고서는 로컬 자료와 실제 실행 기록을 기준으로 구현 상태를 설명한다. 답변 품질·논문 효과·실운영 인증 적합성은 확인 범위를 넘어 확정하지 않았다.

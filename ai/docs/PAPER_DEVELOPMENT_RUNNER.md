# A/B/C 개발 실험 실행 도구

기존 Work 2B 초안 → Work 2C 문맥 → 고정 revision Qwen → 결과/실행 원장 → 사람 검토 대기 → B/C 대응 집계를 연결한다. BE 입력은 기존 `scripts/run_minimal_pipeline.py`를 사용한다. BE의 단순 `verified`는 서명 성공 증거가 아니며, 이 실행기는 암호학적 재검증을 하지 않는다.

현재 초안 5개는 **1 family의 개발 사례**다. 독립 정답과 사람 승인 자료가 없으므로 `paper_ready=false`를 유지한다. 실행 설정을 고정하는 lock과 본 실험 승인 freeze는 구분한다. 본 실험 test split은 이 개발 실행기에서 거부한다.

## 실행 (ai 폴더 기준)

```powershell
python scripts/run_paper_experiment.py prepare --plan data/paper_experiment_v2/development_plan.example.json --output outputs/paper_development/lock-001.json
python scripts/run_paper_experiment.py run --lock outputs/paper_development/lock-001.json --output outputs/paper_development/run-001 --max-runs 15
python scripts/run_paper_experiment.py inspect --run outputs/paper_development/run-001
```

- `prepare`: 명시된 초안만 읽는다. 실제 로컬 tokenizer로 모든 A/B/C 입력을 검사하며 모델을 로드하지 않는다. 입력 2048, 출력 1~256 tokens. 넘치는 입력은 자르지 않고 거부한다.
- 계획의 초안 경로는 **계획 JSON의 위치 기준**이다. 짧은 점검은 초안 하나와 seed 하나(3조건)로 별도 계획을 만든다. 예제 15건 전체 추론은 자동 실행하지 않는다.
- `run`: 모델을 한 번 로드하고 독립 대화로 순차 실행한다. 기존 디렉터리 덮어쓰기와 자동 재시도는 없다. 실패하면 중단하고 성공·실패·남은 누락을 모두 보존한다. 출력 `length`는 실패로 바꾸거나 답변을 수정하지 않는다.
- 파일/프롬프트/모델 revision/환경이 바뀌면 새 lock을 만든다. 원본 파일 내용은 lock 내부에 당시 바이트 해시와 함께 보존한다. 해시는 변경 감지용이며 출처의 진실성이나 사람 승인을 증명하지 않는다.
- greedy decoding이므로 다른 seed 반복은 독립 표본 추가가 아니다. 통계 단위는 family다.

## 혼자 시작하는 검토

`review/packets.json`의 답변과 근거를 읽고 `review/reviews_pending.json`에 주장별 원문 span과 판정을 기록한다. 처음에는 빈 양식이며 자동 정답/승인을 만들지 않는다. `private/adjudication_key.json`은 조건 대응표이므로 첫 검토 패킷과 별도로 취급한다. 근거 내용·길이로 조건을 유추할 수 있어 완전한 블라인드를 보장하지 않는다.

혼자 검토하는 동안에는 `pending` 상태를 유지한다. 기존 v2 계약의 확정 지표에는 독립 정답을 갖춘 formal case와 서로 다른 두 검토자의 승인·원검토가 필요하다. 그 요건을 한 사람 또는 AI로 대체하지 않는다. 원검토 파일은 UTF-8 JSON이며 최소 `reviewer_id`와 `result_sha256`을 포함하고 실제 판단 내용도 보존한다. 최종 review의 각 `original_review_sha256`은 이 파일의 **바이트** SHA256, `result_sha256`은 기존 evaluator의 **canonical JSON** digest다. 파일의 존재·해시·연결은 검사하지만 실제 사람 신원과 정답의 진실성은 인증하지 않는다.

## formal case가 준비된 이후의 집계

```powershell
python scripts/run_paper_experiment.py aggregate --run outputs/paper_development/run-001 --cases cases.json --reviews reviews.json --original-review reviewer1.json --original-review reviewer2.json --output outputs/paper_development/report-001
```

`cases.json`은 `paper-case/2.0` 객체 배열, `reviews.json`은 run_id→`paper-review/2.0` 매핑이다. 원검토 파일은 필요한 만큼 옵션을 반복한다. 임의 formal case를 만들어 승인하지 말고 실제 독립 사실·검증 증빙을 먼저 확보한다.

실행된 모든 case가 있어야 하며 profile/JD/후보/적격성/노이즈/원기록/검증 checks를 대조한다. 각 formal record의 `text`는 해당 기록의 렌더된 evidence 객체를 `json.dumps(record, ensure_ascii=False, sort_keys=True)`로 직렬화한 내용이다. 검토 중 보지 않은 문맥이 평가에 섞이는 것을 막기 위한 계약이다.

집계는 예정 전체의 성공·실패·누락·길이 제한·미검토 수를 보고한다. 승인된 B/C 완전쌍의 차이(B−C)를 family 안에서 평균한 후 family별 동등 가중치로 집계한다. family가 2개 미만이면 신뢰구간은 `null`이다. 주장 없는 오류율도 0이 아니라 `null`이며 커버리지·모름·답변 보류를 함께 표시한다. 이는 기술적 집계이며 정식 논문 승인이나 효과 입증은 아니다.

기존 outputs/dev_drafts/Work 2A·2B·2C 및 미추적 파일은 그대로 보존한다. 입력 준비·검토·독립 표본 확충은 남은 연구 작업이다.

## 새 study 2.1 경로

`docs/paper_experiment_protocol_v2_1.md`의 명시적 단독 평가 계약을 추가했다. 기존 v2/개발 실행은 그대로다. 새 계획은 `paper-study-plan/2.1`이며 dev/test를 명시한다. test는 실제 사용자 gold 검토와 protocol declaration 없이는 모델을 로드하지 않는다.

```powershell
python scripts/prepare_paper_study.py seed --output outputs/새폴더/world.pending.json
```

VC 폴더에서 기존 모듈을 재사용하는 새 격리 생성기를 실행한다. 아래의 경로는 실제 새 경로로 지정한다. 기존 generate:fixtures/setup:fixtures는 실행하지 않는다.

```powershell
node node_modules/tsx/dist/cli.mjs scripts/prepare-paper-study.ts ../ai/outputs/새폴더/world.pending.json ../ai/outputs/새폴더/signed
```

다시 ai 폴더에서:

```powershell
python scripts/prepare_paper_study.py assemble --world outputs/새폴더/world.pending.json --verification outputs/새폴더/signed/verification_bundle.json --output outputs/새폴더/prepared
python scripts/run_paper_experiment.py prepare --plan outputs/새폴더/prepared/test.plan.json --output outputs/새폴더/prepared/test.pending.lock.json
```

prepare는 Qwen 추론 없이 전체 입력을 검사하고 준비 차단 이유와 scope hash를 출력한다. `GOLD_REVIEW_PENDING.md`는 사람이 읽을 기준 사실 목록이며 승인된 자료가 아니다. 승인 파일 작성은 protocol2.1 문서의 계약을 따른다. 실제 본 실험 실행은 사용자 지시와 준비 요건 충족 후 별도로 한다.

새 study 집계에서는 동결 case를 사용하므로 `--cases`를 생략할 수 있다. `--reviews`와 `--original-review`는 기존처럼 제공한다. 재평가는 `--repeat-reviews`로 별도 결과를 넣고 두 시점의 원검토 파일을 모두 전달한다. 이전 사람 판정이나 실험 결과를 자동으로 수정하지 않는다.

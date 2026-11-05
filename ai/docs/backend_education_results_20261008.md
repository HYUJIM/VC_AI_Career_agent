# BE 교육 구조화·평가 결과 — 2026-10-08

## 결론

BE 계약 조사, 원문 추적 구조화, 고정 개발/보류 사례, 선별·생성 분리 평가를 구현하고 실행했다. 전체 소프트웨어 테스트 107개가 통과했다. 고정 합성 보류 사례의 자동 선별/필드 구조화는 기대 결과와 일치했지만 Qwen 생성 품질은 승격 기준에 미달했다. 원본 기반 표시를 유지하고 BE 데이터 계약 보완 및 모델 역할 축소 검토로 분기한다.

실제 기관 자료에 대한 일반화, 독립 사람 정답 검토, 실제 VC 검증, 논문 A/B/C 평가는 완료하지 않았다. 같은 에이전트가 규칙과 합성 사례를 작성했으므로 보류 평가의 독립성은 제한적이다. 기존에 관찰한 실제 BE 12건은 개발/회귀 자료이며 새 보류 평가로 계산하지 않았다.

## 기준 및 변경 범위

- AI 기준: AI 브랜치 6074b328aaaad6dde3da54a23933e9fe57fe1d5d. 기존 추적 코드 수정 없음. 신규 모듈/스크립트/테스트/문서/사례만 추가.
- BE 조사 기준: e0ad118f38a192a253a8fa20b90a77b93909d5c4 detached HEAD. 수정 없음. 운영 서버 재조회 없음.
- 원본 export SHA256: afec5ec84e5dd8e5e3700fe1a79fe26d25592acc3c00d7f55e953d21f6792202.
- 기존 ai.zip 및 과거 outputs/backend_qwen 4건 보존. 기존 3건 진단 및 review/v2 결과를 덮어쓰지 않음.
- 이번 작업의 Git 반영 범위는 AI 브랜치의 신규 코드·테스트·고정 평가 사례·문서 및 고정 파일의 줄바꿈 보존 설정 9개 파일이다. outputs 실행 결과와 기존 ai.zip은 커밋 대상에서 제외해 로컬에 보존한다. main 변경, PR 생성, 병합은 수행하지 않는다.

## 계약 조사

BE openapi.yaml Achievement.achievement_type은 선택적 자유 문자열이다. 교육/자격/수상 enum과 기간/이수량 전용 필드가 정의되지 않았다. DB raw_data JSONB는 임의 원문을 보존할 수 있지만 의미가 보장된 필드는 아니다. careers 응답은 DB 원문을 반환하고 verified_credentials 배열을 status 조건 없이 구성한다.
실제 저장 응답 12건의 achievement에는 name/description만 있다. 과정 기간 6개월은 설명문 1건에만 존재하며 시간 단위 이수량은 없다. 자료는 mock_seeder 시드 출처다. 어댑터는 achievement_type을 보존할 수 있으므로 현재 유형 누락은 변환 과정에서 생긴 것이 아니다. 새 확장 필드의 의미와 전파는 별도 계약 보완이 필요하다.

## 구현

| 파일 | 역할 |
|---|---|
| src/rag/backend_education.py | BE 입력 검증, 제한된 교육 수료 선별, 과정 기간/이수 시간 분리, 값별 원문 위치·인용, 원본 표시 |
| src/rag/backend_education_evaluation.py | 사실/표현/미확인/완전성의 독립 평가 |
| scripts/evaluate_backend_education.py | 사전 해시 검사, 개발/보류 분리 실행, oracle/automatic 분리, 토큰 초과 차단, 원답변·원본 표시·검토 별도 저장 |
| tests/test_backend_education.py | 개발 사례 및 경계/원문 보존/정답 변경 차단 테스트 27개 |
| data/backend_education_v1/cases.json | 개발 8개, 합성 보류 10개 및 답변 검사 probe 8개 |
| data/backend_education_v1/lock.json | 구현·모델 실행 전에 고정한 사례/계약 SHA256 |
| docs/backend_education_contract.md | 범위, 결측·보류·오류 분류, 평가·분기 정책 |

기간은 시간으로 환산하지 않는다. 0시간은 명시값일 때만 보존한다. 상태는 provided/not_provided/ambiguous/conflicting/unassessable이다. 완료 상태와 사실 검증 상태는 별개이며 backend_status로 교육 여부나 VC 검증 여부를 추론하지 않는다.

## 실행 결과

기준 회귀 테스트: 80 passed in 5.50s. 신규 테스트: 27 passed in 0.28s. 최종 전체: 107 passed in 5.63s. 실제 Qwen 호출은 pytest 수치에 포함되지 않는다.

개발 및 실제 원문 구조화 run: `de381113-9f6f-4df6-b5c6-8a66dd0dc165`.
개발 8개 선별 상태·필드 기대값 일치. 추론 0회. 실제 12건은 selected 3, rejected 7, review 2. review는 교과 성적과 코딩테스트 역량 기록이다. 세 교육명과 ID를 원본대로 보존하고, 백엔드 개발자 부트캠프만 과정 기간 6개월, 세 건의 이수 시간은 모두 미제공이다. 실제 12건에 대해 독립 gold 정답표에 따른 정확도를 계산하지 않았다.

보류 Qwen run: `428bbcda-c5f9-4fd5-a284-0e90e4865880`.
입력 유효 사례 8개/10개 기록. 선별 TP8/FP0/FN0, 교육 선택 precision/recall 각각 1.0, 보류1/제외1, 3상태 판정 10/10 일치. 두 수치 필드의 status/value/unit은 10개 기록 모두 기대값과 일치. 이는 사전 작성한 제한된 합성 사례에서의 결과다.
사용자 혼입 1개와 UUID 중복 1개는 전체 입력을 차단했다. 긴 입력은 양 경로 모두 실제 tokenizer 기준 16,587토큰, 상한 2,048 초과로 추론하지 않았다. 잘림/요약/모델 변경 없음.

| 보류 사례 | 자동 선별/필드 | oracle 답변 | automatic 답변 |
|---|---|---|---|
| 교육·수상·교과성적 혼합 | 기대 일치 | needs_review | needs_review |
| 4개월·72시간 동시 명시 | 기대 일치 | passed | passed |
| 소수 시간 12.5시간 | 기대 일치 | failed | failed |
| 주당 8시간 | 기대 일치 | failed | failed |
| 2개월/5개월 상충 | 기대 일치 | failed | failed |
| 한글 수사 | 기대 일치 | needs_review | needs_review |
| 다른 사용자 | 입력 차단 | 미실행 | 미실행 |
| 중복 ID | 입력 차단 | 미실행 | 미실행 |
| 긴 원문 | 기대 일치 | input_overflow | input_overflow |
| 90분 이수 | 기대 일치 | failed | failed |

각 경로 실제 생성 7회: passed1/needs_review2/failed4. 총 14회 모두 EOS. 두 경로에서 ID/입력과 출력이 일치했으므로 14개 독립 표본이 아니다. 생성 시간 합계 oracle 15.271초, automatic 15.076초이며 모델 로드·전처리 시간은 포함하지 않는다.
각 경로의 기계적 오류 표시는 facts6, wording8, unknown10, completeness0. 이는 독립 오류율이나 확정 환각 수가 아니다. 한 답변에 여러 표시가 붙으며 프롬프트를 반복한 줄의 ID 오류도 포함된다.

모델: Qwen/Qwen2.5-1.5B-Instruct, revision 989aa7980e4cf806f80c7fef2b1adb7bc71aa306. torch2.5.1+cu118, transformers4.51.3, TITAN Xp, FP32/eager, greedy, seed42, 출력256. 모델/환경 변경 없음. eager 경고가 있었으나 runtime use_sliding_window=false이며 실제 추론이 완료됐다.
저장된 보류 run의 모든 manifest artifact SHA256을 재계산해 불일치 0개를 확인했다.

## 오류 해석과 한계

- 결측 사례: 모델이 발급일을 기간 칸에, 단위 없는 0을 시간 칸에 출력했다. v1 검토기는 이 표현을 unknown으로 보류하며 사실 통과로 승인하지 않는다. 사람의 의미 검토에서는 부적절한 필드 사용을 지적할 수 있지만 기계 facts 집계에는 사후 추가하지 않았다.
- 소수 시간/주당 시간: 모델이 시간을 과정 기간 칸에도 넣었다. 상충 사례에는 원문 없는 별도 행과 null ID가 생겼다. EOS만으로 품질을 보장할 수 없다.
- 한글 수사 사례의 6달/30시간은 원문과 의미가 맞을 수 있지만 v1 지원 범위 밖이다. failed 환각으로 집계하지 않고 needs_review로 남겼다. 90분 이수 역시 원문 근거는 있으나 hours 필드로 정규화하지 않은 지원 외 표현이다.
- 이름 불일치는 표현 오류로 분리한다. 실제로 다른 교육명을 붙인 것인지에 대한 의미 판정까지 일반화하지 않는다.
- 문자열 규칙은 복합 문장·부정 범위·경력과 과정 기간의 관계·여러 단위 환산·불명확한 유형을 완전하게 해석하지 못한다. Completion만으로 교육임을 확정하지 않는다. 구조화 결과의 자동 운영 사용에는 추가 검증이 필요하다.
- 평가 기대 결과는 에이전트 작성이며 독립적인 사람의 확인이 없다. 실제 BE 보류 자료, 다양한 발급기관/다른 사용자 자료가 추가로 필요하다. 모델 출력 이후 기준·규칙·gold를 변경하지 않았다.
- 이번 자료에서는 선별이 모두 일치했으므로 oracle/automatic 차이로 선별 오류의 생성 영향이나 개선 인과를 추정할 수 없다.

## 다음 분기

1. BE 계약 보완: 유형 enum/의미, 수료 여부, 과정 기간 또는 실제 수강일, 실제 이수량과 단위, 계획량 분리, 결측 상태, 원문 출처를 확정한다. 요청안은 계약 문서에 있으며 팀에 전송하지 않았다.
2. 그 전까지 제품 교육명·ID·기간·이수량은 원문 기반으로 표시하고 보류 항목을 수동 검토한다. 현재 Qwen 원답변을 자동 확정 표시로 승격하지 않는다.
3. 새로운 실제 보류 자료와 사람 검토를 확보한다. 이번 보류 결과를 사용해 의미 규칙을 수정하면 이번 사례를 개발로 이동하고 새 보류 세트를 고정한다.
4. 모델의 역할 축소 또는 별도 프롬프트/모델 비교는 새 Work에서 수행한다. 변경 전 비교 계획·메모리·호환성을 명시한다. 논문 조건 C는 별도의 실제 VC 검증 근거와 평가 프로토콜을 요구한다.

## 재현

저장소의 ai 디렉터리에서 vc-ai 환경으로 실행한다. outputs는 Git 제외 대상이며 전달 시 두 run 폴더를 별도로 보존해야 한다.

```powershell
python -m pytest -q -p no:cacheprovider
python scripts/evaluate_backend_education.py --split development --export outputs/backend_exports/630ebd28-d305-4ef8-a898-3e2114071d2f/backend_evidence.json
python scripts/evaluate_backend_education.py --split holdout --qwen
```

재실행마다 새 UUID 폴더를 만든다. --qwen 없이는 모델 추론을 하지 않으며 summary에서 not_run으로 표시한다. hash 고정 파일을 수정하면 runner가 중단한다. 의미가 달라지는 수정은 새 버전/새 평가 세트로 진행한다.

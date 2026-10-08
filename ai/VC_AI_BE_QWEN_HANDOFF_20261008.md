# BE → Qwen 실제 추론 인수인계 (2026-10-08 KST)

## 현재 결론

BE export 12건을 고정 Qwen으로 실제 추론했고 답변/입출력 토큰/시간/GPU allocator 메모리/근거를 저장했다. 연결 검증은 완료했지만 전체 근거에서 교육을 정확히 선별하고 근거에 충실한 완성 답변을 만드는 품질은 확보하지 못했다. 교육 3건을 수동 선택한 진단은 EOS로 완료됐으며 ID가 일치했다. 엄격한 표현 검사는 실패했고, 별도 v2 검토는 needs_review다. 이것은 논문 조건 C나 실제 VC 검증 성공이 아니다.

원래 저장소: C:\Users\bje10\VC_AI_Career_agent, 브랜치 AI, 기준 f86723542c9d77afc2ffa21579d062a332ff0801. 작업 전 미추적 ai.zip 존재. 이번 코드는 작업 복사본에서 작성 후 원래 저장소에 추가 파일로 반영한다. 기존 연구 A/B/C 구현은 변경하지 않았다. commit/push는 이번 요청에서 수행하지 않는다. 다음 Work는 먼저 git status를 확인하고 미커밋 신규 파일을 보존해야 한다.

## 실행 환경 및 장애

vc-ai, torch 2.5.1+cu118, transformers 4.51.3, safetensors 0.5.3, TITAN Xp. 모델 Qwen/Qwen2.5-1.5B-Instruct revision 989aa7980e4cf806f80c7fef2b1adb7bc71aa306, FP32, eager, greedy, seed 42, 로컬 캐시, 입력 상한 2048, 출력 상한 256 유지.

WinError 4551은 torch.dll을 VerifiedAndReputableDesktop 정책이 차단한 것으로 로그에서 확인했다. 사용자 후속 터미널에서 torch import 및 CUDA=True 확인 후 실제 추론 성공. 보안 설정의 최종 상태는 별도 확인하지 않았다. 패키지 재설치/모델 변경은 하지 않았다. eager 경고는 설치된 코드가 sliding_window 값만 검사해 발생하며 실제 runtime_metadata의 use_sliding_window는 false였다.

## 자료와 결과

원본 export: outputs/backend_exports/630ebd28-d305-4ef8-a898-3e2114071d2f/backend_evidence.json. SHA256 afec5ec84e5dd8e5e3700fe1a79fe26d25592acc3c00d7f55e953d21f6792202. 첨부 export/preview ZIP 7개 파일과 작업 복사본이 바이트 단위로 일치했다. 전체 12건의 원래 입력은 1830토큰이며 preview와 재생성 prompt도 일치했다.

아래 폴더는 모두 outputs/backend_qwen/ 아래에 있다.

| run ID | 입력/출력 | 생성 초 | 종료 | 평가 |
|---|---|---:|---|---|
| 111a7ed2-757e-47b6-bbba-22407e85de3b | 1830/128 | 4.855 | length | 두 번째 항목 도중 잘림, 자격증 포함 |
| bafba25c-dc38-4afd-bf04-bda7497dede8 | 1830/256 | 8.325 | length | 네 번째 항목 도중 잘림, 자격증 포함 |
| 56bdef33-9471-4fef-bdfe-efe3f0ed7cbc | 1949/256 | 8.538 | length | 질문 변경. 근거 없는 기간 및 잘못된 ID |
| 2626dc1c-b06f-4031-92d0-a59694e1b613 | 727/149 | 4.329 | eos | 수동 선택 3건. ID 완전성 통과, 표현 미준수 |

마지막 실행 GPU peak allocated/reserved는 6350148096/6517948416 bytes, 전체 시간 10.104초. 이는 PyTorch allocator 통계이며 GPU 전체 사용량은 아니다. 첫 두 실행은 질문 동일, 이후는 질문과 근거 구성이 달라 인과 효과를 분리할 수 없다. 자동 검색 성능을 입증하지 않는다.

마지막 run의 review.json은 원래 엄격 검사 failed. review_v2_3d201990-0c81-4327-953b-0dbaaea6c515.json은 사후 needs_review. 두 기록과 원답변을 모두 보존한다. v2는 기존 manifest의 review_status를 덮어쓰지 않는다.

## 추가 구현

- scripts/run_backend_qwen.py: 가중치 로드 전 토큰 점검, 독립 BE 실행, 원문/템플릿/지표/실패 저장, 교육 진단 옵션.
- src/rag/backend_education_diagnostic.py: 이번 DID와 수동 선정 3건에만 적용되는 fixture. 소스 제목/설명 변화는 중단. 범용 추출기가 아니다.
- src/rag/backend_education_review.py: ID/완전성, 기간 근거, 표현/형식의 세 축. 과정 기간과 시간 단위 이수량 분리. N/A는 모호하므로 needs_review. 정확히 인식하지 못한 문장은 자동으로 사실 판정하지 않는다.
- scripts/review_backend_education.py: 기존 결과 재검토, 입력 해시 확인, 새 UUID 검토 파일 저장, GPU 불필요.
- tests/test_backend_qwen.py, test_backend_education_diagnostic.py, test_backend_education_review.py: 기존 포함 총 80개 테스트 통과(작업 복사본). 원래 저장소 반영 후 테스트는 별도 검증 로그 확인.
- data/backend_demo/question_education_compact_v1.txt: 전체 근거 실험용 질문 변형, 품질 실패 결과도 보존.

## 원래 저장소에서 사용

```bat
conda activate vc-ai
cd /d C:\Users\bje10\VC_AI_Career_agent\ai
python -m pytest -q
python scripts/review_backend_education.py --run outputs/backend_qwen/2626dc1c-b06f-4031-92d0-a59694e1b613 --export outputs/backend_exports/630ebd28-d305-4ef8-a898-3e2114071d2f/backend_evidence.json
```

기존 결과 재검토에는 Docker/BE/Qwen 실행이 필요 없다. 반복 실행마다 새 review_v2 파일을 저장한다. 이번 Work에서는 추가 추론이 필요하지 않다.

## 다음 Work의 범위와 분기점

이 Work 종료 기준: 원래 저장소에 신규 파일과 결과 반영, 원래 위치 테스트 통과, 인수인계/실험 결과 보존. 충족하면 다음 Work로 전환한다. 현재 실패 답변을 억지로 통과시키기 위한 재실행은 하지 않는다.

다음 Work 목표: BE 교육 정보의 구조화 및 여러 사례에 대한 검증 설계. 모델 교체나 A/B/C 재실험부터 시작하지 않는다.

1. BE 계약 확인: 교육/자격/수상 구분, 과정 기간, 시간 단위 이수량 필드를 실제로 제공하는지 조사한다. 미제공은 null+미제공 상태로 표현하고 6개월을 이수 시간으로 환산하지 않는다. 없으면 팀에 필요한 필드를 구체적으로 요청하거나 원문 인용과 수동 검토를 유지한다.
2. 제품의 교육명/ID는 원본에서 표시한다. 모델 원답변, 원본 기반 표시, 사후 판정을 구분해 보존한다. 이번 3건 하드코딩은 진단으로만 유지한다.
3. 개발 사례와 별도의 보류 평가 사례를 마련한다. 교육 아님, 기간 없음, 시간 명시, 상충 정보, 다른 사용자/ID, 긴 입력을 포함하고 사람이 근거와 기대 결과를 먼저 확정한다. 모델 결과를 보고 정답을 바꾸지 않는다.
4. 자동 선별/추출을 구현할 경우 선별과 답변 생성 평가를 분리한다. 검증 불가능한 주장/ID 불일치는 차단 또는 수동 검토로 보낸다. 고정 Qwen에서 동일한 보류 사례로 평가한다.

다음 Work 종료/분기 기준: 데이터 계약과 결측 처리 문서, 원문 추적 가능한 구조화 결과, 사전 정의된 평가 사례 및 결과표, 환각/형식/미확인 상태를 구분한 테스트가 갖춰지면 다음 단계로 이동한다. 필수 원문 정보가 없으면 BE 계약 보완으로 분기한다. 관련 근거를 제공해도 생성 품질이 부족하면 모델이 맡을 역할 축소 또는 별도의 모델/프롬프트 비교 Work로 분기한다. 모델 변경은 호환성/메모리 및 비교 계획을 먼저 정한다.

논문 A/B/C 평가는 실제 VC 검증 증거, 정답 근거, 평가 프로토콜을 준비한 후 별도 Work에서 진행한다. BE verified나 이번 80개 소프트웨어 테스트 통과를 사실성/VC 검증 성공으로 보고하지 않는다.

## 다음 Work 시작 요청

이 문서와 원래 저장소 AI의 미커밋 변경 및 outputs/backend_qwen 결과를 읽어라. 이번 목표는 BE 교육 데이터 계약과 구조화/평가 설계다. 기존 모델/환경/연구 경로와 로컬 결과를 보존하고, 수동 선정 fixture를 범용 검색기로 취급하지 마라. 먼저 데이터 필드와 평가 기준을 확정한 뒤 구현하라. 직전 결과는 연결 성공, 답변 품질은 미확보이며 마지막 3건 진단은 EOS와 ID 완전성 통과, 사후 needs_review다.

## 반영 후 최종 확인
원래 저장소에서 80 passed in 5.69s. 신규 11개 파일과 실행 결과 48개 파일의 SHA256 일치를 확인했다. 기존 추적 파일 변경 없음, ai.zip 보존. commit/push 미수행. 위 종료 기준이 충족되어 다음 Work로 전환 가능하다. outputs 결과는 Git status에 나타나지 않으므로 다음 환경으로 옮길 때 별도 전달한다.


## Git 반영 후속 작업
위 commit/push 미수행 표기는 최초 인수인계 시점의 기록이다. 이후 사용자 요청으로 이번 신규 코드·테스트·문서 11개를 AI 브랜치 커밋 및 push 대상으로 확정했다. 실제 반영 커밋은 git log에서 확인한다. ai.zip과 outputs의 실행 원본은 커밋에서 제외하고 로컬에 보존한다. 저장소만 내려받는 다음 환경에는 BE export와 실행 결과를 별도 전달해야 한다.

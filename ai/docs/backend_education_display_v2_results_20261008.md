# 교육 계약·검토·원문 표시 v2 준비 결과 — 2026-10-08

결론: **준비 구현 완료, BE 계약 합의·신규 실제 자료 검증·사람 판정·제품 연결 대기**.

## 완료 범위

- `docs/backend_education_contract_v2_proposal.md`: 교육 유형/수료/달력 기간/수강일/실제량/계획량/주당량, 상태·근거·원문 보존·호환 전파 제안 및 담당별 pending 표.
- `docs/backend_education_gold_v2_review.md`, `data/backend_education_v2/gold_template.json`: 신규 실제 자료의 빈 양식, 원문·해시·독립성 확인, 사람 2차 검토/쟁점 해결/추론 전 고정 절차. 사람 확정 0건.
- `src/rag/backend_education_display_v2.py`: 기존 export validator 및 v1 구조화 재사용, 원본 이름/ID/원문/해시/인용 보존. 기간·시간·선별·필드 보류·사람 검토 상태 분리. 모든 selected/rejected/review 기록 노출. 모델 답변과 검토 결과는 별도 영역.
- `scripts/run_backend_education_display_v2.py`: 새 UUID 폴더에 원본 export 복사·JSON·독립 HTML·해시 manifest 생성. 추론 없음, 외부 자산/스크립트 없음.
- `tests/test_backend_education_display_v2.py`: 개발 사례 기반 13개 검증. 사용자 혼입/중복 차단, HTML 삽입 방어, 원본·근거·0/결측 보존 및 모델 표시 분리 포함.

표시 버전 v2는 BE 계약 v2 파서가 아니다. 기존 v1 규칙을 바꾸지 않았으며 신규 제안 필드를 생산/검증하는 BE·DB/adapter/export 구현은 합의 대기다. 계약 상태는 proposed_pending, 제품 연결 상태는 standalone_example_not_product_connected로 기록한다. 표시 예제는 기존 export 경로를 소비하지만 운영 서비스 API/UI 연결 완료로 보고하지 않는다.

## 확인한 코드·Git·결과

AI 브랜치와 HEAD는 bfb9fa1a8743dca474b8a4fc1089226e85f4028e. 로컬 origin/AI도 동일하며 원격 네트워크 재조회는 하지 않았다. 시작 시 AI 변경은 미추적 ai.zip뿐. BE e0ad118f38a192a253a8fa20b90a77b93909d5c4 변경 없음. Git 상태는 초기 샌드박스 조회 오류 후 허용된 환경에서 확인했다.

frozen cases SHA256 `30fcb43f7dc80511109341f402042e8445c27f46ba5d871a0e2446bc33c0acc5`, frozen 계약 SHA256 `d9f82c81ea2f7dae8960632d0cc4903358bfabda1c87bc6acb7d40ace33ec67c`가 lock과 일치했다. 기존 export SHA256 `afec5ec84e5dd8e5e3700fe1a79fe26d25592acc3c00d7f55e953d21f6792202`도 일치했다.

기존 구조화 run de381113의 manifest 등록 artifact 40개, 평가 run 428bbcda의 97개를 다시 해시 검사하여 불일치 0개. 과거 Qwen 폴더도 보존했으며 과거 전체 Qwen artifact의 해시를 재감사했다고 주장하지 않는다.

## 실행 검증

환경: 기존 vc-ai Python. 패키지·모델·보안 설정 변경 없음.

```powershell
Set-Location 'C:\Users\bje10\VC_AI_Career_agent\ai'
& 'C:\Users\bje10\anaconda3\envs\vc-ai\python.exe' -B -m pytest -q -p no:cacheprovider
```

첫 실행: 101 passed, 19 setup errors (샌드박스의 pytest 임시 폴더 생성 실패). 동일 명령을 허용된 환경에서 재실행: **120 passed in 5.65s** (기존 107 + 신규 13). 이 수치는 소프트웨어 테스트이며 실제 모델 품질/기관 자료 일반화/사람 gold 정확도가 아니다.

```powershell
& 'C:\Users\bje10\anaconda3\envs\vc-ai\python.exe' -B scripts/run_backend_education_display_v2.py --export outputs/backend_exports/630ebd28-d305-4ef8-a898-3e2114071d2f/backend_evidence.json --output 'C:\Users\bje10\.codex\.chatgpt-projects\g-p-6abbcc05c98481918cde46f03cf34db0\deliverables\be_education_v2\runs'
```

실행 run: `f58daf21-1739-40bb-b619-bf548d193302`. 출력은 위 output 아래 run 폴더의 `index.html`, `source_display.json`, `source_export.json`, `manifest.json`. 출력 artifact 3개가 manifest 해시와 일치했다. 폴더는 프로젝트 작업 공간에 있으며 저장소의 과거 outputs와 분리했다. HTML은 파일로 열 수 있다. 테스트에서 이스케이프·행/영역 존재를 확인했으며 브라우저 시각 검수는 수행하지 않았다.

BE 시드 12건: 교육 수료 후보 3 / 대상 제외 7 / 선별 보류 2. 원본 이름/ID 유지. 백엔드 개발자 부트캠프만 기간 6개월, 후보 3건 모두 이수 시간 미제공. 사람 검토는 모두 pending. 이 자료는 이미 관찰한 회귀 자료이며 새 실제 보류 평가가 아니다. 모델 추론 0회.

## 외부 입력과 다음 분기

| 대기 항목 | 필요한 입력 | 이후 작업 |
|---|---|---|
| BE 계약 | 필드 위치·의미·enum·결측·다중 단위·출처 규칙의 서면 확인과 응답 예제 | 별도 v2 adapter/export 스키마 및 전파 테스트 |
| 새 실제 자료 | 기관 원문/대응 응답, 사용 허용·익명화 조건, 이전 사례와의 관계 | 양식 작성·source_family 분리·사람 검토 |
| 사람 확정 | 작성자·독립 검토자·쟁점 판정자 | 확정 세트와 정책/해시를 추론 전에 고정 |
| 제품 연결 | FE 저장소/브랜치/화면/소비 API 위치 | 기존 화면에 최소 연결·인증/사용자 격리 통합 검증 |

원문 정보가 부족하면 BE/발급기관 보완으로 분기한다. 충분한 원문을 제공해도 생성 오류가 남으면 원문 표시를 유지하고 모델 역할 축소/별도 비교로 분기한다. 합성 통과로 운영 자동 확정을 선언하지 않는다. BE verified와 실제 VC 검증/논문 C는 별개다.

## 보존과 인계

이번 변경은 신규 v2 파일 추가다. frozen v1, 기존 규칙/평가/실행 결과, ai.zip, sources는 수정하지 않았다. 신규 파일은 미커밋 상태이며 commit/push는 수행하지 않았다. BE 담당자 메시지, main 변경, PR 생성·병합도 수행하지 않았다. 사용자가 요청한 독립 준비 작업은 완료했으며 외부 합의나 자료·사람 검토를 완료로 표시하지 않는다.

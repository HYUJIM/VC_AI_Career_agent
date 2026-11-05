# 최소 AI 연결 실행 안내

새 기본 진입점은 `scripts/run_minimal_pipeline.py`입니다. 기존 실행기와 Work 2A·2B·2C 파일은 그대로 보존합니다.

## 연결과 검증 범위

- BE: careers GET 또는 저장된 응답 → 기존 BE 어댑터 → 기존 Work 2C 정책·문맥 → Qwen → 새 UUID 결과 폴더.
- fixture: 기존 Work 2B 초안 → CredentialRecord 형식 검사 → 기존 Work 2C 정책·문맥 → 같은 Qwen → 새 결과 폴더.
- A: 근거 없음. B: 지정 후보 전부. C: 동일 후보에서 정책 적격만 유지하며 재충원하지 않음.
- BE 상태 `verified`는 서명 검증 증거가 아닙니다. 현재 BE 계약에는 상세 checks가 없으므로 C는 빈 근거이며 제외 사유를 저장합니다. BE 데모의 A/C 근거는 동일할 수 있습니다.
- fixture 모드는 보고된 checks를 감사합니다. 암호학적 서명 재검증, 실제 기관 진위 확인, 논문 준비 완료를 의미하지 않습니다. `paper_ready=false`와 `paper_evaluation_eligible=false`를 유지합니다.
- profile과 JD는 별도 사용자 입력입니다. profile에 쓴 경력을 VC 검증 결과로 간주하지 마세요. 검증 대상 경력은 근거 레코드로 제공합니다.
- 모든 A/B/C 문맥에 동일 출력 예산을 적용합니다. 하나라도 2048 입력 토큰을 넘으면 모델 로드 전에 중단하며 자동 절단하지 않습니다.

## 실행

Anaconda의 기존 `vc-ai` 환경에서 실행합니다.

```bat
conda activate vc-ai
cd /d C:\Users\bje10\VC_AI_Career_agent\ai
```

기존 fixture 한 사례의 문맥·로컬 토크나이저 검사(모델 로드 없음):

```bat
python scripts/run_minimal_pipeline.py --fixture-draft data/paper_experiment_v2/dev_drafts/dev-backend-001.draft.json --check-only
```

짧은 실제 Qwen 연결 확인은 `--check-only` 대신 `--condition B_naive_rag --max-new-tokens 16`을 사용합니다. 짧은 답변은 정상적으로 길이 제한에 도달할 수 있으며 품질 평가에 사용하지 않습니다. `--condition`을 생략하면 A/B/C 각각 실행합니다. 모델·토크나이저는 로컬 캐시만 사용하며 새 다운로드나 CPU 우회는 하지 않습니다.

BE 입력에는 별도 JSON 문맥 파일이 필요합니다. 다음 예시는 합성 데모 DID입니다. `context.json`이라는 새 파일로 저장하고 실제 BE 사용 시 해당 사용자의 DID로 바꿉니다.

```json
{
  "subject_did": "did:example:be-demo-001",
  "profile": "제공된 자격증명에 기반해 경력을 정리하려는 지원자",
  "jd": "백엔드 개발: API 개발, 데이터베이스 활용, 테스트",
  "request": "근거로 확인되는 사항을 record_id와 함께 요약하세요."
}
```

```bat
python scripts/run_minimal_pipeline.py --backend-response data/backend_demo/careers_response.json --context context.json --check-only
python scripts/run_minimal_pipeline.py --backend-export outputs/backend_exports/실행ID/backend_evidence.json --context context.json --check-only
python scripts/run_minimal_pipeline.py --backend-url http://127.0.0.1:3000 --context context.json --check-only
```

후보를 줄이려면 `--record-id 실제레코드ID`를 순서대로 반복합니다. ID가 없거나 중복이면 중단합니다. fixture는 초안에 이미 후보가 있으므로 추가 문맥·후보 인자를 받지 않습니다.

## BE SQL 데이터셋 직접 사용

`--backend-seed`로 DB·BE 서버 없이 원본 SQL을 읽습니다. `--context`의 subject_did와 일치하는 사용자 한 명의 자격증명만 추출하고, 기존 BE 어댑터와 A/B/C 경로로 전달합니다. 원본 SQL을 실행하거나 DB에 적재하지 않습니다.

저장소의 현재 시드 첫 사용자에 맞춘 `data/backend_demo/seed_context.example.json`을 제공합니다. 다른 사용자는 새 문맥 파일의 subject_did를 변경하세요. DID 목록은 BE/test_users_did_list.csv에서 확인할 수 있으며 최종 선택은 SQL에 존재하는 DID를 기준으로 합니다.

```bat
python scripts/run_minimal_pipeline.py --backend-seed C:/Users/bje10/VC_AI_backend/BE/DB/seeds/03_seed_data.sql --context data/backend_demo/seed_context.example.json --check-only
```

검사가 통과하면 `--check-only` 대신 `--condition B_naive_rag --max-new-tokens 128`로 해당 사용자의 답변을 생성합니다. 2048 입력 토큰을 초과하면 `seed_selection.json`의 ID 중 필요한 것만 `--record-id`로 지정합니다. 자동 자르기나 후보 재충원은 하지 않습니다.

`seed_selection.json`에 SQL 해시·전체 건수·선택한 DID·레코드 ID·출처를 기록합니다. 추출된 careers 응답은 `backend/raw_response.json`에 저장되어 기존 `--backend-response` 모드로 재사용할 수 있습니다. 원본 전체를 결과 폴더에 복사하지 않습니다.

현재 시드는 `mock_seeder` 합성 데이터입니다. `input_kind=backend_seed`로 구분하며 verified 상태를 실제 서명 검증으로 승격하지 않습니다. C의 근거는 현재 비어 있습니다. 만료일·수여일 등은 기존 careers API에 노출되는 필드 범위를 그대로 따릅니다.

읽기 형식은 현재 시드의 한 줄짜리 `INSERT INTO public.users/credentials VALUES (...)`입니다. SQL 문자열의 작은따옴표 이스케이프와 NULL을 지원합니다. COPY·다중 행·명시적 열 목록·SQL 표현식 등 다른 형식은 오류로 중단합니다. 같은 사용자/DID/레코드의 중복이나 소유 사용자 누락도 차단합니다.

SQL 연결 변경의 관련 테스트만 실행:

```bat
python -m pytest -q tests/test_backend_seed.py tests/test_minimal_pipeline.py tests/test_backend_integration.py
```

## 산출물과 실패 확인

기본 위치: `outputs/minimal_pipeline/<새 UUID>/`.

- `manifest.json`: 입력 종류, 검증 근거, 단계, 해시, 모델 버전, 토큰 수, 완료 결과 수, 실제 추론 여부.
- `policy_audit.json`: 정책 적격 여부, 제외 이유, 검증 증빙 한계.
- `*.input.json`, `*.prompt.txt`: 원본 입력 사본 또는 조건별 모델 입력·chat template 적용 결과.
- `*.result.json`: 조건별 답변, 선택 근거 ID, 생성 메타데이터.
- `backend/`: BE 응답을 변환한 경우 원문·정규화 자료·격리 보고서.

실패하면 manifest에 실패 단계가 남으며 앞서 완료한 답변을 지우지 않습니다. 결과에는 원문·프로필이 포함될 수 있습니다. 기존 outputs나 초안에 덮어쓰지 않습니다.

## 관련 단위 테스트만 실행

```bat
python -m pytest -q tests/test_minimal_pipeline.py tests/test_transformers_runner.py tests/test_backend_qwen.py tests/test_backend_integration.py tests/test_paper_vc_adapter_v2.py tests/test_paper_context_renderer_v2.py
```

이 테스트들은 모델 대역을 사용합니다. 통과해도 실제 BE 서버 응답 또는 실제 Qwen 추론 완료를 뜻하지 않습니다.

## 보존·정리 후보

- 유지: BE 어댑터·클라이언트, Work 2C 정책·문맥, Qwen 클래스, 관련 테스트.
- 현재 위치에 보관: 기존 Stage1 `run_transformers_experiment.py`/`context_policy.py`(상태 기반 C), `run_backend_qwen.py`(기존 BE 진단), 교육 v1/v2, Work 2B 개별 준비 스크립트, 이전 안내 문서.
- 보존 필수: Work 2A·2B·2C, outputs, dev_drafts, ai.zip, BE.zip, 기존 미추적 파일.
- 삭제 후보만 기록: __pycache__, .pytest_cache. 비캐시 삭제 후보 없음. 삭제·이동은 수행하지 않음.

BE 상세 검증 증빙 전달은 별도 BE 변경 승인이 필요한 후속 작업입니다. 이 실행기는 해당 증빙을 추정하거나 생성하지 않습니다.

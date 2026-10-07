# BE → AI 연결 사용 안내

작성일: 2026-10-06 (한국 시간)

이번 변경은 백엔드 이력 조회, 데이터 변환, 기존 AI 프롬프트 연결을 추가합니다.
Qwen 모델 설정·프롬프트·Stage1 A/B/C 정책은 바꾸지 않았고 실제 모델을 실행하지 않았습니다.

## 1. 연결 구조와 범위

`BE /api/v1/subjects/{did}/careers` → `BackendClient` → `backend_adapter` →
`backend_evidence.json` → `BackendPipeline` → 기존 `build_prompt` / `snippet` → `llm.generate(prompt)`

- 제공 실행 명령은 마지막 단계를 `MockLLM`으로 확인합니다. Torch나 모델 다운로드가 필요하지 않습니다.
- `BackendPipeline`의 LLM 인터페이스는 기존 `TransformersLLM.generate(prompt)`와 같습니다.
- 실제 Qwen 실행·생성 품질 비교는 다음 Work에서 진행합니다.
- 회사 API 호출, 발급·폐기·검증 요청, DB 변경, GitHub push는 포함하지 않습니다.
- 회사 API 키를 AI에 복사할 필요가 없습니다. 이번 클라이언트는 현재 BE의 읽기 API만 호출합니다.

기준 소스: 업로드한 `ai.zip`; BE `e0ad118f38a192a253a8fa20b90a77b93909d5c4`.
기존 VC 스키마 참조는 AI 브랜치 `f742713f13b5c002ec1d91e543000c16b9f91092`입니다.

## 2. 설치와 기존 기능 확인

Anaconda Prompt에서 실행합니다. 기존 vc-ai 환경을 사용합니다.

```bat
conda activate vc-ai
cd /d C:\Users\bje10\VC_AI_Career_agent\ai
python -m pip install -r requirements.txt
python -m pytest -q
```

이미 의존성이 설치되어 있다면 pip 명령은 생략할 수 있습니다.
이번 변경으로 새 필수 패키지는 추가되지 않았습니다. 기존 테스트는 저장소의
`VC/src/schemas`를 참조합니다. 제공 패키지의 `snapshot/VC/src/schemas`는 해당 고정 버전의 참조 사본이며,
로컬 VC 코드가 이미 있으면 덮어쓰지 않습니다.

## 3. 서버 없이 먼저 확인

`data/backend_demo/careers_response.json`은 현재 BE 응답 모양으로 만든 합성 예제입니다.
실제 발급·서명된 자격증명이 아닙니다. 정상 형식 3건과 알 수 없는 상태값 1건을 포함합니다.

```bat
python scripts/export_backend_credentials.py --input-json data/backend_demo/careers_response.json --subject-did did:example:be-demo-001
```

예상: `Received: 4`, `Accepted: 3`, `Quarantined: 1`.
출력된 `Saved:` 폴더 경로를 아래 `EXPORT_DIR`에 넣습니다.

```bat
set "EXPORT_DIR=outputs\backend_exports\출력된-폴더-ID"
python scripts/run_backend_preview.py --export "%EXPORT_DIR%\backend_evidence.json" --question-file data/backend_demo/question.txt
```

예상: 근거 3개, `mock: true`, `inference_performed: false`.
결과 폴더의 `prompt.txt`에서 배지명·설명·날짜·근거 ID가 전달되는지 확인할 수 있습니다.
`mock_result.json`의 답변은 고정 문구이며 Qwen 응답이 아닙니다.

BE에서 `verified`라고 표시한 항목만 선택하는 진단도 가능합니다.

```bat
python scripts/run_backend_preview.py --export "%EXPORT_DIR%\backend_evidence.json" --question-file data/backend_demo/question.txt --status verified
```

이는 BE 상태값 필터 점검입니다. 논문 조건 `C_verified_rag`가 아니며 예제에서는 2건이 남습니다.

## 4. 실제 BE 서버에서 받기

현재 BE의 기본 origin은 `http://127.0.0.1:3000`입니다. `.env`에 다음을 추가하거나
명령의 `--base-url`로 지정할 수 있습니다. `/api/v1`을 origin 뒤에 붙이지 않습니다.

```dotenv
BE_API_BASE_URL=http://127.0.0.1:3000
```

BE 서버와 DB가 실행 중인 PC에서, 실제 등록된 사용자의 DID를 사용합니다.
BE 시드 데이터를 사용하는 경우 해당 BE의 `test_users_did_list.csv`에서 DID를 확인합니다.
합성 데모 DID는 실제 BE에 등록되어 있다고 가정하지 않습니다.

```bat
set "BE_DID=실제로-등록된-DID"
python scripts/export_backend_credentials.py --base-url http://127.0.0.1:3000 --subject-did "%BE_DID%"
```

BE가 다른 PC에서 실행되면 접근 가능한 해당 서버 origin을 지정해야 합니다.
출력된 새 `Saved:` 경로로 `EXPORT_DIR`을 바꾼 뒤 3번의 preview 명령을 실행합니다.
서버 404/401/500, 연결 오류, JSON 아닌 응답은 정상 빈 목록과 구분해 오류로 종료합니다.

## 5. 저장되는 파일

| 파일 | 내용 |
|---|---|
| `raw_response.json` | 수신한 JSON 전체. 사용자 프로필과 격리된 항목도 포함 |
| `backend_evidence.json` | 검사를 통과한 AI 입력 레코드와 subject DID |
| `validation_report.json` | 수신/채택/격리 수, 상태 분포, 누락 경고, 격리 항목의 원본 배열 위치 |
| `manifest.json` | 입력 소스, 수집 시각, 버전, 파일 해시, 연동용 목적 |

모든 쓰기는 UTF-8이며 매 실행마다 새 UUID 폴더를 생성합니다.
다른 사용자 DID나 중복 ID가 발견되면 전체 묶음을 중단하고 원본과 실패 보고서를 남깁니다.
그 외 개별 레코드 구조 오류는 격리합니다. 빈 목록/전부 격리된 경우 보고서는 남기되 CLI는 종료 코드 2입니다.
원본에는 프로필 정보가 포함될 수 있으므로 결과 폴더를 코드 저장소에 추가하지 않습니다.

## 6. 변환 규칙

| 입력 | 처리 |
|---|---|
| `user_profile.did` | 요청 DID와 일치해야 함; 각 근거의 subject로 사용 |
| `record_id` | 유효한 UUID만 채택; 원래 문자열 보존; 대소문자가 다른 중복도 감지 |
| `issuer`, `achievement_name` | 기관명/성취명 객체로 변환; 원본 내 이름과 충돌하면 격리 |
| `raw_data.achievement` | 더미 요약 구조의 설명과 허용 필드 추출 |
| `raw_data.credentialSubject.achievement` | VC 구조의 설명·성취 기준 등 추출; 단일 subject만 지원 |
| `raw_data.vc` | JWT 페이로드를 감싼 객체인 경우 해당 VC 객체 읽기 |
| `issued_at`, 원본 발급일 | 형식 검사; 제공된 값이 서로 충돌하면 격리 |
| 원본 `expirationDate` / `validUntil` 등 | 있으면 만료일 보존; 없으면 null 및 정보 누락 경고 |
| 원본 `evidence` | 이름·설명·URL만 추출; HTTP(S) `id`는 URL로 보존 |
| `status` | `backend_status`로 보존. 알 수 없는 값은 격리 |
| 검증 상세·검증 시각·source_provider | 현재 BE 조회에 없으므로 없는 것으로 기록 |

스킬을 배지명에서 추론하지 않습니다. 현재 BE가 정규화된 스킬 계약을 제공하지 않아 스킬 매핑은 이번 범위에 포함하지 않습니다.
이름·날짜 충돌을 조용히 한쪽 값으로 덮어쓰지 않습니다. `raw_response.json`에서 원본을 확인해 해결해야 합니다.

## 7. 기존 실험과 데이터 경계

BE 근거는 `be-evidence/1.0`이고 기존 VC `CredentialRecord`와 다른 형식입니다.
`verification.status`나 7개 체크를 만들어 넣지 않습니다. 대신 `backend_status`와
`verification_basis=backend_status_only`를 보존합니다.

- 기존 `credential_loader.py`와 VC 스키마 검사는 그대로입니다.
- BE export를 기존 `run_transformers_experiment.py --credentials`에 직접 넣으면 안 됩니다.
- BE 데이터를 실험 C에 사용하려면 검증 출처/결과를 받는 별도 계약과 실험 데이터 설계를 먼저 확정해야 합니다.
- 기존 합성 Stage1 A/B/C 실험은 기존 명령으로 동작합니다.
- 모델에 전달되는 근거는 기존 허용 필드 방식이며 상태·검증 메타데이터·프로필 이메일은 전달하지 않습니다.

## 8. 다음 Work의 Qwen 연결 지점

`BackendPipeline(llm).run(export, question, record_ids=..., status_filter="all")`의
`llm`에 기존 `TransformersLLM` 인스턴스를 전달하면 같은 generate 인터페이스를 사용합니다.
이번 preview는 이 위치에 Mock만 전달합니다. 실제 모델 생성과 GPU 메모리 점검은 수행하지 않았습니다.

다음 Work에서는 다음을 먼저 확인합니다.

1. 사용자 PC에서 실제 BE 조회가 성공했는지와 export 파일의 해시.
2. 질문과 관련된 명시적 후보 ID. `--record-id`를 여러 번 주면 후보를 선택할 수 있음.
3. 기존 모델의 2048 입력 토큰 한도. preview의 문자 수는 토큰 수가 아니며 자동 자르기 없음.
4. 기존 Qwen 설정으로 소규모 생성 후 실제 결과/토큰/시간 기록.
5. 입력 표현·지시문·출력 길이 비교는 인수인계 문서의 P1/P2/P3 계획에 따라 별도로 설계.

데이터를 BE로 바꾸면서 동시에 프롬프트까지 바꾼 결과를 기존 Stage1 결과와 직접 비교해 개선 효과라고 해석하지 않습니다.

## 9. 검증 결과와 한계

- 변경 전: 25 tests passed.
- 변경 후: 54 tests passed (기존 25 + 추가 29).
- BE 시드 SQL 45명/443건을 읽고 고정 BE의 `get_user_and_credentials` 메서드를
  메모리 DB 대체물로 실행: 443건 채택, 0건 격리, 45개 Mock 프롬프트 생성.
- 저장소 README에는 434건이라고 적혀 있지만 확인한 SQL의 자격증명 INSERT는 443건입니다.
- 환경: 이번 Work의 Linux/Python 3.12. 사용자 Windows/Python 3.11에서 패치 적용 후 pytest 재확인 필요.
- 실제 BE HTTP 서버/실제 PostgreSQL/기업 검증 API/실제 Qwen은 이번 검증에서 실행하지 않았습니다.

BE 담당자와 후속 합의할 항목: 최신 API 명세, 실제/더미 출처 구분, 검증 일시·결과 원문,
만료일, 실패/폐기 결과 보존. 현재 수신 기능은 이 작업을 기다리지 않고 사용할 수 있습니다.

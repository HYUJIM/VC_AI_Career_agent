# AI 서비스 설치와 실행

BE(`../BE`)가 호출하는 AI 서버입니다. 직무 비교(검증된 자격증명 ↔ 채용공고 요구사항)와 커리큘럼 생성을 계산합니다.
수집한 공고 묶음으로 직무별 요구 역량 통계도 계산합니다(`POST /v1/market/snapshots`. 수집·저장·조회는 BE).
무엇을 어떻게 계산하는지는 [docs/CAREER_AI_SERVICE.md](docs/CAREER_AI_SERVICE.md)를 보세요. 다음 작업(공고 수집·통계 → 커리큘럼 반영) 설계는
[docs/JOB_MARKET_STATS_DESIGN.md](docs/JOB_MARKET_STATS_DESIGN.md)에 있습니다. 아래 명령은 이 `ai/` 폴더에서 실행합니다.
AWS 배포(ECS Fargate, BE와는 ECS Service Connect `http://ai:8100`으로 연결)는 [../deploy/README.md](../deploy/README.md)와 작업 정의
템플릿 [../deploy/ecs/ai-service.taskdef.json](../deploy/ecs/ai-service.taskdef.json)을 보세요. 운영 이미지는 Apple Silicon에서도
`docker build --platform linux/amd64 -t <ECR 주소>/vc-ai:<커밋> .`로 빌드합니다(BE와 같은 커밋).

## 1. 가장 쉬운 방법: BE와 함께 Docker Compose로

`BE/.env`에 `AI_SERVICE_TOKEN`(32바이트 이상)을 넣고 BE 폴더에서 실행하면 DB·BE·AI가 함께 뜹니다.
토큰은 Compose가 BE와 AI 컨테이너에 똑같이 넣고, BE는 `http://ai:8100`으로 접속합니다.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

```bash
cd ../BE && docker compose up --build -d
```

AI 상태: <http://127.0.0.1:8100/health>. LLM(기본 Google Gemini API)을 쓰려면 이 폴더에 `.env`를 만들어 6번 설정(`GEMINI_API_KEY` 등)을 넣고 `docker compose up -d ai`로 다시 띄웁니다.

## 2. 직접 실행 (개발·디버깅)

Python 3.11이 필요합니다.

```bash
python3.11 -m venv .venv
```

```bash
.venv/bin/python -m pip install -r requirements.txt
```

`.env.example`을 `.env`로 복사하고 `AI_SERVICE_TOKEN`에 **BE `.env`와 같은 값**을 넣은 뒤 실행합니다.

```bash
.venv/bin/python -m uvicorn src.server.app:app --host 127.0.0.1 --port 8100
```

BE도 로컬에서 실행하면 BE의 기본값 `AI_SERVICE_URL=http://127.0.0.1:8100`으로 연결됩니다. Windows는 `.venv/bin/python` 대신 `.venv\Scripts\python`을 씁니다.

- API 문서: <http://127.0.0.1:8100/docs> (`/v1/...`는 모두 `Authorization: Bearer <AI_SERVICE_TOKEN>` 필요. 토큰 없이 "Try it out"을 누르면 본문 형식과 관계없이 401)
- AI를 다른 PC(예: GPU PC)에서 실행하면 BE의 `AI_SERVICE_URL`을 그 주소로 바꿉니다. 근거 데이터가 오가므로 HTTPS 또는 신뢰된 내부망만 쓰고,
  평문 HTTP는 BE에서 `AI_SERVICE_ALLOW_HTTP=true`로 명시해야 허용됩니다.

## 3. 사용 흐름 (FE → BE → AI)

1. 연구자(BE `RESEARCHER_USER_IDS`)가 원문 인용과 함께 공고·학습자료를 반입합니다: `POST /api/v1/research/job-postings`, `POST /api/v1/research/learning-resources`.
   AI가 주석이 원문과 맞는지 검사하고, BE가 버전으로 저장합니다. `dataset_kind=real`이고 수집 7일 이내인 공고·30일 이내인 자료만 비교에 쓰입니다.
2. 사용자가 FE **직무 비교·커리큘럼** 화면에서 공고를 고르고 비교합니다 (`POST /api/v1/career-analyses`).
   BE가 사용자의 모든 자격증명을 DXWorks로 **지금 다시 검증**하고, 통과한 것만 AI로 보냅니다.
3. 같은 화면에서 학습 계획을 만듭니다 (`POST /api/v1/curricula`, 비교 후 5분 이내). 템플릿은 즉시, LLM은 최대 90초.

## 4. 테스트

테스트 도구(pytest)는 실행용 `requirements.txt`가 아니라 `requirements-dev.txt`에 있습니다.

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
```

```bash
.venv/bin/python -m pytest -q
```

GPU·DB·네트워크 없이 실행됩니다. BE 쪽 공유 형식 사본(`../BE/app/ai_contract`)이 같은지도 검사합니다.
BE 테스트(`../BE/tests/test_career.py`)는 이 AI 앱을 같은 프로세스에 띄워 실제 HTTP 형식으로 연동을 검사합니다.

## 5. 공유 형식(계약)을 바꿨을 때

`src/career/contracts/`를 고쳤다면 BE에 같은 사본을 복사하고 두 쪽 테스트를 돌린 뒤 **BE와 AI를 함께 배포**합니다.

```bash
.venv/bin/python scripts/sync_contract_to_be.py
```

차이만 확인하려면 `--check`를 붙입니다(CI에서 실행). BE 쪽 사본(`../BE/app/ai_contract/`)은 직접 고치지 않습니다.

## 6. 커리큘럼 LLM 켜기 — Google Gemini API (선택, 기본 꺼짐)

LLM 없이도 템플릿 커리큘럼은 만들어집니다. LLM을 쓰려면 ① BE `.env`의 `CURRICULUM_LLM_USER_IDS`에 사용자 UUID를 넣고,
② FE에서 "Google Gemini API로 배분" 동의를 체크하고, ③ 이 폴더의 `.env`에서 생성기를 켭니다.
기본 공급자는 **Google Gemini API**(`CURRICULUM_LLM_PROVIDER=gemini`, 비워 두어도 같음)입니다. SDK 없이 이미 쓰는 `httpx`로
Interactions API(`POST https://generativelanguage.googleapis.com/v1/interactions`)를 부릅니다(`src/career/gemini_client.py`).

> 예전에 OpenAI 호환 설정(`CURRICULUM_LLM_BASE_URL` 등)만 넣고 `CURRICULUM_LLM_PROVIDER`를 비워 두었다면 이제 Gemini로 해석되어
> `GEMINI_API_KEY`가 없다는 설정 오류가 납니다. 그 설정을 계속 쓰려면 `CURRICULUM_LLM_PROVIDER=compatible`을 적습니다.

### 6-1. Gemini API 키 만들기

1. Google 계정(만 18세 이상, 한국은 사용 가능 지역)으로 Google AI Studio의 API 키 화면 <https://aistudio.google.com/apikey>에
   들어갑니다. 처음 쓰는 계정은 약관에 동의하면 기본 Google Cloud 프로젝트와 키가 만들어집니다.
2. 이미 있는 Google Cloud 프로젝트를 쓰려면 AI Studio의 Projects → "Import projects"로 가져온 뒤 "Create API key"를 누릅니다
   (버튼이 비활성이면 그 프로젝트의 편집 권한이 필요). AI Studio에서 새로 만든 키는 Gemini API 전용으로 제한된 키입니다.
3. 가져온 프로젝트는 무료 등급을 쓰기 전에 프로젝트 확인이 필요할 수 있습니다(안 하면 403 `permission_denied`. 이유로 `FREE_TIER_VERIFICATION_REQUIRED`가 함께 보일 수 있음).
   결제 계정을 연결한 프로젝트(유료 등급)는 필요 없습니다.
4. 키는 **이 폴더의 `.env`에만** 넣습니다. 저장소·FE·이슈·채팅에 붙여 넣지 않고, 브라우저(FE)에서 직접 부르지 않습니다.
   새어 나갔으면 새 키를 만들어 바꾼 뒤 옛 키를 지웁니다. AWS에서는 Secrets Manager `vc/ai-llm`(키 `GEMINI_API_KEY`)에 둡니다
   ([../deploy/ENV_MATRIX.md](../deploy/ENV_MATRIX.md)).

### 6-2. `.env` 설정

```dotenv
CURRICULUM_LLM_ENABLED=true
CURRICULUM_LLM_PROVIDER=gemini
GEMINI_API_KEY=<AI Studio에서 만든 키>
```

| 변수 | 비우면 | 규칙 · 설명 |
|---|---|---|
| `GEMINI_API_KEY` | (필수) | 공백·줄바꿈 없는 출력 가능한 ASCII. 요청의 `x-goog-api-key` 헤더에만 들어가고 주소·로그·오류·감사 기록·`/v1/curricula/generator` 응답에는 나오지 않음. 이 서비스는 `GOOGLE_API_KEY`·`CURRICULUM_LLM_API_KEY`를 Gemini 키로 읽지 않음 |
| `CURRICULUM_LLM_MODEL` | `gemini-3.5-flash-lite` | 안정(Stable) 모델 중 가장 싸고 빠르며 구조화 JSON 추출용. 다른 안정 모델: `gemini-3.1-flash-lite`(더 쌈), `gemini-3.6-flash`, `gemini-3.8-flash`. `gemini-flash-latest` 같은 별칭(예고 2주 후 바뀜)과 미리보기 모델은 쓰지 않음. `gemini-2.5-flash`·`gemini-2.5-pro`는 지원 중단(2026-12-01부터 다른 모델로 자동 전환) |
| `CURRICULUM_LLM_THINKING_LEVEL` | 모델 기본값(Flash-Lite는 `minimal`) | `minimal`·`low`·`medium`·`high`만. `gemini-3.8-flash`·Gemini 4 모델에 `minimal`은 설정 오류. 출력 상한 4,096 토큰에 생각 토큰이 포함되므로 높일수록 불완전 응답 → 템플릿 대체가 늘어남 |
| `GEMINI_API_VERSION` | `v1` | `v1`(안정) 또는 `v1beta`만. v1이 404로 거절될 때만 `v1beta`로 바꿈(요청·응답 형식은 같음) |
| `CURRICULUM_LLM_MAX_CONCURRENCY` | `2` | 1~32(ASCII 숫자). AI 프로세스 하나가 동시에 돌리는 LLM 생성 수(공급자 공통). 자리가 10초 안에 나지 않으면 모델을 부르지 않고 실패(BE 503 `LLM_BUSY`). 범위 밖이면 AI가 시작하지 않음(`CURRICULUM_LLM_MAX_CONCURRENCY must be an integer from 1 to 32` 한 줄, 종료 코드 1) |

동시 생성 상한을 뺀 위 규칙에 맞지 않으면(`CURRICULUM_LLM_ENABLED`가 `true`가 아닌 경우 포함) 서비스는 뜨고 LLM 모드 요청만
503 `GENERATOR_CONFIGURATION_REQUIRED`이며 템플릿 계획은 그대로 됩니다. `temperature`·`top_p`·`top_k`는 보내지 않습니다(3.6 이후 모델은 무시하고 다음 세대는 거절 예정).

### 6-3. 연결 확인 (실제 호출 1번)

키를 넣은 뒤 이 폴더에서 실행합니다.

```bash
.venv/bin/python scripts/gemini_check.py
```

`.env`를 읽고(셸에 이미 있는 변수가 우선) 서비스와 같은 코드(`gemini_client.py`)로 가짜 단계 2개짜리 요청을 **한 번** 보낸 뒤
엔드포인트·모델·생각 수준·HTTP 상태·지연 시간(ms)·토큰 사용량·JSON 검사(`json_valid`)·선택 규칙 검사(`draft_valid`)를 출력합니다.
키·요청·모델이 쓴 글은 출력하지 않습니다. 종료 코드 0 = 연결됐고 응답이 커리큘럼 초안 JSON 형식에 맞음, 1 = 호출 실패 또는 JSON
불일치(`error:`·`hint:` 줄에 HTTP 상태와 공급자 오류 코드), 2 = 설정 오류. `CURRICULUM_LLM_ENABLED`가 아직 `false`여도 확인은
됩니다(이 명령의 프로세스에서만 켬). `--model <ID>`, `--api-version v1beta`, `--thinking-level low`로 한 번만 바꿔 볼 수 있고,
`--no-dotenv`는 `.env`를 읽지 않습니다. 유료 키면 실행마다 요청 1건이 과금됩니다. 이 스크립트는 Docker 이미지에 들어가지 않으므로
Compose로 띄운 경우에도 이 폴더의 venv로 실행합니다.

### 6-4. 무엇이 Google로 가는가 (데이터 정책)

- **보내는 것:** 고정 지시문과 계획 옵션(단계 ID·기술 ID·자료 버전 ID·최소 시간·비용·수준, 학습자료 주석 인용 200자 이내),
  총 시간·예산 한도, 기본 계획, 출력 형식(`../src/career/llm_client.py`의 `selector_payload`). 60,000바이트를 넘으면 보내지 않고
  `LLM_CONTEXT_TOO_LARGE`. **보내지 않는 것:** VC 원문·DID·이름·이메일 등 사용자 정보, 공고 원문, 자료 URL.
- 학습자료 주석 인용은 화면에 보이는 글자만 보냅니다(`visible_text`): Unicode 서식 문자(Cf: 방향 제어·폭 없는 문자·태그 문자)와
  Default_Ignorable 글자 전부(이형 선택자 U+FE00–FE0F·U+E0100–E01EF, CGJ, 한글 채움 문자, 아직 배정되지 않은 U+E0080 같은 폭 0 글자),
  사용자 정의 영역 글자(Co), 탭·줄바꿈 밖의 제어 문자, 점자 빈칸 U+2800, U+FFFC, 이집트 상형문자 서식 문자를 지웁니다. 남기는 것은
  보이는 두 글자 사이의 ZWJ 하나(이모지·인도계 문자 결합)와, 문자·숫자·문장부호·기호 바로 뒤의 U+FE0E·U+FE0F 하나(글자·이모지
  표시 방식)뿐입니다.
  연구자가 검토한 인용이 겉보기와 달리 모델만 읽는 숨은 지시를 싣지 못하게 하기 위해서입니다. 인용 문구는 여전히 신뢰하지 않는
  입력으로 다루고, 모델 출력은 허용된 단계·자료·시간 안에서만 검사를 통과합니다.
- 요청마다 `store: false`를 보내 Gemini API가 상호작용을 저장하지 않게 합니다(기본은 저장: 무료 등급 1일, 유료 55일).
- **무료 등급 키:** Google 약관에 따라 입력·출력이 Google 제품 개선에 쓰이고 사람이 검토할 수 있습니다(`store: false`와 무관).
  Google은 무료 등급에 민감한 정보를 보내지 말라고 안내합니다. **유료 등급**(Cloud 프로젝트에 결제 계정 연결)은 제품 개선에 쓰지
  않고, 정책 위반 감지를 위해 제한된 기간만 기록합니다. 실제 사용자 데이터로 운영하면 유료 등급을 씁니다. FE의 동의 문구가
  외부 처리자로 Google Gemini API를 밝힙니다.
- 모델 출력은 신뢰하지 않습니다. 스키마로 JSON을 받아도 `output_validator`가 단계·순서·자료 ID·시간·예산을 다시 검사하고, 틀리면
  1번 고쳐 받고, 그래도 틀리면 템플릿 계획으로 대체합니다(`generation_method: "llm_fallback"`, 경고 `UNTRUSTED_MODEL_OUTPUT_DISCARDED`).
  도구 호출(`function_call` 등)이 든 응답은 실행하지 않고 `LLM_MALFORMED_RESPONSE`입니다.

### 6-5. 한도 · 비용 · 오류

- 커리큘럼 1건 = 모델 호출 최대 2번(수정 1번). 호출마다 출력 4,096 토큰(생각 포함)·25초·응답 256 KiB 상한, 리디렉션을 따라가지
  않고 `HTTP(S)_PROXY` 같은 프록시 환경변수를 쓰지 않습니다. 동시 생성 자리 대기 10초 + 25초 × 2 = 최대 60초라 BE가 기다리는
  90초(`AI_GENERATION_TIMEOUT_SECONDS`) 안에 끝납니다. 클라이언트는 스스로 다시 시도하지 않습니다(무료 등급의 분당 한도가 작음).
- 우리 쪽 한도: AI 프로세스마다 동시 생성 `CURRICULUM_LLM_MAX_CONCURRENCY`(기본 2)개, BE에서 허용된 계정마다 24시간에
  `CURRICULUM_LLM_DAILY_LIMIT`(BE `.env`, 기본 20)건입니다. BE 한도는 저장된 LLM 계획(DB)과 Gemini까지 간 실패한 시도(BE 태스크
  메모리)를 함께 세고, 넘으면 Gemini를 부르지 않고 429 + `Retry-After`입니다([../BE/README.md](../BE/README.md) "직무 비교 · 커리큘럼").
  하루 최대 호출 수는 대략 허용된 계정 수 × 한도 × 2(수정 1번)이므로 Google 쪽 RPD·지출 한도를 그보다 넉넉하게 둡니다.
- Google의 한도(RPM·입력 TPM·RPD)는 **키가 아니라 프로젝트 단위**이고 RPD는 태평양 시간 자정에 초기화됩니다. 모델별 무료 등급
  숫자는 문서에 없으므로 AI Studio의 Rate limit 화면(<https://aistudio.google.com/rate-limit>)에서 확인합니다.
- 가격(2026-11 문서 기준, 바뀔 수 있음): `gemini-3.5-flash-lite`는 무료 등급 무료, 유료 100만 토큰당 입력 $0.30·출력 $2.50(생각 포함).
  유료 등급이면 AI Studio의 Spend 화면에서 월 지출 상한과 결제 알림을 둡니다. 유료 등급에는 10분 단위 지출 한도가 있어 넘으면
  429 `RESOURCE_EXHAUSTED`이고, 선불 잔액이 0이면 그 결제 계정의 모든 키가 402입니다.

| Gemini 응답 | AI 오류 코드 (다시 시도 가능) | BE 응답 |
|---|---|---|
| HTTP 408·429·500·502·503·504 | `LLM_PROVIDER_REJECTED` (가능) | 503 |
| 그 밖의 HTTP 오류: 400(요청 오류·안전 차단 `safety`·`jailbreak`·`prohibited_content`·`blocklist`), 401(키), 402(선불 잔액), 403(권한·프로젝트 확인), 404(모델), 리디렉션 | `LLM_PROVIDER_REJECTED` (불가) | 422 |
| 연결 실패·25초 초과 | `LLM_PROVIDER_UNAVAILABLE` (가능) | 503 |
| 상태 `failed`·`cancelled` | `LLM_PROVIDER_REJECTED` (불가) | 422 |
| 상태 `incomplete`(출력 한도)·`requires_continuation` | 오류 아님: 1번 다시 생성 → 그래도 불완전하면 템플릿 대체 `llm_fallback` | 201 (계획 저장) |
| 해석할 수 없는 응답·도구 호출·글 없음 | `LLM_MALFORMED_RESPONSE` (불가) | 422 |
| 응답 256 KiB 초과 | `LLM_RESPONSE_TOO_LARGE` (불가) | 422 |
| (호출 전) 동시 생성 자리가 10초 안에 나지 않음 | `LLM_BUSY` (가능) | 503 |
| (호출 전) BE가 받은 생성기 정보와 지금 AI 설정이 다름 | `GENERATOR_CONFIGURATION_CHANGED` (가능) | 503 |

HTTP 오류는 AI 로그에 `Gemini API rejected the request: status=403 code=permission_denied retryable=False`
처럼 한 줄 남습니다(상태와 식별자 모양의 오류 코드만. 오류 메시지·본문·키는 남기지 않음). Interactions API의 오류 본문은
`{"error": {"code": "<snake_case>", "message": …}}`이고(401 `authentication`, 403 `permission_denied`, 404 `model_not_found`,
429 `rate_limit_exceeded`·`quota_exceeded`), 일부 오류는 예전 Google 형식(`status`·`details[].reason`)으로 와서
`PERMISSION_DENIED/FREE_TIER_VERIFICATION_REQUIRED`처럼 이유가 함께 찍힐 수 있습니다. 코드는 진단용이고, 다시 시도할지는 HTTP 상태로만 정합니다.
BE가 생성기 정보를 받은 AI와 생성 요청을 받은 AI의 설정이 다르면(배포 중 AI 태스크마다 모델·사고 수준·API 버전이 다름) 모델을 부르지 않고
`GENERATOR_CONFIGURATION_CHANGED`(다시 시도 가능, BE 503 + `Retry-After: 5`)로 끝나며 하루 LLM 몫도 쓰지 않습니다.

### 6-6. 다른 공급자 (선택)

> 공급자를 바꾸면 FE 학습 계획 화면의 동의 문구(`FE/src/pages/CareerPlanPage.tsx`, "외부 서비스 Google Gemini API")도
> 실제로 데이터를 받는 곳으로 함께 고칩니다. 문구는 지금 Gemini로 고정되어 있어, 고치지 않으면 사용자가 다른 처리자에
> 동의한 것이 됩니다.

**OpenAI 호환 API**

```dotenv
CURRICULUM_LLM_ENABLED=true
CURRICULUM_LLM_PROVIDER=compatible
CURRICULUM_LLM_BASE_URL=https://<공급자>/v1
CURRICULUM_LLM_MODEL=<모델 ID>
CURRICULUM_LLM_API_KEY=<키>
CURRICULUM_LLM_TOKEN_PARAMETER=max_completion_tokens
```

`CURRICULUM_LLM_API_KEY`는 공백 없는 출력 가능한 ASCII 문자여야 합니다(요청의 `Authorization` 헤더에 그대로 들어감). 아니면 생성기 설정 오류(`GENERATOR_CONFIGURATION_REQUIRED`)입니다.

**로컬 Qwen 모델** (`Qwen/Qwen2.5-1.5B-Instruct` 고정 revision, NVIDIA GPU·CUDA 11.8, Docker 이미지에는 미포함)

```bash
.venv/bin/python -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cu118
```

```bash
.venv/bin/python -m pip install -r requirements-gpu.txt
```

```dotenv
CURRICULUM_LLM_ENABLED=true
CURRICULUM_LLM_PROVIDER=local-transformers
CURRICULUM_LOCAL_ALLOW_DOWNLOAD=true
```

`CURRICULUM_LOCAL_ALLOW_DOWNLOAD=true`는 처음 모델을 받을 때만 쓰고 이후 `false`로 되돌립니다.
로컬 모델의 상한은 입력 2,048·출력 256 토큰이고, 넘었을 때 동작이 다릅니다.

- 입력 초과: 생성 단계에서 `LLM_CONTEXT_TOO_LARGE`(BE 422)로 실패합니다. 계획 컨텍스트 단계의 사전 검사는 공급자 공통 60,000바이트 기준이라 로컬 상한 초과는 생성할 때 처음 걸립니다.
- 출력 초과: 오류가 아닙니다. 잘린 초안은 불완전한 출력으로 버리고 한 번 다시 생성하며, 그것도 잘리면 기본(템플릿) 계획을
  `generation_method: "llm_fallback"`과 경고 `UNTRUSTED_MODEL_OUTPUT_DISCARDED`로 돌려줍니다(GPU 생성 2회).
- 단계마다 UUID가 들어가 출력이 빨리 찹니다. 보완할 기술이 3개 정도만 되어도(기본 `assess` 기준) 기본 계획으로 대체될 수 있으니,
  큰 계획은 템플릿 모드나 Gemini·OpenAI 호환 API(출력 4,096 토큰)를 쓰거나 대상 공고를 좁힙니다.

## 7. 문제 해결

| 증상 | 원인과 조치 |
|---|---|
| FE에 "AI 서버에 연결할 수 없습니다" (BE 503) | AI가 꺼져 있거나 BE의 `AI_SERVICE_URL`이 틀림 |
| FE에 "AI_SERVICE_TOKEN이 서로 다릅니다" | BE `.env`와 AI의 `AI_SERVICE_TOKEN`이 다름 |
| BE 502 "contract checks" | 공유 형식이 다름 → 5번 실행 후 둘 다 재시작 |
| `/v1/...`가 503 `AI_SERVICE_TOKEN_NOT_CONFIGURED` | AI의 토큰이 비었거나 32바이트 미만 |
| AI가 시작하지 않음 `AI_MAX_BODY_BYTES must be a positive integer (bytes)` (한 줄, 종료 코드 1) | `AI_MAX_BODY_BYTES`가 양의 정수(바이트)가 아님(예: `32MiB`, `0`). 부호·밑줄·소수점이 들어간 표기(`+4096`, `4_096`, `4096.0`)와 전각 숫자도 해당 → 지우거나 비우면 기본값 32 MiB, 넣으려면 ASCII 숫자만(예: `33554432`, 18자리까지) |
| AI가 시작하지 않음 `CURRICULUM_LLM_MAX_CONCURRENCY must be an integer from 1 to 32` (한 줄, 종료 코드 1) | 그 변수를 비우거나(기본 2) 1~32의 ASCII 숫자로 |
| LLM 모드 계획이 503 `GENERATOR_CONFIGURATION_REQUIRED` | AI의 LLM 설정이 규칙에 맞지 않음: `CURRICULUM_LLM_ENABLED=true`, `GEMINI_API_KEY`(공백·줄바꿈 없음), 모델 ID, `CURRICULUM_LLM_THINKING_LEVEL`, `GEMINI_API_VERSION`(6-2). `scripts/gemini_check.py`가 어느 변수인지 한 줄로 알려 줌(값은 출력하지 않음) |
| LLM 모드 계획이 422 `LLM_PROVIDER_REJECTED`, AI 로그 `Gemini API rejected the request: status=…` | 401 키가 틀림·폐기됨, 403 권한·프로젝트 확인(`FREE_TIER_VERIFICATION_REQUIRED`)·결제 문제, 404 모델 ID(또는 `GEMINI_API_VERSION=v1beta`), 400 요청 오류·안전 차단, 402 선불 잔액 0 → `scripts/gemini_check.py`로 확인 |
| LLM 모드 계획이 503 `LLM_PROVIDER_REJECTED`(429·5xx) / `LLM_PROVIDER_UNAVAILABLE` | Google의 분·일 한도(프로젝트 단위) 또는 일시 장애 / AI에서 `generativelanguage.googleapis.com:443`으로 나가는 길(NAT·보안 그룹·DNS) → 잠시 뒤 다시 시도, 자주 나오면 템플릿 모드 |
| LLM 모드 계획이 503 `LLM_BUSY` | AI 프로세스의 동시 LLM 생성이 `CURRICULUM_LLM_MAX_CONCURRENCY`(기본 2)개로 찬 채 10초가 지남. 모델은 부르지 않았음 → 잠시 뒤 다시 시도 |
| LLM 모드인데 결과가 "LLM 초안 폐기 후 기본 계획"(`llm_fallback`) | 모델 초안이 검사를 2번 통과하지 못했거나 출력 한도(4,096 토큰)에 걸림. `CURRICULUM_LLM_THINKING_LEVEL`을 비우거나 낮추고, 큰 계획은 대상 공고를 좁힘 |
| FE에 "분석 요청이 많습니다…" (BE 503 `AI_SERVICE_BUSY`) | BE 프로세스가 AI로 동시에 보내는 공고·학습자료 묶음 요청이 BE의 `AI_MAX_CONCURRENCY`(기본 4)개로 찬 채 10초가 지남. 이 요청은 AI에 오지 않았음 → 잠시 뒤 다시 시도, 자주 나오면 AI가 느린지(CPU·메모리) 확인 |
| `/v1/...`의 422 `REQUEST_SCHEMA_INVALID`에 보낸 값이 없음 | `detail`은 오류마다 `loc`·`msg`·`type`만 싣고(입력값 없음) 앞에서부터 20개까지, 64자를 넘는 키 이름은 `<long key>`로 나옴 |
| 비교할 공고가 없음 | 연구자 반입 공고가 없거나, 수집 7일·게시 90일이 지났거나, `dataset_kind`가 `real`이 아님 |
| 공고·자료 미리보기·반입이 422 `TEXT_EXPANDS_TOO_MUCH_WHEN_NORMALIZED` | 원문·인용에 NFKC 정규화로 크게 늘어나는 호환 문자(예: U+FDFA)가 많음 → 풀어 쓴 문자로 고쳐 다시 반입 |
| 새 버전으로 올린 뒤 공고 목록·직무 비교가 409 `JOB_SOURCE_INVALID` (학습자료는 `LEARNING_SOURCE_INVALID`) | 이미 저장된 공고·자료 버전이 지금 규칙의 재검사를 통과하지 못함. 이번 버전은 결합 문자가 30개 넘게 이어진 구간을 30개에서 자르므로, 그런 구간이 든 원문·인용(실제 문장에는 없는 형태)이 저장돼 있으면 걸릴 수 있음 → `../BE/README.md`의 "결합 문자 점검"으로 찾아 고쳐서 다시 반입 |
| 커리큘럼 409 | 비교 결과 유효시간(5분)이 지남 → 다시 비교 |

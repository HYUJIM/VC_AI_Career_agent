# AI 서비스 설치와 실행

BE(`../BE`)가 호출하는 AI 서버입니다. 직무 비교(검증된 자격증명 ↔ 채용공고 요구사항)와 커리큘럼 생성을 계산합니다.
무엇을 어떻게 계산하는지는 [docs/CAREER_AI_SERVICE.md](docs/CAREER_AI_SERVICE.md)를 보세요. 아래 명령은 이 `ai/` 폴더에서 실행합니다.

## 1. 가장 쉬운 방법: BE와 함께 Docker Compose로

`BE/.env`에 `AI_SERVICE_TOKEN`(32바이트 이상)을 넣고 BE 폴더에서 실행하면 DB·BE·AI가 함께 뜹니다.
토큰은 Compose가 BE와 AI 컨테이너에 똑같이 넣고, BE는 `http://ai:8100`으로 접속합니다.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

```bash
cd ../BE && docker compose up --build -d
```

AI 상태: <http://127.0.0.1:8100/health>. LLM을 쓰려면 이 폴더에 `.env`를 만들어 6번 설정을 넣고 `docker compose up -d ai`로 다시 띄웁니다.

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

## 6. 커리큘럼 LLM 켜기 (선택, 기본 꺼짐)

LLM 없이도 템플릿 커리큘럼은 만들어집니다. LLM을 쓰려면 ① BE `.env`의 `CURRICULUM_LLM_USER_IDS`에 사용자 UUID를 넣고,
② FE에서 "LLM으로 배분" 동의를 체크하고, ③ 이 폴더의 `.env`에서 생성기를 켭니다.

**OpenAI 호환 API**

```dotenv
CURRICULUM_LLM_ENABLED=true
CURRICULUM_LLM_PROVIDER=compatible
CURRICULUM_LLM_BASE_URL=https://<공급자>/v1
CURRICULUM_LLM_MODEL=<모델 ID>
CURRICULUM_LLM_API_KEY=<키>
CURRICULUM_LLM_TOKEN_PARAMETER=max_completion_tokens
```

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
  큰 계획은 템플릿 모드나 OpenAI 호환 API(출력 4,096 토큰)를 쓰거나 대상 공고를 좁힙니다.

## 7. 문제 해결

| 증상 | 원인과 조치 |
|---|---|
| FE에 "AI 서버에 연결할 수 없습니다" (BE 503) | AI가 꺼져 있거나 BE의 `AI_SERVICE_URL`이 틀림 |
| FE에 "AI_SERVICE_TOKEN이 서로 다릅니다" | BE `.env`와 AI의 `AI_SERVICE_TOKEN`이 다름 |
| BE 502 "contract checks" | 공유 형식이 다름 → 5번 실행 후 둘 다 재시작 |
| `/v1/...`가 503 `AI_SERVICE_TOKEN_NOT_CONFIGURED` | AI의 토큰이 비었거나 32바이트 미만 |
| 비교할 공고가 없음 | 연구자 반입 공고가 없거나, 수집 7일·게시 90일이 지났거나, `dataset_kind`가 `real`이 아님 |
| 공고·자료 미리보기·반입이 422 `TEXT_EXPANDS_TOO_MUCH_WHEN_NORMALIZED` | 원문·인용에 NFKC 정규화로 크게 늘어나는 호환 문자(예: U+FDFA)가 많음 → 풀어 쓴 문자로 고쳐 다시 반입 |
| 커리큘럼 409 | 비교 결과 유효시간(5분)이 지남 → 다시 비교 |

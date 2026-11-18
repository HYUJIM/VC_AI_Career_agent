# 환경변수·설정 표 (NCP 서버 한 대 + Docker Compose)

[README.md](README.md)의 구성(nginx → backend → ai, postgres는 TLS, 모두 [docker-compose.yml](docker-compose.yml))에서 파일마다 넣을 값입니다.
`<도메인>`은 `deploy/.env`의 `SERVER_NAME`(예 `career.example.com`)입니다. 항목별 기준은 [CHECKLIST.md](CHECKLIST.md).

표기
- 필수: **필수** / **조건부**(그 기능을 쓸 때) / **도메인 뒤**(README ⑦ HTTPS 이후) / 선택 / **넣지 않음**
- 파일: `.env` = `deploy/.env`(compose가 `${…}`로 읽음), `backend.env` = `deploy/backend.env`(backend만), `ai.env` = `deploy/ai.env`(ai만).
  세 파일은 서버에만 두고 권한 600, `scripts/init-secrets.sh`가 예시 파일(`*.example`)을 복사해 만들고 비밀값을 무작위로 채웁니다(있으면 건드리지 않음).
- 형식: 한 줄에 `KEY=값`(값 앞에 `KEY=`를 한 번 더 붙이지 않음). Compose는 따옴표 없는 값과 큰따옴표 값의 `$`를 변수로 풀므로, `$`가 든 값은
  작은따옴표로 감쌉니다(`KEY='값'`).
- 바꾼 뒤: `.env`·`backend.env`·`ai.env`의 값은 컨테이너를 다시 만들어야 들어갑니다(`docker compose up -d backend`·`ai`, 또는 `scripts/deploy.sh`).
  `docker compose restart`로는 바뀌지 않습니다.
- 검사하는 코드: 저장소 기준 파일과 함수. `config.py` = `BE/app/core/config.py`, `database.py` = `BE/app/services/database.py`,
  시작 점검 = `BE/app/services/db_startup_check.py`
- "종료 1": 운영 설정 검사·범위 검사·형식 검사(`LOG_LEVEL`, `HOLDER_KEY_ENCRYPTION_KEY`)가 실패하면 BE는 stderr에 그 메시지 **한 줄**
  (`APP_ENV=production requires …`처럼 변수 이름만, 값 없음)을 남기고 종료 코드 1로 끝납니다(`BE/main.py`가 불러올 때). AI의 `AI_MAX_BODY_BYTES`·
  `CURRICULUM_LLM_MAX_CONCURRENCY`도 같습니다(`ai/src/server/app.py`). "종료 3": 시작 DB 점검 실패(`DB startup check failed: <종류>: <요약>` 한 줄 +
  `Application startup failed. Exiting.`). 둘 다 `restart: unless-stopped`라 계속 다시 시작하므로 `docker compose ps`(`Restarting`)와
  `docker compose logs --tail=50 backend`로 봅니다.
- 시작 점검·migrate 요약 가운데 `RDS`가 든 문구(`check PGSSLROOTCERT: RDS CA bundle` 등)는 코드에 고정된 예전 이름입니다. 이 구성에서의 뜻은 아래 4번 표.

---

## 1. `deploy/.env` (compose가 읽음)

| 변수 | 필수 | 값 | 쓰는 곳 | 검사하는 코드 | 틀리면 |
|---|---|---|---|---|---|
| `SERVER_NAME` | 도메인 전에는 비움, 뒤에는 **필수** | `<도메인>` (`https://` 없이) | nginx(`server_name`, HTTPS 리디렉션), `scripts/issue-cert.sh` | `nginx/40-vc-site.sh` | 비움 → `_`, 공인 IP로 HTTP만. 글자·점·`-` 밖의 문자 → nginx가 `40-vc-site: SERVER_NAME must be a host name …`로 시작하지 않음. 도메인인데 인증서 없음 → HTTP 모드. IP 주소 → `issue-cert.sh`가 거부(인증서를 받을 수 없음) |
| `DB_NAME` | 선택 | `vc_career_agent` | postgres 첫 초기화(`initdb/10-roles.sh`), migrate·backend의 `DB_NAME`, `backup-db.sh` | `config.py` `get_settings()`, `database.py` `get_db_connection()` | 볼륨을 만든 뒤 바꿈 → 종료 3 `database does not exist (check DB_NAME)` |
| `POSTGRES_PASSWORD` | **필수** | 무작위(`init-secrets.sh`) | postgres 슈퍼유저 (새 볼륨을 만들 때만 적용) | compose `${…:?}` | 비움 → `docker compose`가 `required variable POSTGRES_PASSWORD is missing a value: set POSTGRES_PASSWORD in deploy/.env …`로 시작하지 않음. 컨테이너 안 유닉스 소켓 접속(`docker compose exec postgres psql -U postgres`)에는 쓰이지 않음 |
| `DB_OWNER_PASSWORD` | **필수** | 무작위 | `vc_owner` 역할(새 볼륨에서 만듦), migrate의 `DB_PASSWORD` | compose `${…:?}`, `config.py` `db_password()` | 비움 → compose 오류. 볼륨을 만든 뒤 이 파일만 바꿈 → migrate `마이그레이션 실패: DB에 접속하지 못했습니다: OperationalError: password authentication failed (check DB_USER and DB_PASSWORD)` (바꾸는 법: README "비밀번호 바꾸기") |
| `DB_APP_PASSWORD` | **필수** | 무작위 | `vc_app` 역할, backend의 `DB_PASSWORD` | compose `${…:?}`, `config.py` `get_settings()`·`db_password()` | 비움 → compose 오류. `password` → 종료 1. 역할 비밀번호와 다름 → 종료 3 `password authentication failed (check DB_USER and DB_PASSWORD)` |
| `AI_SERVICE_TOKEN` | **필수** | 무작위(32바이트 이상) | backend·ai 둘 다 같은 값 | `config.py` `get_settings()`(다른 비밀값과 비교), `BE/app/integrations/ai_service.py` `_settings()`, `ai/src/server/app.py` `require_service_token()` | 비움 → compose 오류. 32바이트 미만·공백/줄바꿈 → AI 기능 503 `AI_SERVICE_NOT_CONFIGURED`(BE), `/v1/*` 503 `AI_SERVICE_TOKEN_NOT_CONFIGURED`(AI). 다른 비밀값과 같음 → 종료 1. 바꾼 뒤 `docker compose up -d ai backend` |
| `ACME_WEBROOT` | 선택 | 비움 → `/var/www/certbot` (`setup-server.sh`가 만듦) | nginx 볼륨, `issue-cert.sh` | — | 두 곳이 같은 값을 읽으므로 바꾸면 `deploy.sh`로 nginx도 다시 만듦(그 전에는 `/.well-known/acme-challenge/`가 404) |

새 DB 볼륨을 만든 뒤에는 `POSTGRES_PASSWORD`·`DB_OWNER_PASSWORD`·`DB_APP_PASSWORD`가 이 파일에만 남으므로 `backend.env`와 함께 서버 밖에도 백업합니다.

---

## 2. `deploy/backend.env` (BE만)

`APP_ENV`·`DB_*`·`PGSSL*`·`AI_SERVICE_URL`·`AI_SERVICE_ALLOW_HTTP`·`AI_SERVICE_TOKEN`·`FORWARDED_ALLOW_IPS`는 compose가 정하므로 여기 넣어도 무시됩니다(4번).

| 변수 | 필수 | 이 구성의 값 | 검사하는 코드 | 틀리면 |
|---|---|---|---|---|
| `JWT_SECRET` | **필수** | 무작위(`init-secrets.sh`, 48바이트 hex) | `config.py` `get_settings()` | 32바이트 미만·저장소의 테스트 값·서로 다른 바이트 8개 미만 → 종료 1. 바꾸면 모든 사용자가 한 번 로그아웃 |
| `JWT_EXPIRES_MINUTES` | 선택 | 넣지 않음(1440) | `get_settings()` `_env_int` | 1~1440 밖 → 종료 1 `JWT_EXPIRES_MINUTES must be an integer between 1 and 1440` |
| `HOLDER_KEY_ENCRYPTION_KEY` | **필수** | 무작위 base64 32바이트 | `get_settings()`, `BE/app/services/key_custody.py` `parse_master_key()` | 없음·공개 테스트 키 → 종료 1. 형식 오류 → 종료 1 `HOLDER_KEY_ENCRYPTION_KEY must be base64` / `HOLDER_KEY_ENCRYPTION_KEY must decode to 32 bytes`(`NAME=` 접두사를 붙인 값은 보통 뒤쪽). 바꾸거나 잃으면 기존 홀더 키 복호화 불가(서버 밖에 백업) |
| `SERVICE_API_KEY` | **넣지 않음** | — | `get_settings()` | 넣으면 모든 DID를 읽는 키가 됨 |
| (비밀값끼리 같은 값) | — | 용도마다 따로 생성 | `get_settings()` | 두 값이 같으면 종료 1 `<이름> (not the same value as <다른 이름>)` (`.env`의 `AI_SERVICE_TOKEN` 포함) |
| `DXWORKS_API_HOST` | **조건부** | `https://<협력사 호스트>` | `get_settings()`, `BE/app/integrations/dxworks.py` `is_valid_host_setting()` | 비루프백 `http://`·사용자 정보·쿼리·공백 → 종료 1. 비움 → 그 기능만 503 `PROVIDER_NOT_CONFIGURED`. 나가는 길 없음 → 502 `DXWorks 연결 실패: ConnectTimeout`(약 15초) |
| `DXWORKS_API_KEY` | **조건부** | 협력사 키 | `get_settings()` | ASCII 아님 → 종료 1. 비움 → 발급·검증 503 |
| `DXWORKS_BADGE_TEMPLATE_ID` | **조건부**(모의 발급) | 협력사 안내서의 템플릿 ID | `get_settings()` | 비움 → 허용 템플릿 없음(403) |
| `DXWORKS_TIMEOUT_SECONDS` | 선택 | 넣지 않음(15) | `get_settings()` `_env_float` | 1~120 밖 → 종료 1. 최악 4배(기본 60초). 31초보다 크면 4배가 nginx `proxy_read_timeout`(125초)을 넘어 nginx가 먼저 504 |
| `MOCK_ISSUER_ENABLED` | 시연이면 **필수** | `true` (예시 파일 값) | `get_settings()` | 없음 → production에서 꺼짐(404 `Mock issuer is disabled`). 켜면 시작 로그에 경고 한 줄 |
| `MOCK_ISSUER_TEMPLATE_IDS` / `MOCK_ISSUER_DAILY_LIMIT` / `MOCK_ISSUER_GLOBAL_DAILY_LIMIT` | 선택 | 비움 / 10 / 시연 인원 × 10보다 조금 크게(기본 200) | `get_settings()` | 한도 1 미만 → 종료 1 |
| `IDENTITY_VERIFICATION_ENABLED` | 넣지 않음 | (false) | `get_settings()` | 켜면 사용자가 보낸 값에 회사 API 키가 붙어 나감 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | **도메인 뒤** | 콘솔 값 / 콘솔 값 / `https://<도메인>/auth/google/callback` | `get_settings()`, `BE/app/api/oauth.py` | 하나만·콜백 없음·`http://` → 종료 1. FE가 보내는 주소(`window.location.origin` + 경로)와 글자가 다르면 400 `Invalid redirect_uri` |
| `GOOGLE_ALLOWED_REDIRECT_URIS` | 넣지 않음 | — | `get_settings()` | Google 로그인을 켰을 때(`GOOGLE_CLIENT_ID`·`GOOGLE_CLIENT_SECRET` 중 하나라도) `https://` 아닌 항목 → 종료 1. Google을 끈 채면 검사하지 않고 쓰지도 않음 |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` / `GITHUB_REDIRECT_URI` | **도메인 뒤** | 콘솔 값 / 콘솔 값 / `https://<도메인>/auth/github/callback` | `get_settings()` | 위와 같은 규칙 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` / `NAVER_REDIRECT_URI` | **도메인 뒤** | 콘솔 값 / 콘솔 값 / `https://<도메인>/auth/naver/callback` | `get_settings()` | 위와 같은 규칙 |
| `OAUTH_TIMEOUT_SECONDS` | 선택 | 넣지 않음(10) | `config.py` `_oauth_timeout_seconds()` | 1~120 밖 → 종료 1 |
| `RESEARCHER_USER_IDS` | 선택 | 관리자(연구자) 계정 UUID (README ⑥) | `get_settings()` | — |
| `CURRICULUM_LLM_USER_IDS` | 선택 | `*` (예시 파일 값, 로그인한 모든 계정) 또는 계정 UUID | `get_settings()` | 비움 → 아무도 LLM 모드를 못 씀 |
| `CURRICULUM_LLM_DAILY_LIMIT` | 선택 | 넣지 않음(20) | `get_settings()` `_env_int`, `BE/app/services/career.py` `reserve_llm_generation()` | 1 미만·정수 아님 → 종료 1 `CURRICULUM_LLM_DAILY_LIMIT must be an integer >= 1`. 허용된 계정 하나의 24시간 LLM 모드 학습 계획 수(저장된 LLM 계획은 DB로, 외부 LLM(Claude)까지 간 실패한 시도는 BE 프로세스 메모리로 셈). 넘으면 429 `Daily LLM curriculum limit reached. …` + `Retry-After` |
| `CURRICULUM_LLM_GLOBAL_DAILY_LIMIT` | 선택 | 넣지 않음(200) | `get_settings()` `_env_int`, `career.py` `_reserve_global_llm_generation()` | 1 미만·정수 아님 → 종료 1. 모든 계정을 합친 24시간 LLM 모드 학습 계획 수. 넘으면 429 `Daily LLM curriculum service limit reached. …` + `Retry-After`, 감사 로그 `event=llm_service_limit_denied` |
| `AI_TIMEOUT_SECONDS` / `AI_GENERATION_TIMEOUT_SECONDS` | 선택 | 넣지 않음(30 / 90) | `get_settings()` `_env_float` | 1~600 밖 → 종료 1. 125초 가까이 올리면 nginx `proxy_read_timeout`(125초)에 먼저 끊김(504) |
| `AI_MAX_CONCURRENCY` | 선택 | 넣지 않음(4) | `get_settings()` | 1 미만 → 종료 1. 사용자 한 명은 그중 2자리·분당 30번·시간당 300번까지(코드 고정, 넘으면 429 `Too many analysis requests. Try again later.`) |
| `DB_CONNECT_TIMEOUT` | 선택 | 넣지 않음(10) | `get_db_connection()` | 시작 DB 점검의 시도마다 이 시간까지 기다림 |
| `DB_MAX_CONNECTIONS` | 선택 | 넣지 않음(20) | `config.py` `db_max_connections()` | 2 미만 → 종료 1. 이 값 + migrate·운영자 접속이 postgres `max_connections`(기본값, 보통 100)를 넘으면 `too many connections`(시작 때면 종료 3, 실행 중이면 503 `Service is busy`) |
| `PGOPTIONS` | 넣지 않음 | — | `database.py` | 앱 세션 제한(15초·5초·30초)이 뒤에 붙어 우선 |
| `CORS_ORIGINS` | **넣지 않음** | 같은 출처라 불필요 | `get_settings()` | `https://<도메인>/`(끝 `/`)·`*` → 종료 1 `CORS_ORIGINS (scheme://host[:port] entries only: …)` |
| `LOG_LEVEL` | 선택 | 넣지 않음(INFO) | `config.py` `log_level()`, `BE/main.py` | `DEBUG`·`INFO`·`WARNING`·`ERROR`·`CRITICAL`(대소문자 무관, 빈 값은 INFO)만 받음. 그 밖(`verbose`·`NOTSET`·`WARN` 등) → 종료 1 `LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL (empty means INFO)`. `DEBUG` → httpx가 DXWorks 전체 URL을 로그에 남김. `WARNING` 이상 → 감사 로그 없음 |
| `UVICORN_*` (`UVICORN_RELOAD`, `UVICORN_TIMEOUT_KEEP_ALIVE` 등) | **넣지 않음** | — | `BE/main.py`, uvicorn(`auto_envvar_prefix="UVICORN"`) | 리로더가 켜지는 등 실행 옵션이 바뀜. nginx는 backend와 연결을 유지하지 않으므로 keep-alive도 맞출 값이 없음 |
| `PORT` | 의미 없음 | — | `BE/Dockerfile` CMD가 3000 고정 (`python main.py`에서만 읽음) | — |
| `AUTH_REQUIRED`, `KAKAO_*` | 넣지 않음 | — | `get_settings()` | 경고만 |

---

## 3. `deploy/ai.env` (AI만)

`AI_SERVICE_TOKEN`은 compose가 `.env`의 값을 넣으므로 여기 넣어도 무시됩니다. AI는 `LOG_LEVEL`을 읽지 않습니다. AI의 `/docs`는 켜져 있지만 ai는 포트를
열지 않아 밖에서 닿지 않습니다.

| 변수 | 필수 | 값 | 검사하는 코드 | 틀리면 |
|---|---|---|---|---|
| `CURRICULUM_LLM_ENABLED` | 선택 | `false` (LLM을 켤 때만 `true`) | `ai/src/career/claude_client.py`, `gemini_client.py`, `llm_client.py`, `local_selector.py` | `true`가 아니면 LLM 모드 요청만 503 `GENERATOR_CONFIGURATION_REQUIRED`(템플릿 계획은 됨) |
| `ANTHROPIC_API_KEY` | 조건부(LLM) | Claude Console(platform.claude.com) > API keys에서 만든 키 | `claude_client.py` `ClaudeConfig.from_env()` (요청마다) | 없음·공백·줄바꿈·비ASCII → LLM 모드만 503 `GENERATOR_CONFIGURATION_REQUIRED`. 키가 틀림 → 422 `LLM_PROVIDER_REJECTED`, AI 로그 `Claude API rejected the request: status=401 …`. 값은 `x-api-key` 헤더에만 쓰이고 로그·응답에 나오지 않음 |
| `CURRICULUM_LLM_PROVIDER` | 넣지 않음 | 비움 → `claude` | `llm_client.py` `generator_from_env()` | 그 밖의 값 → LLM 모드만 503. `gemini`(Google Gemini API, 아래 `GEMINI_*` 행)와 `compatible`(OpenAI 호환, `CURRICULUM_LLM_BASE_URL`·`CURRICULUM_LLM_API_KEY`·`CURRICULUM_LLM_TOKEN_PARAMETER`, 키도 이 파일)도 받음. `local-transformers`는 이미지에 GPU 패키지가 없어 쓸 수 없음 |
| `CURRICULUM_LLM_MODEL` | 선택 | 넣지 않음 → `claude-sonnet-5-5` (`gemini`면 `gemini-3.5-flash-lite`) | `claude_client.py`, `gemini_client.py` | 모델 ID 형식 위반(Claude는 `claude-`로 시작하지 않는 값 포함) → LLM 모드만 503. 없는 모델 → 422(404) |
| `CURRICULUM_LLM_EFFORT` | 선택 | 넣지 않음 → `low` | `claude_client.py` | `low`·`medium`·`high`·`xhigh`·`max` 밖 → LLM 모드만 503 |
| `GEMINI_API_KEY` | `gemini` 공급자만 | Google AI Studio에서 만든 키(실제 사용자에게는 유료 등급) | `gemini_client.py` `GeminiConfig.from_env()` (요청마다) | 없음·공백·줄바꿈·비ASCII → LLM 모드만 503. 키가 틀림 → 422, AI 로그 `Gemini API rejected the request: status=401 …` |
| `CURRICULUM_LLM_THINKING_LEVEL` / `GEMINI_API_VERSION` | 넣지 않음(`gemini` 공급자만) | 모델 기본값 / `v1` | `gemini_client.py` | `minimal`·`low`·`medium`·`high` / `v1`·`v1beta` 밖 → LLM 모드만 503 |
| `CURRICULUM_LLM_MAX_CONCURRENCY` | 선택 | 넣지 않음(2) | `llm_client.py` `llm_max_concurrency()`, `app.py` `create_app()` | 1~32의 ASCII 숫자가 아님 → 시작(import) 때 종료 1 `CURRICULUM_LLM_MAX_CONCURRENCY must be an integer from 1 to 32`(한 줄). 넘치는 생성은 10초 뒤 BE 503 `LLM_BUSY` |
| `AI_MAX_BODY_BYTES` | 선택 | 넣지 않음(32 MiB) | `app.py` `max_body_bytes()`, `ServiceGuard.body_limit()` | 양의 정수가 아님 → 시작(import) 때 종료 1 `AI_MAX_BODY_BYTES must be a positive integer (bytes)`(한 줄). 공고·학습자료 묶음을 싣는 경로(`app.py` `CORPUS_ROUTES`)에만 이 값을 쓰고, 그 밖의 경로(공고 하나·자료 하나·미리보기)는 min(4 MiB, 이 값). 묶음이 이 값을 넘으면 BE에 413 |
| `CURRICULUM_LOCAL_ALLOW_DOWNLOAD` | 넣지 않음 | (false) | `local_selector.py` | — |
| `CAREER_AI_SKIP_DOTENV` | 불필요 | — | `app.py` (이미지에 `.env` 없음, `ai/.dockerignore`가 `**/.env`·`**/.env.*` 제외) | — |
| LLM 나가는 길 | 조건부(LLM) | `api.anthropic.com:443` (`gemini`면 `generativelanguage.googleapis.com:443`) | `claude_client.py`, `gemini_client.py` (프록시 환경변수·리디렉션 안 씀) | NCP ACG 아웃바운드에서 막힘 → 25초 뒤 503 `LLM_PROVIDER_UNAVAILABLE` |

---

## 4. `docker-compose.yml`에 고정된 값 (`backend.env`·`ai.env`에 넣어도 무시)

### backend (그리고 `scripts/import-market.sh`의 일회성 backend)

| 변수 | 값 | 검사하는 코드 | 틀리면 |
|---|---|---|---|
| `APP_ENV` | `production` (이미지 기본값과 같음) | `config.py` `_app_env()` | `development`면 운영 검사·시작 DB 점검·HSTS 꺼짐, `/docs` 노출, 모의 발급 기본 켜짐 |
| `DB_HOST` / `DB_PORT` | `postgres` / `5432` | `get_settings()`, `database.py` `get_db_connection()` | 다른 이름 → 종료 3 `server certificate does not match the host name (set DB_HOST to the RDS endpoint name, not an IP or CNAME)`(서버 인증서 이름이 `postgres`). 닿지 않음 → 종료 3 `connection refused (check DB_HOST and DB_PORT)`·`host name not resolved (check DB_HOST)` |
| `DB_NAME` | `${DB_NAME:-vc_career_agent}` | 위와 같음 | 1번 표 |
| `DB_USER` / `DB_PASSWORD` | `vc_app` / `${DB_APP_PASSWORD}` | `get_settings()`, `config.py` `db_password()` | 없는 역할 → 종료 3 `role does not exist (check DB_USER)`. 권한 없음 → 종료 3 `InsufficientPrivilege: permission denied on table users (run the migration task with DB_APP_ROLE set to this DB_USER)`(migrate가 `DB_APP_ROLE` 없이 돈 것). 표 없음 → 종료 3 `UndefinedTable: table users not found (run the migration task first)` |
| `PGSSLMODE` | `verify-full` | `config.py` `_db_tls_problems()`·`db_tls_options()` | 없음·빈 값·`require`·`prefer`·`VERIFY-FULL`(대소문자 그대로 비교) → 종료 1 `PGSSLMODE (verify-full or verify-ca in production)`. TLS 없이 접속하면 postgres가 거절 → 종료 3 `connection rejected by pg_hba.conf (check PGSSLMODE: RDS requires SSL)` |
| `PGSSLROOTCERT` | `/run/vc-db-tls/ca.crt` (`deploy/secrets/db-tls/ca.crt`를 읽기 전용으로 붙임, `init-secrets.sh`가 만든 서버 전용 CA) | `_db_tls_problems()` (적힌 그대로 읽을 수 있는 파일인지) | 파일 없음(`ca.crt` 없이 `up`을 실행해 Docker가 빈 디렉터리를 만든 경우 포함)·디렉터리 → 종료 1 `PGSSLROOTCERT (readable CA bundle file)`. 서버 인증서를 서명한 CA가 아님(`secrets/db-tls/`를 일부만 다시 만듦) → 종료 3 `server certificate verification failed (check PGSSLROOTCERT: RDS CA bundle)`. 이미지 기본값 `/app/certs/rds-global-bundle.pem`(RDS CA)은 이 구성에서 쓰지 않음 |
| `AI_SERVICE_URL` | `http://ai:8100` | `get_settings()`, `ai_service.py` `_settings()` | ai가 떠 있지 않음 → AI 기능 503 `AI_SERVICE_UNAVAILABLE` |
| `AI_SERVICE_ALLOW_HTTP` | `true` (같은 서버 안 Docker 네트워크라 평문 허용) | `ai_service.py` `_settings()` | `false` → 시작은 하고 AI 기능 503 `AI_SERVICE_NOT_CONFIGURED` |
| `AI_SERVICE_TOKEN` | `${AI_SERVICE_TOKEN}` | 1번 표 | 1번 표 |
| `FORWARDED_ALLOW_IPS` | `"*"` (backend는 포트를 열지 않아 nginx만 닿음. nginx가 `X-Forwarded-For`를 실제 접속 IP로 덮어씀) | uvicorn 0.54.0 `uvicorn/config.py` | 없음 → 접근 로그의 클라이언트 IP가 nginx 컨테이너 주소. 앱 동작 영향 없음 |
| 그 밖 | `env_file: backend.env`(없으면 compose 오류), 네트워크 `edge`·`backend`·`db`, 포트 없음, 메모리 1.5 GB, `stop_grace_period: 120s`, `read_only` + `/tmp` tmpfs, `cap_drop: ALL`, `no-new-privileges`, 상태 확인은 이미지 `HEALTHCHECK`(`/health`, 30초) | — | — |

### migrate (같은 BE 이미지, `deploy.sh`마다 backend보다 먼저 한 번)

| 변수/설정 | 값 | 검사하는 코드 | 틀리면 |
|---|---|---|---|
| 명령 | `["python", "-m", "app.migrate"]` (`--seed` 없음) | `BE/app/migrate.py` `main()` | `--seed` → production에서 접속 전에 거부(종료 1) |
| `APP_ENV` | `production` | `migrate.py` `_production()`(`development`가 아니면 운영 검사), `config.py` `_app_env()` | 비우면 운영 검사(시드 해시 계정 검사·TLS 검사)를 건너뜀 |
| `DB_HOST` / `DB_PORT` / `DB_NAME` | backend와 같음 | `database.py` `get_db_connection()` | 연결 거부·시간 초과·DB 시작 중 → 60초 동안 2초마다 다시 시도(`DB 연결 대기 중: <요약>`) 뒤 종료 1 |
| `DB_USER` / `DB_PASSWORD` | `vc_owner` / `${DB_OWNER_PASSWORD}` (DB와 테이블 소유 계정, 슈퍼유저 아님) | `config.py` `db_password()`, `migrate.py` `_connect_waiting()` | 빈 값·`password` → 접속하지 않고 종료 1. 틀림 → 기다리지 않고 종료 1 `마이그레이션 실패: DB에 접속하지 못했습니다: OperationalError: password authentication failed (check DB_USER and DB_PASSWORD)` |
| `DB_APP_ROLE` | `vc_app` | `migrate.py` `grant_app_role()`·`check_app_role()` | 역할 없음·특권 소속 등 → 종료 1(`마이그레이션 실패:` 줄, 고치는 법은 CHECKLIST "DB 마이그레이션"). 비우면 권한을 주지 않아 BE가 종료 3 `InsufficientPrivilege: …` |
| `PGSSLMODE` / `PGSSLROOTCERT` | backend와 같음 | `config.py` `db_tls_options()` (접속할 때마다) | 접속하기 전에 종료 1, 한 줄 `ERROR:app.migrate:마이그레이션 실패: APP_ENV=production requires PGSSLMODE (verify-full or verify-ca in production)…`(60초 대기 없음). `DB_PASSWORD`도 틀렸으면 그것을 먼저 알림 |
| (접속 오류) | — | `migrate.py` `_connect_waiting()`, 요약은 시작 점검의 `describe_error()` | 인증서 확인 실패·호스트 이름 불일치·비밀번호·없는 역할·DB·`pg_hba.conf` → 기다리지 않고 종료 1. 줄에는 DB 주소·IP·사용자 이름·인증서 경로가 없음 |
| 앱 비밀값(`JWT_SECRET` 등) | 없음(`env_file` 없음) | 실행기는 `get_settings()`를 부르지 않음 | — |
| 그 밖 | `restart: "no"`, 네트워크 `db`만, `read_only`·`cap_drop: ALL` | — | 실패하면 backend가 뜨지 않음(`service "migrate" didn't complete successfully`) |

`scripts/import-market.sh`는 backend 서비스를 `docker compose run --rm --no-deps`로 한 번 띄워 `python -m app.jobs.market import --dir /app/market_snapshots`를
실행합니다(위 backend 값 그대로, `vc_app` 권한으로 충분, `ai/data/market_snapshots`를 읽기 전용으로 붙임). 인자 오류는 종료 2, 파일 없음·권한 부족 등은
`실패: …` 한 줄과 종료 1입니다(`BE/app/jobs/market.py` `main()`).

### ai · postgres · nginx

| 서비스 | 고정 값 | 비고 |
|---|---|---|
| ai | `AI_SERVICE_TOKEN: ${AI_SERVICE_TOKEN}`, `env_file: ai.env`, 네트워크 `backend`만, 포트 없음, 메모리 1 GB, `stop_grace_period: 120s`, 하드닝은 backend와 같음 | 상태 확인은 이미지 `HEALTHCHECK`(8100 `/health`). backend는 ai가 healthy일 때 시작 |
| postgres | 이미지 `pgvector/pgvector:0.8.7-pg16-bookworm`, `POSTGRES_USER=postgres`, entrypoint `postgres/start.sh`(TLS 키를 옮기고 `ssl=on`, 최소 TLS 1.2, `hba_file=postgres/pg_hba.conf`), `initdb/10-roles.sh`(새 볼륨에서 `vc_owner`·`vc_app`·DB 생성), 볼륨 `postgres_data`, 네트워크 `db`(internal)만, 메모리 768 MB | `pg_hba.conf`: 유닉스 소켓은 `trust`(컨테이너 안, 서버의 root·docker 그룹만), TCP는 `hostssl` + `scram-sha-256`만. `max_connections`는 따로 정하지 않음(PostgreSQL 기본값) |
| nginx | 포트 `80:8080`·`443:8443`(compose에서 여는 유일한 포트), `SERVER_NAME: ${SERVER_NAME:-_}`, `deploy/secrets/tls`(인증서) · `${ACME_WEBROOT:-/var/www/certbot}` 읽기 전용, 네트워크 `edge`만, 메모리 128 MB, `cap_drop: ALL` | backend가 healthy일 때 시작 |
| 공통 | 로그 `json-file` 10 MB × 5개, `restart: unless-stopped`(migrate 제외) | 컨테이너를 다시 만들면 그 전 로그는 함께 지워짐 |

---

## 5. nginx · NCP ACG · DNS · certbot (환경변수는 아니지만 배포 실패와 직결)

| 항목 | 값 | 틀리면 |
|---|---|---|
| NCP ACG 인바운드 | TCP 22 ← 운영자 공인 IP만, TCP 80·443 ← `0.0.0.0/0` | 80이 막힘 → `issue-cert.sh`의 확인과 Let's Encrypt 인증·갱신 실패. 443이 막힘 → 사이트가 열리지 않음 |
| NCP ACG 아웃바운드 | 전체 허용, 또는 최소 TCP 80·443과 UDP 53·123 | 빌드 실패(Docker Hub·PyPI·npm), DXWorks 502 `ConnectTimeout`, 소셜 로그인 502, LLM 모드(Claude API) 503 `LLM_PROVIDER_UNAVAILABLE`, 인증서 발급 실패 |
| ufw (`setup-server.sh`) | 22·80·443 허용 | Docker가 연 포트(80·443)는 ufw와 별개로 열림. compose에 `ports:`를 더하지 않음 |
| DNS | `<도메인>` A 레코드 → 서버 공인 IP (`dig +short <도메인>`) | `issue-cert.sh`가 `http://<도메인> does not reach this server's nginx yet.`로 멈춤 |
| certbot | `certbot certonly --webroot -w /var/www/certbot -d <도메인> --deploy-hook deploy/scripts/install-cert.sh` (`issue-cert.sh`가 실행), 갱신은 certbot systemd 타이머 | `install-cert.sh`가 `deploy/secrets/tls/`(`fullchain.pem` 644, `privkey.pem` 600, uid 101)로 복사하고 nginx를 다시 시작. 서버가 자기 공인 IP로 돌아오지 못하면 확인만 실패 → `SKIP_PROBE=1` |
| HTTP/HTTPS 모드 | `nginx/40-vc-site.sh`: `SERVER_NAME`이 도메인이고 `/etc/nginx/tls`에 인증서가 있으면 `vc/site-https.conf.template`, 아니면 `vc/site-http.conf.template` | 인증서를 받은 뒤에도 HTTP면 `docker compose restart nginx` 후 로그의 `40-vc-site: serving …` 줄 |
| TLS | TLS 1.2·1.3(`vc/tls.conf`), 443에 다른 이름(IP 등)으로 오면 핸드셰이크 거절, 80은 `/.well-known/acme-challenge/` 외에 `https://<도메인>`으로 301 | — |
| IP별 요청 제한 | `http.conf`: `vc_slow` 20r/m, `vc_fast` 60r/m(IP + 경로 이름 키). `vc/app.conf`: `burst=20`·`burst=60` `nodelay` | 넘으면 429 `{"detail":"Too many attempts. Try again later."}` + `Retry-After: 60`. 경로 목록은 CHECKLIST "HTTPS·네트워크" |
| 본문 크기 | `client_max_body_size 3m` | 넘으면 nginx 413. BE 경로별 상한(최대 2 MiB)보다 작게 두지 않음 |
| `/api/*` 프록시 | `proxy_pass http://backend:3000`(변수, Docker DNS `127.0.0.11`), 연결 5초·보내기/읽기 125초, `proxy_next_upstream off`, `X-Forwarded-For`·`X-Real-IP` = `$remote_addr` | 125초보다 오래 걸리면 504. backend를 다시 만드는 동안 502 |
| 정적 파일 | `index.html`·SPA 경로 `no-cache`, `/assets/` 1년 `immutable`, 점 있는 파일 1시간, 점 없는 경로는 `index.html`(200) | — |
| 보안 헤더 | `vc/security-headers.conf`(HSTS는 HTTPS 모드에서만, CSP 값은 CHECKLIST "FE") | CSP에서 `https://cdn.jsdelivr.net`을 빼면 Pretendard 대신 시스템 글꼴 |

---

## 6. 제공자 콘솔 (콜백은 모두 FE 주소 `https://<도메인>/auth/<제공자>/callback`, 도메인 뒤)

`backend.env`의 `*_REDIRECT_URI`와 콘솔 값은 글자 그대로 같아야 합니다(다르면 400 `Invalid redirect_uri`). 운영용 클라이언트에는 localhost 주소를 넣지
않습니다.

| 제공자 | 설정 | 이 구성의 값 | 제약 · 주의 |
|---|---|---|---|
| Google (OAuth 클라이언트: 웹 애플리케이션) | 승인된 리디렉션 URI | `https://<도메인>/auth/google/callback` | HTTPS·도메인만(IP·`http://` 안 됨). BE는 `openid email profile`만 요청 |
| | 동의 화면 게시 상태 | 테스트(테스트 사용자 최대 100명에 시연 계정 등록) 또는 게시 | 테스트 상태에서는 등록한 사용자만 로그인. BE는 refresh token을 받지 않음(`access_type=offline` 없음) |
| GitHub (OAuth App) | Homepage URL / Authorization callback URL | `https://<도메인>` / `https://<도메인>/auth/github/callback` | 정확히 일치(와일드카드 끔), 앱당 콜백 10개, PKCE S256, Expire user access tokens 켬, Device Flow 끔, 조직 소유로 만들지 않음 |
| 네이버 | 서비스 URL / Callback URL / 제공 정보 | `https://<도메인>` / `https://<도메인>/auth/naver/callback` / 이메일 | 검수 전에는 관리자·멤버로 등록한 아이디만 로그인 |

---

## 7. FE 빌드 (nginx 이미지 안, `nginx/Dockerfile`)

| 변수/조건 | 값 | 검사하는 코드 | 틀리면 |
|---|---|---|---|
| Node.js | `node:22-bookworm-slim`(22.12 이상 필요), `npm ci && npm run build` | `FE/package.json` | 낮은 Node → 빌드 실패 |
| `VITE_API_BASE_URL` | **빈 값**(Dockerfile이 고정) → 같은 출처 `/api` | `FE/src/lib/apiClient.ts` | 다른 출처 주소 → CORS·CSP `connect-src`도 바꿔야 함 |
| `FIGMA_CAPTURE` | 넣지 않음 | `FE/vite.config.ts` (개발 서버 전용) | — |
| 도메인 | 불필요(API는 상대 경로, OAuth 콜백은 `window.location.origin`) | `FE/src/lib/oauth.ts` | — |

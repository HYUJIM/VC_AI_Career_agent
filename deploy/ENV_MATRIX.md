# 환경변수·설정 표 (도메인 없는 CloudFront 구성)

[README.md](README.md)의 구성(비공개 S3 + CloudFront(OAC), `/api/*` → VPC 원본 → 내부 ALB(HTTP:80) → BE(Fargate, 3000), AI(Fargate,
8100)는 ECS Service Connect, RDS for PostgreSQL 16, Secrets Manager, CloudWatch Logs, 리전 `ap-northeast-2`)에서 태스크·빌드·콘솔마다
넣을 값입니다. `<CF>`는 CloudFront 기본 주소 `https://dXXXXXXXXXXXXX.cloudfront.net`(배포판을 만든 뒤에야 알 수 있음)입니다.

표기
- 필수: **필수** / **조건부**(그 기능을 쓸 때) / **2단계**(CloudFront 주소가 나온 뒤, README ⑪) / 선택 / **넣지 않음**
- 종류: **Secret** = 작업 정의 `secrets`(Secrets Manager, `비밀 이름:키`), Plain = 작업 정의 `environment`, 이미지 = Dockerfile `ENV` 기본값
- 검사하는 코드: 저장소 기준 파일과 함수. `config.py` = `BE/app/core/config.py`, `database.py` = `BE/app/services/database.py`,
  시작 점검 = `BE/app/services/db_startup_check.py`
- "종료 1": 운영 설정 검사·범위 검사가 실패하면 BE는 stderr에 그 메시지 **한 줄**(`APP_ENV=production requires …`처럼 변수 이름만,
  값 없음)을 남기고 종료 코드 1로 끝납니다(`BE/main.py`가 불러올 때). "종료 3": 시작 DB 점검 실패
  (`DB startup check failed: <종류>: <요약>` 한 줄 + `Application startup failed. Exiting.`). 둘 다 배포 회로 차단기가 롤백합니다.
- Secrets Manager에는 **값만** 넣습니다(`NAME=` 접두사·끝 줄바꿈 없음, README ④).

---

## 1. BE 서비스 태스크 (`vc-be`, [ecs/be-service.taskdef.json](ecs/be-service.taskdef.json))

| 변수 | 필수 | 이 구성의 값 | 종류 · 출처 | 검사하는 코드 | 틀리면 |
|---|---|---|---|---|---|
| `APP_ENV` | 필수 | `production` (이미지 기본값, 작업 정의에도 명시) | Plain · 이미지 | `config.py` `_app_env()` | `prod` 같은 값 → 종료 1 `APP_ENV must be one of: development, production (empty means development)`. `development` → 운영 검사·시작 DB 점검·HSTS 꺼짐, `/docs` 노출, 모의 발급 기본 켜짐 |
| `JWT_SECRET` | **필수** | `secrets.token_urlsafe(48)` | **Secret** `vc/be-app:JWT_SECRET` | `get_settings()` | 32바이트 미만·저장소의 테스트 값·서로 다른 바이트 8개 미만 → 종료 1. 태스크마다 다르면 서로의 토큰을 거절 |
| `JWT_EXPIRES_MINUTES` | 선택 | 넣지 않음(1440) | Plain | `get_settings()` `_env_int` | 1~1440 밖 → 종료 1 `JWT_EXPIRES_MINUTES must be an integer between 1 and 1440` |
| `HOLDER_KEY_ENCRYPTION_KEY` | **필수** | `base64(os.urandom(32))` | **Secret** `vc/be-app:HOLDER_KEY_ENCRYPTION_KEY` | `get_settings()`, `BE/app/services/key_custody.py` `parse_master_key()` | 없음·공개 테스트 키 → 종료 1. 형식 오류(`NAME=` 접두사 포함) → 시작 실패(`ValueError` traceback, 한 줄이 아님). 바꾸거나 잃으면 기존 홀더 키 복호화 불가 |
| `SERVICE_API_KEY` | **넣지 않음** | — | — | `get_settings()` | 넣으면 모든 DID를 읽는 키가 됨 |
| (비밀값끼리 같은 값) | — | 용도마다 따로 생성 | — | `get_settings()` | 두 값이 같으면 종료 1 `<이름> (not the same value as <다른 이름>)` |
| `DB_HOST` | **필수** | `<RDS_ENDPOINT>` (엔드포인트 이름 그대로) | Plain | `get_settings()`, `database.py` `get_db_connection()` | 비면 종료 1. IP·CNAME → 종료 3 `server certificate does not match the host name (…)`. 오타 → 종료 3 `host name not resolved (check DB_HOST)` |
| `DB_PORT` | 선택 | `5432` | Plain | `get_db_connection()` | 숫자 아님 → 종료 3 `ValueError: DB_PORT and DB_CONNECT_TIMEOUT must be integers` |
| `DB_NAME` | **필수** | `vc_career_agent` (RDS 초기 DB 이름과 같게) | Plain | `get_settings()`, `get_db_connection()` | 비면 종료 1. 없는 DB → 종료 3 `database does not exist (check DB_NAME)` |
| `DB_USER` | **필수** | `vc_app` (마스터 사용자 아님) | Secret `vc/db-app:username` | `get_settings()`, `get_db_connection()` | 비면 종료 1. 없는 역할 → 종료 3 `role does not exist (check DB_USER)` |
| `DB_PASSWORD` | **필수** | `vc_app` 비밀번호 | **Secret** `vc/db-app:password` | `get_settings()`, `config.py` `db_password()` | 비었거나 `password` → 종료 1. 틀림 → 종료 3 `password authentication failed (check DB_USER and DB_PASSWORD)` |
| `PGSSLMODE` | **필수** | `verify-full` | Plain | `config.py` `_db_tls_problems()`·`db_tls_options()` | 없음·빈 값·`require`·`prefer`·`VERIFY-FULL`(대소문자 그대로 비교) → 종료 1 `PGSSLMODE (verify-full or verify-ca in production)`. 앞뒤 공백은 뗌 |
| `PGSSLROOTCERT` | **넣지 않음** (이미지 기본값) | `/app/certs/rds-global-bundle.pem` ([BE/certs/README.md](../BE/certs/README.md)) | 이미지 | `_db_tls_problems()` (적힌 그대로 읽을 수 있는 파일인지) | 빈 값·공백만·앞뒤 공백·줄바꿈·없는 파일·디렉터리 → 종료 1 `PGSSLROOTCERT (readable CA bundle file)`. RDS CA가 없는 다른 묶음 → 종료 3 `server certificate verification failed (…)`. `system`은 안 됨(RDS CA는 공개 신뢰 저장소에 없음) |
| `DB_CONNECT_TIMEOUT` | 선택 | 넣지 않음(10) | Plain | `get_db_connection()` | 시작 DB 점검의 시도마다 이 시간까지 기다림 |
| `DB_MAX_CONNECTIONS` | 선택 | 넣지 않음(20) | Plain | `config.py` `db_max_connections()` | 2 미만 → 종료 1. 태스크 수 × 이 값이 RDS `max_connections`를 넘으면 `too many connections`(시작 때면 종료 3, 실행 중이면 500) |
| `PGOPTIONS` | 넣지 않음 | — | — | `database.py` | 앱 세션 제한(15초·5초·30초)이 뒤에 붙어 우선 |
| `CORS_ORIGINS` | **넣지 않음** | 같은 출처라 불필요 | — | `get_settings()` | `<CF>/`(끝 `/`)·`*` → 종료 1 `CORS_ORIGINS (scheme://host[:port] entries only: …)` |
| `AI_SERVICE_URL` | **필수** | `http://ai:8100` (AI의 client alias `ai`:8100과 짝) | Plain | `get_settings()`, `BE/app/integrations/ai_service.py` `_settings()` | 없음 → 기본 `http://127.0.0.1:8100`(BE 태스크 자신) → AI 기능 503. 별칭과 다름 → 503 `AI_SERVICE_UNAVAILABLE` |
| `AI_SERVICE_ALLOW_HTTP` | **필수** | `true` | Plain | `ai_service.py` `_settings()` | `false` → 시작은 하고 AI 기능 503 `AI_SERVICE_NOT_CONFIGURED`. 코드는 Service Connect TLS 여부를 모름(README ⑥ 선택지) |
| `AI_SERVICE_TOKEN` | **필수** | AI와 같은 값 | **Secret** `vc/ai-token` (평문 값 하나) | `get_settings()`(다른 비밀값과 비교), `ai_service.py` `_settings()` | 없음·32바이트 미만·공백/줄바꿈 → AI 기능 503 `AI_SERVICE_NOT_CONFIGURED`(시작은 함). AI 값과 다름 → 503 `AI_SERVICE_AUTH_FAILED`. 다른 비밀값과 같음 → 종료 1 |
| `AI_TIMEOUT_SECONDS` / `AI_GENERATION_TIMEOUT_SECONDS` | 선택 | 넣지 않음(30 / 90) | Plain | `get_settings()` `_env_float` | 1~600 밖 → 종료 1. Service Connect `perRequestTimeoutSeconds`(기본 15)나 CloudFront 원본 응답 제한 시간이 더 짧으면 그 시간에 끊김 |
| `AI_MAX_CONCURRENCY` | 선택 | 넣지 않음(4) | Plain | `get_settings()` | 1 미만 → 종료 1 |
| `DXWORKS_API_HOST` | **조건부** | `https://<협력사 호스트>` | **Secret** `vc/be-app:DXWORKS_API_HOST` (비공개 주소라 작업 정의에 드러내지 않음) | `get_settings()`, `BE/app/integrations/dxworks.py` `is_valid_host_setting()` | 비루프백 `http://`·사용자 정보·쿼리·공백 → 종료 1. 키를 빼면 그 기능만 503 `PROVIDER_NOT_CONFIGURED`. 나가는 길 없음 → 502 `DXWorks 연결 실패: ConnectTimeout`(약 15초) |
| `DXWORKS_API_KEY` | **조건부** | 협력사 키 | **Secret** `vc/be-app:DXWORKS_API_KEY` | `get_settings()` | ASCII 아님 → 종료 1. 비움 → 발급·신원 검증 503 |
| `DXWORKS_BADGE_TEMPLATE_ID` | **조건부**(모의 발급) | `<DXWORKS_BADGE_TEMPLATE_ID>` | Plain | `get_settings()` | 비움 → 허용 템플릿 없음(403) |
| `DXWORKS_TIMEOUT_SECONDS` | 선택 | 넣지 않음(15) | Plain | `get_settings()` `_env_float` | 1~120 밖 → 종료 1. 최악 4배(60초) > CloudFront 원본 응답 제한 기본 30초 → README ⑧에서 120초 |
| `MOCK_ISSUER_ENABLED` | 시연이면 **필수** | `true` | Plain | `get_settings()` | 없음 → production에서 꺼짐(404 `Mock issuer is disabled`). 켜면 시작 로그에 경고 한 줄 |
| `MOCK_ISSUER_TEMPLATE_IDS` / `MOCK_ISSUER_DAILY_LIMIT` / `MOCK_ISSUER_GLOBAL_DAILY_LIMIT` | 선택 | 비움 / 10 / 시연 인원 × 10보다 조금 크게 | Plain | `get_settings()` | 한도 1 미만 → 종료 1 |
| `IDENTITY_VERIFICATION_ENABLED` | 넣지 않음 | (false) | — | `get_settings()` | 켜면 사용자가 보낸 값에 회사 API 키가 붙어 나감 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | **2단계** | 콘솔 값 / 콘솔 값 / `<CF>/auth/google/callback` | Plain / **Secret** `vc/be-app:GOOGLE_CLIENT_SECRET` / Plain | `get_settings()`, `BE/app/api/oauth.py` | 하나만·콜백 없음·`http://` → 종료 1. FE가 보내는 주소(`window.location.origin` + 경로)와 글자가 다르면 400 `Invalid redirect_uri` |
| `GOOGLE_ALLOWED_REDIRECT_URIS` | 넣지 않음 | — | — | `get_settings()` | `https://` 아닌 항목 → 종료 1 |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` / `GITHUB_REDIRECT_URI` | **2단계** | 콘솔 값 / 콘솔 값 / `<CF>/auth/github/callback` | Plain / **Secret** / Plain | `get_settings()` | 위와 같은 규칙 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` / `NAVER_REDIRECT_URI` | **2단계** | 콘솔 값 / 콘솔 값 / `<CF>/auth/naver/callback` | Plain / **Secret** / Plain | `get_settings()` | 위와 같은 규칙 |
| `OAUTH_TIMEOUT_SECONDS` | 선택 | 넣지 않음(10) | Plain | `config.py` `_oauth_timeout_seconds()` | 1~120 밖 → 종료 1 |
| `LOG_LEVEL` | 선택 | 넣지 않음(INFO) | Plain | `config.py` `log_level()`, `BE/main.py` | 없는 이름(`verbose`) → 시작 실패(`ValueError: Unknown level` traceback, 한 줄이 아님). `DEBUG` → httpx가 DXWorks 전체 URL을 로그에 남김. `WARNING` 이상 → 감사 로그 없음 |
| `RESEARCHER_USER_IDS` / `CURRICULUM_LLM_USER_IDS` | 선택 | 가입 뒤 계정 UUID | Plain | `get_settings()` | — |
| `FORWARDED_ALLOW_IPS` | 권장 | `<VPC_CIDR>` (예 `10.0.0.0/16`) | Plain | uvicorn 0.54.0 `uvicorn/config.py`(기본 `127.0.0.1,::1`, CIDR 지원) | 없음 → 접근 로그의 클라이언트 IP가 ALB 주소. 앱 동작 영향 없음 |
| `UVICORN_TIMEOUT_KEEP_ALIVE` | 권장 | `130` (ALB 유휴 120초보다 길게) | Plain | uvicorn `uvicorn/main.py`(`auto_envvar_prefix="UVICORN"`, `--timeout-keep-alive`) | 기본 5초 → ALB 쪽 간헐 502 가능 |
| `UVICORN_RELOAD` 등 그 밖 `UVICORN_*` | **넣지 않음** | — | — | `BE/main.py`, uvicorn | 리로더가 켜짐 |
| `PORT` | 의미 없음 | — | — | `BE/Dockerfile` CMD가 3000 고정 (`python main.py`에서만 읽음) | — |
| `AUTH_REQUIRED`, `KAKAO_*` | 넣지 않음 | — | — | `get_settings()` | 경고만 |

작업 정의의 그 밖 값: `runtimePlatform` `LINUX`/`X86_64`(이미지를 `--platform linux/amd64`로 빌드), `cpu` 1024 / `memory` 2048,
포트 매핑 `be-http`(3000, appProtocol `http`), `readonlyRootFilesystem: true`, `stopTimeout: 120`, `healthCheck`(Dockerfile과 같은
python 명령, `startPeriod: 60`), `awslogs`(`/ecs/vc-be`, 접두사 `be`). 서비스는 Service Connect **클라이언트**(AI와 같은 네임스페이스),
ALB 대상 그룹 `ip`/3000, 상태 확인 유예 90초, 배포 회로 차단기·롤백. 실행 역할 `vc-be-execution`(`vc/be-app`·`vc/ai-token`·`vc/db-app`만
읽음), 태스크 역할은 정책 없음(앱이 AWS API를 쓰지 않음).

---

## 2. 마이그레이션 태스크 (`vc-be-migrate`, [ecs/be-migrate.taskdef.json](ecs/be-migrate.taskdef.json), BE 서비스와 **같은 이미지 태그**)

| 변수/설정 | 필수 | 값 | 종류 · 출처 | 검사하는 코드 | 틀리면 |
|---|---|---|---|---|---|
| 명령 | **필수** | `["python", "-m", "app.migrate"]` (`--seed` 금지) | — | `BE/app/migrate.py` `main()` | `--seed` → production에서 접속 전에 거부(종료 1) |
| `APP_ENV` | 필수 | `production` | Plain · 이미지 | `migrate.py` `_production()`(`development`가 아니면 운영 검사), `config.py` `_app_env()` | 비우면 운영 검사(시드 해시 계정 검사·TLS 검사)를 건너뜀 |
| `DB_HOST` / `DB_PORT` / `DB_NAME` | **필수** | 서비스와 같음 | Plain | `database.py` `get_db_connection()` | `DB_HOST` 없음 → `localhost`로 접속 시도, 60초 기다린 뒤 종료 1 |
| `DB_USER` / `DB_PASSWORD` | **필수** | **마스터 사용자** | **Secret** `<RDS 관리형 비밀>:username` / `:password` | `config.py` `db_password()` | 빈 값·`password` → 접속하지 않고 종료 1 |
| `DB_APP_ROLE` | **필수(운영)** | `vc_app` | Plain | `migrate.py` `grant_app_role()`·`check_app_role()` | 역할 없음·특권 소속 등 → 종료 1(`마이그레이션 실패:` 줄). 비우면 권한을 주지 않아 BE가 시작 DB 점검에서 `InsufficientPrivilege: permission denied on table users (run the migration task with DB_APP_ROLE set to this DB_USER)`로 종료 3 |
| `PGSSLMODE` | **필수** | `verify-full` | Plain | `config.py` `db_tls_options()` (접속할 때마다) | 없음·빈 값 등 → 접속하기 전에 종료 1, 한 줄 `ERROR:app.migrate:마이그레이션 실패: APP_ENV=production requires PGSSLMODE (verify-full or verify-ca in production)…`(60초 대기 없음). `DB_PASSWORD`도 틀렸으면 그것을 먼저 알림 |
| `PGSSLROOTCERT` | **넣지 않음** | 이미지 기본값 | 이미지 | 위와 같음 | 서비스와 같음 |
| `DB_CONNECT_TIMEOUT`, `LOG_LEVEL` | 선택 | 10 / INFO | Plain | `get_db_connection()`, `migrate.py` | — |
| 앱 비밀값(`JWT_SECRET` 등) | **넣지 않음** | — | — | 실행기는 `get_settings()`를 부르지 않음 | 불필요한 노출 |
| 네트워크 | **필수** | 앱용 서브넷 + `sg-be`, ECR·Logs·Secrets Manager 경로(NAT·공인 IP·엔드포인트) | — | — | 이미지·비밀을 못 받아 태스크 시작 실패 |

시장 통계 반입(선택, `python -m app.jobs.market import --dir /app/market_snapshots`)은 스냅샷 파일을 BE 이미지에 한 층 더한 배포용
이미지로 BE 서비스 작업 정의에 명령만 덮어써 실행합니다(루트 README "DB 마이그레이션" 마지막 항목). 같은 접근자(`app_db_connection`)를
쓰므로 `DB_*`와 `PGSSLMODE=verify-full`이 필요하고, CA 묶음과 `PGSSLROOTCERT`는 BE 이미지에서 물려받습니다.

---

## 3. AI 서비스 태스크 (`vc-ai`, [ecs/ai-service.taskdef.json](ecs/ai-service.taskdef.json), 공개 로드밸런서 없음)

| 변수/설정 | 필수 | 값 | 종류 · 출처 | 검사하는 코드 | 틀리면 |
|---|---|---|---|---|---|
| `AI_SERVICE_TOKEN` | **필수** | BE와 같은 값 | **Secret** `vc/ai-token` | `ai/src/server/app.py` `require_service_token()` (요청마다) | 32바이트 미만·없음 → AI는 healthy인데 `/v1/*` 503 `AI_SERVICE_TOKEN_NOT_CONFIGURED`(BE는 503 `AI_SERVICE_AUTH_FAILED`). BE와 다름 → 401(BE는 503 `AI_SERVICE_AUTH_FAILED`) |
| `AI_MAX_BODY_BYTES` | 선택 | 넣지 않음(32 MiB) | Plain | `app.py` `max_body_bytes()` | 양의 정수가 아님 → 시작(import) 실패, 태스크가 되풀이해 재시작 |
| `CURRICULUM_LLM_ENABLED` | 선택 | `false` | Plain | `ai/src/career/llm_client.py`, `local_selector.py` | — |
| `CURRICULUM_LLM_PROVIDER` / `_BASE_URL` / `_MODEL` / `_API_KEY` / `_TOKEN_PARAMETER` | 조건부(LLM) | `compatible` / `https://…` / 모델명 / **Secret**(`vc/ai-llm`) / `max_completion_tokens` | Plain / Secret | `llm_client.py` | 규칙 위반 → LLM 요청만 503. LLM 호스트로 나가는 길 필요. `local-transformers`는 이미지에 GPU 패키지가 없어 쓸 수 없음 |
| `CURRICULUM_LOCAL_ALLOW_DOWNLOAD` | 넣지 않음 | (false) | — | `local_selector.py` | — |
| `CAREER_AI_SKIP_DOTENV` | 불필요 | — | — | `app.py` (이미지에 `.env` 없음, `ai/.dockerignore`가 `**/.env`·`**/.env.*` 제외) | — |
| 포트 매핑 | **필수** | `name: ai-http`, `containerPort: 8100`, `appProtocol: http` | — | — | Service Connect 서비스를 만든 뒤에는 `appProtocol`을 바꿀 수 없음 |
| Service Connect(서버) | **필수** | `portName: ai-http`, `discoveryName: ai`, `clientAliases: [{dnsName: ai, port: 8100}]`, `timeout.perRequestTimeoutSeconds: 120`, (선택지 A) `tls` | — | — | 별칭 없음 → 이름이 `ai.<네임스페이스>`라 `http://ai:8100`이 안 풀림(`/career` 503). 제한 시간 기본 15초 → LLM 커리큘럼 끊김 |
| 리소스 | 권장 | `cpu` 512 / `memory` 1024 (프록시 몫 포함) | — | — | — |
| `healthCheck` | 권장 | `ai/Dockerfile`과 같은 python 명령(8100), `startPeriod: 30` | — | — | — |
| 보안 그룹 | **필수** | 인바운드 8100 ← `sg-be`만 | — | — | — |

AI는 `LOG_LEVEL`을 읽지 않습니다. AI의 `/docs`는 켜져 있지만 공개 로드밸런서가 없어 밖에서 닿지 않습니다. 실행 역할
`vc-ai-execution`은 `vc/ai-token`(LLM을 켜면 `vc/ai-llm`)만 읽습니다.

---

## 4. FE 빌드 (Vite, 빌드 때 번들에 들어감)

| 변수/조건 | 필수 | 값 | 검사하는 코드 | 틀리면 |
|---|---|---|---|---|
| Node.js | **필수** | 22.12 이상(CI와 같은 22), `npm ci && npm run build` | `FE/package.json` | 낮은 Node → 빌드 실패 |
| `VITE_API_BASE_URL` | 선택 | **비움** → 같은 출처 `/api` | `FE/src/lib/apiClient.ts` | 다른 출처 주소 → CORS·CSP `connect-src`도 바꿔야 함 |
| `FIGMA_CAPTURE` | 넣지 않음 | — | `FE/vite.config.ts` (개발 서버 전용) | — |
| CloudFront 주소 | 불필요 | FE는 주소를 몰라도 됨(API는 상대 경로, OAuth 콜백은 `window.location.origin`) | `FE/src/lib/oauth.ts` | — |

업로드: `assets/`(1년 캐시, `immutable`) → `favicon.svg` → `index.html`(`no-cache`) 순서, 무효화는 `/index.html` (README ⑨).

---

## 5. CloudFront · ALB · VPC · WAF 설정값 (환경변수는 아니지만 배포 실패와 직결)

| 항목 | 값 | 틀리면 |
|---|---|---|
| 배포판 인증서 | 기본 `*.cloudfront.net` (보안 정책은 `TLSv1`로 고정, 선택 불가) | — |
| 기본 동작(S3) | OAC, `Redirect HTTP to HTTPS`, `Managed-CachingOptimized`, 응답 헤더 정책(루트 README CSP), 뷰어 요청 함수 `spa-fallback.js` | 함수 없음 → 새로고침·OAuth 콜백·공유 링크 403 |
| `/api/*` 동작 | VPC 원본(내부 ALB), `HTTP only`:80, `Managed-CachingDisabled`, `Managed-AllViewerExceptHostHeader`, 모든 메서드, 함수 없음 | `Authorization`이 빠지면 로그인 직후 401로 로그아웃 |
| VPC 원본 응답 제한 시간 / 연결 시도 | 120초(기본 30초, 할당량 1–120초) / 1회 | 30초면 LLM·느린 DXWorks 요청이 504("서버에 연결할 수 없습니다"), GET은 되풀이됨 |
| 사용자 지정 오류 응답 | 없음 | API의 404 JSON이 HTML 200이 됨 |
| 내부 ALB | 프라이빗 서브넷 2개, 리스너 HTTP:80 → 대상 그룹(ip, 3000, `/health`), 유휴 제한 120초, 등록 취소 지연 130초 | 리디렉션을 넣으면 CloudFront가 따라가지 않음 |
| ALB 보안 그룹 | 인바운드 80 ← `CloudFront-VPCOrigins-Service-SG` (VPC 원본 생성 뒤) | — |
| VPC | 인터넷 게이트웨이 필수(VPC 원본 요건), 태스크 아웃바운드는 NAT 또는 공인 IP | 게이트웨이 없음 → VPC 원본을 만들 수 없음 |
| WAF | 웹 ACL을 us-east-1(CloudFront 범위)에서 만들어 배포판에 연결. 규칙은 루트 README | — |
| Service Connect TLS(선택지 A) | Private CA(단기 인증서 모드, 태그 `AmazonECSManaged`=`true`), ECS 인프라 역할 | 태그·역할 없음 → TLS 설정 실패 |

---

## 6. 제공자 콘솔 (콜백은 모두 FE 주소 `<CF>/auth/<제공자>/callback`, 2단계)

| 제공자 | 설정 | 이 구성의 값 | 제약 · 주의 |
|---|---|---|---|
| Google (OAuth 클라이언트: 웹 애플리케이션) | Branding → 승인된 도메인 | `<CF>`의 호스트 — 리디렉션 URI보다 **먼저** | 콘솔이 `dXXXX.cloudfront.net`을 받는지는 확인하지 못함. 브랜드 확인(앱 이름·로고)은 도메인 소유 확인이 필요해 `*.cloudfront.net`으로는 할 수 없다고 보고 계획 |
| | 승인된 리디렉션 URI | `<CF>/auth/google/callback` | HTTPS·IP 금지. BE는 `openid email profile`만 요청 |
| | Audience | External, **Testing**(테스트 사용자 최대 100명에 시연 계정 등록) | 공개하려면 자기 도메인 |
| GitHub (OAuth App) | Homepage URL / Authorization callback URL | `<CF>` / `<CF>/auth/github/callback` | 정확히 일치(와일드카드 끔), 앱당 콜백 10개, PKCE S256, Expire user access tokens 켬, Device Flow 끔, 조직 소유로 만들지 않음 |
| 네이버 | 서비스 URL / Callback URL / 제공 정보 | `<CF>` / `<CF>/auth/naver/callback` / 이메일 | 검수 전에는 관리자·멤버만 로그인. CloudFront 기본 도메인으로 검수가 통과하는지 **미확인** |

---

## 7. Secrets Manager · 실행 역할

| 비밀 | 키 | 연결하는 작업 정의 | 읽는 실행 역할 |
|---|---|---|---|
| `vc/be-app` (JSON) | `JWT_SECRET`, `HOLDER_KEY_ENCRYPTION_KEY`, `DXWORKS_API_HOST`, `DXWORKS_API_KEY`, (2단계) `GOOGLE_CLIENT_SECRET`, `GITHUB_CLIENT_SECRET`, `NAVER_CLIENT_SECRET` | BE 서비스 | `vc-be-execution` |
| `vc/ai-token` (평문 값 하나) | `AI_SERVICE_TOKEN` | BE 서비스, AI 서비스 | `vc-be-execution`, `vc-ai-execution` |
| `vc/db-app` (JSON) | `username`=`vc_app`, `password` | BE 서비스 (+ 시장 통계 반입) | `vc-be-execution` |
| `rds!db-…` (RDS 관리형) | `username`, `password` | 마이그레이션 태스크만 | `vc-migrate-execution` |
| `vc/ai-llm` (선택) | `CURRICULUM_LLM_API_KEY` | AI 서비스 | `vc-ai-execution` |

ECS는 비밀값을 컨테이너가 시작할 때만 넣으므로 값을 바꾸면 서비스를 "새 배포 강제"로 다시 띄웁니다. 작업 정의가 참조하는 JSON 키가
비밀에 없으면 태스크가 시작하지 않습니다.

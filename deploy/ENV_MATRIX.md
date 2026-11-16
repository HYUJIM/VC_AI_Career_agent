# 환경변수·설정 표 (도메인 없는 CloudFront 구성)

[README.md](README.md)의 구성(비공개 S3 + CloudFront(OAC), `/api/*` → VPC 원본 → 내부 ALB(HTTP:80) → BE(Fargate, 3000), AI(Fargate,
8100)는 ECS Service Connect, RDS for PostgreSQL 16, Secrets Manager, CloudWatch Logs, 리전 `ap-northeast-2`)에서 태스크·빌드·콘솔마다
넣을 값입니다. `<CF>`는 CloudFront 기본 주소 `https://dXXXXXXXXXXXXX.cloudfront.net`(배포판을 만든 뒤에야 알 수 있음)입니다.

표기
- 필수: **필수** / **조건부**(그 기능을 쓸 때) / **2단계**(CloudFront 주소가 나온 뒤, README ⑪) / 선택 / **넣지 않음**
- 종류: **Secret** = 작업 정의 `secrets`(Secrets Manager, `비밀 이름:키`), Plain = 작업 정의 `environment`, 이미지 = Dockerfile `ENV` 기본값
- 검사하는 코드: 저장소 기준 파일과 함수. `config.py` = `BE/app/core/config.py`, `database.py` = `BE/app/services/database.py`,
  시작 점검 = `BE/app/services/db_startup_check.py`
- "종료 1": 운영 설정 검사·범위 검사·형식 검사(`LOG_LEVEL`, `HOLDER_KEY_ENCRYPTION_KEY`)가 실패하면 BE는 stderr에 그 메시지
  **한 줄**(`APP_ENV=production requires …`처럼 변수 이름만, 값 없음)을 남기고 종료 코드 1로 끝납니다(`BE/main.py`가 불러올 때).
  AI의 `AI_MAX_BODY_BYTES`도 같습니다(`ai/src/server/app.py`). "종료 3": 시작 DB 점검 실패
  (`DB startup check failed: <종류>: <요약>` 한 줄 + `Application startup failed. Exiting.`). 둘 다 배포 회로 차단기가 롤백합니다.
- Secrets Manager에는 **값만** 넣습니다(`NAME=` 접두사·끝 줄바꿈 없음, README ④).

---

## 1. BE 서비스 태스크 (`vc-be`, [ecs/be-service.taskdef.json](ecs/be-service.taskdef.json))

| 변수 | 필수 | 이 구성의 값 | 종류 · 출처 | 검사하는 코드 | 틀리면 |
|---|---|---|---|---|---|
| `APP_ENV` | 필수 | `production` (이미지 기본값, 작업 정의에도 명시) | Plain · 이미지 | `config.py` `_app_env()` | `prod` 같은 값 → 종료 1 `APP_ENV must be one of: development, production (empty means development)`. `development` → 운영 검사·시작 DB 점검·HSTS 꺼짐, `/docs` 노출, 모의 발급 기본 켜짐 |
| `JWT_SECRET` | **필수** | `secrets.token_urlsafe(48)` | **Secret** `vc/be-app:JWT_SECRET` | `get_settings()` | 32바이트 미만·저장소의 테스트 값·서로 다른 바이트 8개 미만 → 종료 1. 태스크마다 다르면 서로의 토큰을 거절 |
| `JWT_EXPIRES_MINUTES` | 선택 | 넣지 않음(1440) | Plain | `get_settings()` `_env_int` | 1~1440 밖 → 종료 1 `JWT_EXPIRES_MINUTES must be an integer between 1 and 1440` |
| `HOLDER_KEY_ENCRYPTION_KEY` | **필수** | `base64(os.urandom(32))` | **Secret** `vc/be-app:HOLDER_KEY_ENCRYPTION_KEY` | `get_settings()`, `BE/app/services/key_custody.py` `parse_master_key()` | 없음·공개 테스트 키 → 종료 1. 형식 오류 → 종료 1 `HOLDER_KEY_ENCRYPTION_KEY must be base64` / `HOLDER_KEY_ENCRYPTION_KEY must decode to 32 bytes`(`NAME=` 접두사를 붙인 값은 보통 뒤쪽). 바꾸거나 잃으면 기존 홀더 키 복호화 불가 |
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
| `DB_MAX_CONNECTIONS` | 선택 | 넣지 않음(20) | Plain | `config.py` `db_max_connections()` | 2 미만 → 종료 1. 태스크 수 × 이 값이 RDS `max_connections`를 넘으면 `too many connections`(시작 때면 종료 3, 실행 중이면 503 `Service is busy`) |
| `PGOPTIONS` | 넣지 않음 | — | — | `database.py` | 앱 세션 제한(15초·5초·30초)이 뒤에 붙어 우선 |
| `CORS_ORIGINS` | **넣지 않음** | 같은 출처라 불필요 | — | `get_settings()` | `<CF>/`(끝 `/`)·`*` → 종료 1 `CORS_ORIGINS (scheme://host[:port] entries only: …)` |
| `AI_SERVICE_URL` | **필수** | `http://ai:8100` (AI의 client alias `ai`:8100과 짝) | Plain | `get_settings()`, `BE/app/integrations/ai_service.py` `_settings()` | 없음 → 기본 `http://127.0.0.1:8100`(BE 태스크 자신) → AI 기능 503. 별칭과 다름 → 503 `AI_SERVICE_UNAVAILABLE` |
| `AI_SERVICE_ALLOW_HTTP` | **필수** | `true` | Plain | `ai_service.py` `_settings()` | `false` → 시작은 하고 AI 기능 503 `AI_SERVICE_NOT_CONFIGURED`. 코드는 Service Connect TLS 여부를 모름(README ⑥ 선택지) |
| `AI_SERVICE_TOKEN` | **필수** | AI와 같은 값 | **Secret** `vc/ai-token` (평문 값 하나) | `get_settings()`(다른 비밀값과 비교), `ai_service.py` `_settings()` | 없음·32바이트 미만·공백/줄바꿈 → AI 기능 503 `AI_SERVICE_NOT_CONFIGURED`(시작은 함). AI 값과 다름 → 503 `AI_SERVICE_AUTH_FAILED`. 다른 비밀값과 같음 → 종료 1 |
| `AI_TIMEOUT_SECONDS` / `AI_GENERATION_TIMEOUT_SECONDS` | 선택 | 넣지 않음(30 / 90) | Plain | `get_settings()` `_env_float` | 1~600 밖 → 종료 1. Service Connect `perRequestTimeoutSeconds`(기본 15)나 CloudFront 원본 응답 제한 시간이 더 짧으면 그 시간에 끊김 |
| `AI_MAX_CONCURRENCY` | 선택 | 넣지 않음(4) | Plain | `get_settings()` | 1 미만 → 종료 1. 사용자 한 명은 그중 2자리·분당 30번·시간당 300번까지(코드 고정, 넘으면 429 `Too many analysis requests. Try again later.`) |
| `DXWORKS_API_HOST` | **조건부** | `https://<협력사 호스트>` | **Secret** `vc/be-app:DXWORKS_API_HOST` (비공개 주소라 작업 정의에 드러내지 않음) | `get_settings()`, `BE/app/integrations/dxworks.py` `is_valid_host_setting()` | 비루프백 `http://`·사용자 정보·쿼리·공백 → 종료 1. 키를 빼면 그 기능만 503 `PROVIDER_NOT_CONFIGURED`. 나가는 길 없음 → 502 `DXWorks 연결 실패: ConnectTimeout`(약 15초) |
| `DXWORKS_API_KEY` | **조건부** | 협력사 키 | **Secret** `vc/be-app:DXWORKS_API_KEY` | `get_settings()` | ASCII 아님 → 종료 1. 비움 → 발급·신원 검증 503 |
| `DXWORKS_BADGE_TEMPLATE_ID` | **조건부**(모의 발급) | `<DXWORKS_BADGE_TEMPLATE_ID>` | Plain | `get_settings()` | 비움 → 허용 템플릿 없음(403) |
| `DXWORKS_TIMEOUT_SECONDS` | 선택 | 넣지 않음(15) | Plain | `get_settings()` `_env_float` | 1~120 밖 → 종료 1. 최악 4배(60초) > CloudFront 원본 응답 제한 기본 30초 → README ⑧에서 120초 |
| `MOCK_ISSUER_ENABLED` | 시연이면 **필수** | `true` | Plain | `get_settings()` | 없음 → production에서 꺼짐(404 `Mock issuer is disabled`). 켜면 시작 로그에 경고 한 줄 |
| `MOCK_ISSUER_TEMPLATE_IDS` / `MOCK_ISSUER_DAILY_LIMIT` / `MOCK_ISSUER_GLOBAL_DAILY_LIMIT` | 선택 | 비움 / 10 / 시연 인원 × 10보다 조금 크게 | Plain | `get_settings()` | 한도 1 미만 → 종료 1 |
| `IDENTITY_VERIFICATION_ENABLED` | 넣지 않음 | (false) | — | `get_settings()` | 켜면 사용자가 보낸 값에 회사 API 키가 붙어 나감 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | **2단계** | 콘솔 값 / 콘솔 값 / `<CF>/auth/google/callback` | Plain / **Secret** `vc/be-app:GOOGLE_CLIENT_SECRET` / Plain | `get_settings()`, `BE/app/api/oauth.py` | 하나만·콜백 없음·`http://` → 종료 1. FE가 보내는 주소(`window.location.origin` + 경로)와 글자가 다르면 400 `Invalid redirect_uri` |
| `GOOGLE_ALLOWED_REDIRECT_URIS` | 넣지 않음 | — | — | `get_settings()` | Google 로그인을 켰을 때(`GOOGLE_CLIENT_ID`·`GOOGLE_CLIENT_SECRET` 중 하나라도) `https://` 아닌 항목 → 종료 1. Google을 끈 채면 검사하지 않고 쓰지도 않음 |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` / `GITHUB_REDIRECT_URI` | **2단계** | 콘솔 값 / 콘솔 값 / `<CF>/auth/github/callback` | Plain / **Secret** / Plain | `get_settings()` | 위와 같은 규칙 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` / `NAVER_REDIRECT_URI` | **2단계** | 콘솔 값 / 콘솔 값 / `<CF>/auth/naver/callback` | Plain / **Secret** / Plain | `get_settings()` | 위와 같은 규칙 |
| `OAUTH_TIMEOUT_SECONDS` | 선택 | 넣지 않음(10) | Plain | `config.py` `_oauth_timeout_seconds()` | 1~120 밖 → 종료 1 |
| `LOG_LEVEL` | 선택 | 넣지 않음(INFO) | Plain | `config.py` `log_level()`, `BE/main.py` | `DEBUG`·`INFO`·`WARNING`·`ERROR`·`CRITICAL`(대소문자 무관, 빈 값은 INFO)만 받음. 그 밖(`verbose`·`NOTSET`·`WARN` 등) → 종료 1 `LOG_LEVEL must be one of: DEBUG, INFO, WARNING, ERROR, CRITICAL (empty means INFO)`. `DEBUG` → httpx가 DXWorks 전체 URL을 로그에 남김. `WARNING` 이상 → 감사 로그 없음 |
| `RESEARCHER_USER_IDS` / `CURRICULUM_LLM_USER_IDS` | 선택 | 가입 뒤 계정 UUID | Plain | `get_settings()` | — |
| `CURRICULUM_LLM_DAILY_LIMIT` | 선택 | 넣지 않음(20) | Plain | `get_settings()` `_env_int`, `BE/app/services/career.py` `reserve_llm_generation()` | 1 미만·정수 아님 → 종료 1 `CURRICULUM_LLM_DAILY_LIMIT must be an integer >= 1`. 허용된 계정 하나의 24시간 LLM 모드 학습 계획 수(저장된 LLM 계획은 DB로, Gemini까지 간 실패한 시도는 태스크 메모리로 셈). 넘으면 429 `Daily LLM curriculum limit reached. …` + `Retry-After` |
| `FORWARDED_ALLOW_IPS` | 권장 | `<VPC_CIDR>` (예 `10.0.0.0/16`) | Plain | uvicorn 0.54.0 `uvicorn/config.py`(기본 `127.0.0.1,::1`, CIDR 지원) | 없음 → 접근 로그의 클라이언트 IP가 ALB 주소. 앱 동작 영향 없음 |
| `UVICORN_TIMEOUT_KEEP_ALIVE` | 권장 | `130` (ALB 유휴 120초보다 길게) | Plain | uvicorn `uvicorn/main.py`(`auto_envvar_prefix="UVICORN"`, `--timeout-keep-alive`) | 기본 5초 → ALB 쪽 간헐 502 가능 |
| `UVICORN_RELOAD` 등 그 밖 `UVICORN_*` | **넣지 않음** | — | — | `BE/main.py`, uvicorn | 리로더가 켜짐 |
| `PORT` | 의미 없음 | — | — | `BE/Dockerfile` CMD가 3000 고정 (`python main.py`에서만 읽음) | — |
| `AUTH_REQUIRED`, `KAKAO_*` | 넣지 않음 | — | — | `get_settings()` | 경고만 |

작업 정의의 그 밖 값: `runtimePlatform` `LINUX`/`X86_64`(이미지를 `--platform linux/amd64`로 빌드), `cpu` 1024 / `memory` 2048,
포트 매핑 `be-http`(3000, appProtocol `http`), `readonlyRootFilesystem: true`, `stopTimeout: 120`, `healthCheck`(Dockerfile과 같은
python 명령, `startPeriod: 60`), `awslogs`(`/ecs/vc-be`, 접두사 `be`. 그룹은 README ①에서 미리 만듦). 서비스는 Service Connect **클라이언트**(AI와 같은 네임스페이스),
ALB 대상 그룹 `ip`/3000, 상태 확인 유예 90초, 배포 회로 차단기·롤백. 실행 역할 `vc-be-execution`(`vc/be-app`·`vc/ai-token`·`vc/db-app`만
읽음), 태스크 역할은 정책 없음(앱이 AWS API를 쓰지 않음).

---

## 2. 마이그레이션 태스크 (`vc-be-migrate`, [ecs/be-migrate.taskdef.json](ecs/be-migrate.taskdef.json), BE 서비스와 **같은 이미지 태그**)

| 변수/설정 | 필수 | 값 | 종류 · 출처 | 검사하는 코드 | 틀리면 |
|---|---|---|---|---|---|
| 명령 | **필수** | `["python", "-m", "app.migrate"]` (`--seed` 금지) | — | `BE/app/migrate.py` `main()` | `--seed` → production에서 접속 전에 거부(종료 1) |
| `APP_ENV` | 필수 | `production` | Plain · 이미지 | `migrate.py` `_production()`(`development`가 아니면 운영 검사), `config.py` `_app_env()` | 비우면 운영 검사(시드 해시 계정 검사·TLS 검사)를 건너뜀 |
| `DB_HOST` / `DB_PORT` / `DB_NAME` | **필수** | 서비스와 같음 | Plain | `database.py` `get_db_connection()` | `DB_HOST` 없음 → `localhost`로 접속 시도, 60초 기다린 뒤 종료 1(`마이그레이션 실패: DB에 접속하지 못했습니다: OperationalError: connection refused (check DB_HOST and DB_PORT)`) |
| `DB_USER` / `DB_PASSWORD` | **필수** | **마스터 사용자** | **Secret** `<RDS 관리형 비밀>:username` / `:password` | `config.py` `db_password()`, `migrate.py` `_connect_waiting()` | 빈 값·`password` → 접속하지 않고 종료 1. 틀림 → 기다리지 않고 종료 1 `마이그레이션 실패: DB에 접속하지 못했습니다: OperationalError: password authentication failed (check DB_USER and DB_PASSWORD)` |
| `DB_APP_ROLE` | **필수(운영)** | `vc_app` | Plain | `migrate.py` `grant_app_role()`·`check_app_role()` | 역할 없음·특권 소속 등 → 종료 1(`마이그레이션 실패:` 줄). 비우면 권한을 주지 않아 BE가 시작 DB 점검에서 `InsufficientPrivilege: permission denied on table users (run the migration task with DB_APP_ROLE set to this DB_USER)`로 종료 3 |
| `PGSSLMODE` | **필수** | `verify-full` | Plain | `config.py` `db_tls_options()` (접속할 때마다) | 없음·빈 값 등 → 접속하기 전에 종료 1, 한 줄 `ERROR:app.migrate:마이그레이션 실패: APP_ENV=production requires PGSSLMODE (verify-full or verify-ca in production)…`(60초 대기 없음). `DB_PASSWORD`도 틀렸으면 그것을 먼저 알림 |
| `PGSSLROOTCERT` | **넣지 않음** | 이미지 기본값 | 이미지 | 위와 같음 | 서비스와 같음 |
| `DB_CONNECT_TIMEOUT`, `LOG_LEVEL` | 선택 | 10 / INFO | Plain | `get_db_connection()`, `migrate.py` | 없는 `LOG_LEVEL` 이름 → 종료 1 `마이그레이션 실패: LOG_LEVEL must be one of: …` |
| (접속 오류) | — | — | — | `migrate.py` `_connect_waiting()`, 요약은 시작 점검의 `describe_error()` | 인증서 확인 실패·호스트 이름 불일치·비밀번호·없는 역할·DB·`pg_hba.conf` → 기다리지 않고 종료 1. 연결 거부·시간 초과·DB 시작 중 → 60초 동안 2초마다 다시 시도(`DB 연결 대기 중: <요약>`) 뒤 종료 1. 줄에는 DB 주소·IP·사용자 이름·인증서 경로가 없음 |
| 앱 비밀값(`JWT_SECRET` 등) | **넣지 않음** | — | — | 실행기는 `get_settings()`를 부르지 않음 | 불필요한 노출 |
| 네트워크 | **필수** | 앱용 서브넷 + `sg-be`, ECR·Logs·Secrets Manager 경로(NAT·공인 IP·엔드포인트) | — | — | 이미지·비밀을 못 받아 태스크 시작 실패 |

## 2-1. (선택) 시장 통계 반입 태스크 (`vc-be-market-import`, [ecs/be-market-import.taskdef.json](ecs/be-market-import.taskdef.json), README ⑤-1)

`run-task`의 컨테이너 덮어쓰기는 명령만 바꾸고 이미지는 바꾸지 못하므로, 스냅샷을 더한 이미지와 그 이미지를 쓰는 일회성 작업 정의를
따로 둡니다(BE 서비스 작업 정의에 명령만 덮어쓰면 `/app/market_snapshots`가 없어 실패).

| 변수/설정 | 필수 | 값 | 종류 · 출처 | 검사하는 코드 | 틀리면 |
|---|---|---|---|---|---|
| 이미지 | **필수** | `vc-be:<TAG>-market` (BE 이미지 + `ai/data/market_snapshots/*.json`, 태그 불변이라 서비스와 다른 태그) | — | — | BE 서비스 이미지 그대로 → `실패: 통계 JSON 파일이 없습니다: /app/market_snapshots`, 종료 1 |
| 명령 | **필수** | `["python", "-m", "app.jobs.market", "import", "--dir", "/app/market_snapshots"]` | — | `BE/app/jobs/market.py` `main()` | 인자 오류 → 종료 2 |
| `APP_ENV` | 필수 | `production` | Plain · 이미지 | `config.py` | — |
| `DB_HOST` / `DB_PORT` / `DB_NAME` / `PGSSLMODE` | **필수** | 서비스와 같음(`verify-full`) | Plain | `database.py` `get_db_connection()`, `config.py` `db_tls_options()` | `PGSSLMODE`가 틀리면 접속하지 않고 `실패: APP_ENV=production requires PGSSLMODE …` 한 줄, 종료 1 |
| `DB_USER` / `DB_PASSWORD` | **필수** | 서비스 계정 `vc_app` | **Secret** `vc/db-app:username` / `:password` | `config.py` `db_password()` | 권한이 모자라면 `실패: …` 한 줄, 종료 1 |
| `PGSSLROOTCERT` | **넣지 않음** | 이미지 기본값 | 이미지 | 서비스와 같음 | 서비스와 같음 |
| 앱 비밀값(`JWT_SECRET` 등)·AI 설정 | **넣지 않음** | — | — | `import`는 `get_settings()`·AI를 부르지 않음 | 불필요한 노출 |
| 실행 역할 · 네트워크 | **필수** | `vc-be-execution`(`vc/db-app`을 읽음), 앱용 서브넷 + `sg-be` | — | — | — |

---

## 3. AI 서비스 태스크 (`vc-ai`, [ecs/ai-service.taskdef.json](ecs/ai-service.taskdef.json), 공개 로드밸런서 없음)

| 변수/설정 | 필수 | 값 | 종류 · 출처 | 검사하는 코드 | 틀리면 |
|---|---|---|---|---|---|
| `AI_SERVICE_TOKEN` | **필수** | BE와 같은 값 | **Secret** `vc/ai-token` | `ai/src/server/app.py` `require_service_token()` (요청마다) | 32바이트 미만·없음 → AI는 healthy인데 `/v1/*` 503 `AI_SERVICE_TOKEN_NOT_CONFIGURED`(BE는 503 `AI_SERVICE_AUTH_FAILED`). BE와 다름 → 401(BE는 503 `AI_SERVICE_AUTH_FAILED`) |
| `AI_MAX_BODY_BYTES` | 선택 | 넣지 않음(32 MiB) | Plain | `app.py` `max_body_bytes()`, `ServiceGuard.body_limit()` | 양의 정수가 아님 → 시작(import) 때 종료 1 `AI_MAX_BODY_BYTES must be a positive integer (bytes)`(한 줄), 배포 회로 차단기가 롤백. 공고·학습자료 묶음을 싣는 경로(`app.py` `CORPUS_ROUTES`)에만 이 값을 쓰고, 그 밖의 경로(공고 하나·자료 하나·미리보기)는 min(4 MiB, 이 값)이다. 묶음이 이 값을 넘으면 BE에 413 |
| `CURRICULUM_LLM_ENABLED` | 선택 | `false` (Gemini를 켤 때만 `true`) | Plain | `ai/src/career/gemini_client.py`, `llm_client.py`, `local_selector.py` | `true`가 아니면 LLM 모드 요청만 503 `GENERATOR_CONFIGURATION_REQUIRED`(템플릿 계획은 됨) |
| `GEMINI_API_KEY` | 조건부(LLM) | Google AI Studio에서 만든 키 | **Secret** `vc/ai-llm:GEMINI_API_KEY` | `gemini_client.py` `GeminiConfig.from_env()` (요청마다) | 없음·공백·줄바꿈·비ASCII → LLM 모드만 503 `GENERATOR_CONFIGURATION_REQUIRED`. 키가 틀림 → 422 `LLM_PROVIDER_REJECTED`, AI 로그 `Gemini API rejected the request: status=401 …`. 값은 로그·응답에 나오지 않음 |
| `CURRICULUM_LLM_PROVIDER` | 넣지 않음 | 비움 → `gemini` | Plain | `llm_client.py` `generator_from_env()` | 그 밖의 값 → LLM 모드만 503. `compatible`(OpenAI 호환, `_BASE_URL`·`_API_KEY`·`_TOKEN_PARAMETER`, 키는 `vc/ai-llm:CURRICULUM_LLM_API_KEY`)도 받음. `local-transformers`는 이미지에 GPU 패키지가 없어 쓸 수 없음 |
| `CURRICULUM_LLM_MODEL` | 선택 | 넣지 않음 → `gemini-3.5-flash-lite` | Plain | `gemini_client.py` | 모델 ID 형식 위반 → LLM 모드만 503. 없는 모델 → 422(Gemini 404) |
| `CURRICULUM_LLM_THINKING_LEVEL` / `GEMINI_API_VERSION` | 넣지 않음 | 모델 기본값 / `v1` | Plain | `gemini_client.py` | `minimal`·`low`·`medium`·`high` / `v1`·`v1beta` 밖 → LLM 모드만 503 |
| `CURRICULUM_LLM_MAX_CONCURRENCY` | 선택 | 넣지 않음(2) | Plain | `llm_client.py` `llm_max_concurrency()`, `app.py` `create_app()` | 1~32의 ASCII 숫자가 아님 → 시작(import) 때 종료 1 `CURRICULUM_LLM_MAX_CONCURRENCY must be an integer from 1 to 32`(한 줄). 넘치는 생성은 10초 뒤 BE 503 `LLM_BUSY` |
| LLM 나가는 길 | 조건부(LLM) | `generativelanguage.googleapis.com:443` (AI만) | — | `gemini_client.py` (프록시 환경변수·리디렉션 안 씀) | 막힘 → 25초 뒤 503 `LLM_PROVIDER_UNAVAILABLE` |
| `CURRICULUM_LOCAL_ALLOW_DOWNLOAD` | 넣지 않음 | (false) | — | `local_selector.py` | — |
| `CAREER_AI_SKIP_DOTENV` | 불필요 | — | — | `app.py` (이미지에 `.env` 없음, `ai/.dockerignore`가 `**/.env`·`**/.env.*` 제외) | — |
| 포트 매핑 | **필수** | `name: ai-http`, `containerPort: 8100`, `appProtocol: http` | — | — | Service Connect 서비스를 만든 뒤에는 `appProtocol`을 바꿀 수 없음 |
| Service Connect(서버) | **필수** | `portName: ai-http`, `discoveryName: ai`, `clientAliases: [{dnsName: ai, port: 8100}]`, `timeout.perRequestTimeoutSeconds: 120`, (선택지 A) `tls` | — | — | 별칭 없음 → 이름이 `ai.<네임스페이스>`라 `http://ai:8100`이 안 풀림(`/career` 503). 제한 시간 기본 15초 → LLM 커리큘럼 끊김 |
| 리소스 | 권장 | `cpu` 512 / `memory` 1024 (프록시 몫 포함) | — | — | — |
| `healthCheck` | 권장 | `ai/Dockerfile`과 같은 python 명령(8100), `startPeriod: 30` | — | — | — |
| 보안 그룹 | **필수** | 인바운드 8100 ← `sg-be`만 | — | — | — |

AI는 `LOG_LEVEL`을 읽지 않습니다. AI의 `/docs`는 켜져 있지만 공개 로드밸런서가 없어 밖에서 닿지 않습니다. 실행 역할
`vc-ai-execution`은 `vc/ai-token`(LLM을 켜면 `vc/ai-llm`)만 읽습니다. [ecs/ai-service.taskdef.json](ecs/ai-service.taskdef.json)은 LLM을 끈
상태(`CURRICULUM_LLM_ENABLED=false`, `GEMINI_API_KEY` 참조 없음)이고, Gemini를 켤 때 사본에 더하는 두 줄은 [README.md](README.md) ⑥의
"LLM(Google Gemini API) 켜기"에 있습니다(없는 비밀 키를 참조하면 태스크가 시작하지 않으므로 템플릿에는 넣지 않음).

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
| 기본 동작(S3) | OAC, `Redirect HTTP to HTTPS`, `Managed-CachingOptimized`, 응답 헤더 정책([CHECKLIST.md](CHECKLIST.md) "FE"의 CSP), 뷰어 요청 함수 `spa-fallback.js` | 함수 없음 → 새로고침·OAuth 콜백·공유 링크 403 |
| 배포판 요금제 | 종량제(pay-as-you-go) | Free·Pro 정액 요금제는 VPC 원본·사용자 지정 응답 헤더 정책이 없고 WAF 규칙 5·25개라 이 구성을 만들 수 없음 |
| VPC 원본(자원) | Origin ARN = 내부 ALB, **Protocol `HTTP only`**(`OriginProtocolPolicy=http-only`), **HTTP port 80**, IPv4. 프로토콜·포트는 이 자원에서 정함(배포판의 원본 설정이 아님) | `Match viewer`·`HTTPS only` → ALB 443에 리스너가 없어 모든 `/api/*` 502. 고치려면 배포판에서 떼어 낸 뒤 편집(README ⑧-3) |
| `/api/*` 동작 | 위 VPC 원본, `Managed-CachingDisabled`, `Managed-AllViewerExceptHostHeader`, 모든 메서드, 함수 없음 | `Authorization`이 빠지면 로그인 직후 401로 로그아웃 |
| 배포판 원본(VPC 원본) 응답 제한 시간 / keep-alive / 연결 시도 | 120초(기본 30초, 할당량 1–120초) / 기본값 / 1회 (배포판 쪽에서 정하는 것은 이 셋뿐) | 30초면 LLM·느린 DXWorks 요청이 504("서버에 연결할 수 없습니다"), GET은 되풀이됨 |
| 사용자 지정 오류 응답 | 없음 | API의 404 JSON이 HTML 200이 됨 |
| 내부 ALB | 프라이빗 서브넷 2개, 리스너 HTTP:80 → 대상 그룹(ip, 3000, `/health`), 유휴 제한 120초, 등록 취소 지연 130초 | 리디렉션을 넣으면 CloudFront가 따라가지 않음 |
| ALB 보안 그룹 | 인바운드 80 ← `CloudFront-VPCOrigins-Service-SG` (VPC 원본 생성 뒤) | — |
| VPC | 인터넷 게이트웨이 필수(VPC 원본 요건), 태스크 아웃바운드는 NAT 또는 공인 IP | 게이트웨이 없음 → VPC 원본을 만들 수 없음 |
| WAF | 웹 ACL을 us-east-1(CloudFront 범위)에서 만들어 배포판에 연결. 규칙은 [CHECKLIST.md](CHECKLIST.md) "HTTPS·네트워크" | — |
| Service Connect TLS(선택지 A) | Private CA(루트, 단기 인증서 모드, 태그 `AmazonECSManaged`=`true`, **CA 인증서 설치 → `ACTIVE`**), ECS 인프라 역할 `ecsInfrastructureRole` + `iam:PassRole` (README ⑥) | 태그·역할 없음·CA가 `PENDING_CERTIFICATE` → TLS 설정 실패(인증서 발급 불가) |
| CloudWatch 로그 그룹 | `/ecs/vc-be`·`/ecs/vc-ai`를 첫 태스크 전에 만들고 보존 기간을 정함(README ①) | 없음 → 태스크가 `ResourceInitializationError`(… `The specified log group does not exist`), `exitCode` 없음 |
| 보안 그룹 아웃바운드 | 기본값(모두 허용). 좁히면 `sg-be` 443→`0.0.0.0/0`·5432→`sg-rds`·8100→`sg-ai`, `sg-alb` 3000→`sg-be` (README ①) | 443만 열면 DB(시작 점검 `ConnectionTimeout`)·AI(503)·ALB(502)가 막힘. 호스트 이름 단위 제한은 보안 그룹으로 불가 |

---

## 6. 제공자 콘솔 (콜백은 모두 FE 주소 `<CF>/auth/<제공자>/callback`, 2단계)

| 제공자 | 설정 | 이 구성의 값 | 제약 · 주의 |
|---|---|---|---|
| Google (OAuth 클라이언트: 웹 애플리케이션) | Branding → 승인된 도메인 | `<CF>`의 호스트 — 리디렉션 URI보다 **먼저** | 콘솔이 `dXXXX.cloudfront.net`을 받는지는 확인하지 못함. 브랜드 확인(앱 이름·로고)은 도메인 소유 확인이 필요해 `*.cloudfront.net`으로는 할 수 없다고 보고 계획 |
| | 승인된 리디렉션 URI | `<CF>/auth/google/callback` | HTTPS·IP 금지. BE는 `openid email profile`만 요청 |
| | Audience | External, **Testing**(테스트 사용자 최대 100명에 시연 계정 등록) | 공개하려면 자기 도메인. Testing 앱의 refresh token은 7일 뒤 만료되지만 BE는 refresh token을 받지 않아(`access_type=offline` 없음) 영향 없음 |
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
| `vc/ai-llm` (선택, JSON) | `GEMINI_API_KEY` (OpenAI 호환 공급자를 쓰면 `CURRICULUM_LLM_API_KEY`) | AI 서비스 | `vc-ai-execution` |

ECS는 비밀값을 컨테이너가 시작할 때만 넣으므로 값을 바꾸면 서비스를 "새 배포 강제"로 다시 띄웁니다. 작업 정의가 참조하는 JSON 키가
비밀에 없으면 태스크가 시작하지 않습니다.

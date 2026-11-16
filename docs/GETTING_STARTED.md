# 서비스 실행 방법

프로젝트 소개는 [저장소 README](../README.md), AWS 운영 배포 기준은 [AWS 배포 체크리스트](../deploy/CHECKLIST.md)와 [deploy/README.md](../deploy/README.md)에 있습니다.

아래 명령은 모두 **저장소 최상위 폴더**(`VC_AI_Career_agent/`)에서 실행합니다. 폴더 이동은 `( … )` 안에서만 하므로 순서대로 붙여 넣어도 됩니다. Windows는 Git Bash 또는 WSL을 쓰세요.

## 1. 준비물

- Docker Desktop (DB·BE·AI를 컨테이너로 실행)
- Node.js 22.12 이상 (FE, Vite 8 요구사항. 20.19 이상도 가능)
- Python 3.11 (테스트나 Docker 없이 실행할 때만)
- DXWorks 접속 정보(`DXWORKS_API_HOST`, `DXWORKS_API_KEY`, `DXWORKS_BADGE_TEMPLATE_ID`): 협력사 비공개 값이라 팀 내부 채널로 받습니다. 없으면 배지 발급·검증과 직무 비교를 할 수 없습니다. 협력사 전달본의 `API_BASE_URL`은 `DXWORKS_API_HOST`, `API_KEY`는 `DXWORKS_API_KEY`에 넣고, `DXWORKS_BADGE_TEMPLATE_ID`에는 전달본 안내서(START_HERE)의 준비된 "OB3 배지" 템플릿 ID를 넣습니다. `TENANT_CODE`는 BE가 쓰지 않습니다(템플릿 ID 자리에 넣으면 발급이 400 `BADGE template not found`로 실패).

## 2. 환경 변수 설정 (`BE/.env`)

```bash
cp -n BE/.env.example BE/.env
```

아래 명령으로 값을 하나씩 만들어 `BE/.env`의 같은 이름 항목에 붙여 넣습니다. 출력된 값은 공유 저장소에 올리지 마세요.

```bash
python3 -c "import secrets; print('JWT_SECRET=' + secrets.token_urlsafe(48))"
```

```bash
python3 -c "import secrets; print('AI_SERVICE_TOKEN=' + secrets.token_urlsafe(48))"
```

```bash
python3 -c "import base64, os; print('HOLDER_KEY_ENCRYPTION_KEY=' + base64.b64encode(os.urandom(32)).decode())"
```

| 항목 | 설명 |
|---|---|
| `APP_ENV` | 실행 환경. 로컬은 예시 파일 값 `development` 그대로(비워도 같음). `production`(BE Docker 이미지 기본값, Compose는 로컬 서비스에 `development`를 넣음)이면 아래 `JWT_SECRET`·`HOLDER_KEY_ENCRYPTION_KEY`와 `DB_HOST`·`DB_NAME`·`DB_USER`·`DB_PASSWORD`가 없거나 이 저장소의 테스트·예시 값(예: `DB_PASSWORD=password`)일 때, 또는 `JWT_SECRET`·`HOLDER_KEY_ENCRYPTION_KEY`·`SERVICE_API_KEY`·`AI_SERVICE_TOKEN`·`DXWORKS_API_KEY`·`GOOGLE_CLIENT_SECRET`·`GITHUB_CLIENT_SECRET`·`NAVER_CLIENT_SECRET` 중 넣은 두 값이 서로 같을 때, 소셜 로그인(Google·GitHub·네이버) 설정이나 `CORS_ORIGINS`가 운영 규칙에 맞지 않을 때, DB TLS(`PGSSLMODE`가 `verify-full`·`verify-ca`, `PGSSLROOTCERT`가 읽을 수 있는 CA 묶음 파일)가 아닐 때 BE가 시작하지 않습니다(오류 한 줄, 종료 코드 1). `production`에서는 API 문서(`/docs`·`/redoc`·`/openapi.json`)도 제공하지 않고, 시작할 때 DB에 한 번 접속해 확인합니다([AWS 배포 체크리스트](../deploy/CHECKLIST.md)) |
| `DXWORKS_API_HOST`, `DXWORKS_API_KEY`, `DXWORKS_BADGE_TEMPLATE_ID` | 협력사 DXWorks 접속 정보. `DXWORKS_API_HOST`는 `https://` 주소(`http://`는 `localhost`·`127.0.0.1`·`[::1]`만). 비워 두면 BE는 뜨고 DXWorks를 쓰는 기능만 503이며, 넣은 값이 이 규칙에 맞지 않거나 사용자 정보(`user:pw@`)·쿼리·프래그먼트·공백이 들어 있거나 포트가 1~65535 밖이면 `APP_ENV`와 관계없이 BE가 시작하지 않습니다 |
| `JWT_SECRET` | 로그인 토큰 서명 키(32바이트 이상). 비우면(`development`에서만 허용) 재시작할 때마다 로그인이 풀립니다 |
| `HOLDER_KEY_ENCRYPTION_KEY` | 사용자 지갑 키 암호화 키. **한 번 정하면 바꾸거나 잃어버리면 안 됩니다**. 비우면(`development`에서만 허용) 새 가입자가 임시 DID를 받습니다 |
| `AI_SERVICE_TOKEN` | BE와 AI가 같이 쓰는 비밀(32바이트 이상, 위 명령으로 따로 만들어 `JWT_SECRET`·`HOLDER_KEY_ENCRYPTION_KEY`·`SERVICE_API_KEY`·`DXWORKS_API_KEY`·`GOOGLE_CLIENT_SECRET`·`GITHUB_CLIENT_SECRET`·`NAVER_CLIENT_SECRET`과 다른 값). Compose가 AI 컨테이너에도 같은 값을 넣습니다 |
| `MOCK_ISSUER_ENABLED`, `MOCK_ISSUER_TEMPLATE_IDS`, `MOCK_ISSUER_DAILY_LIMIT`, `MOCK_ISSUER_GLOBAL_DAILY_LIMIT` | FE **자격증명 발급**(`/issue`) 화면이 쓰는 모의 발급. `MOCK_ISSUER_ENABLED`를 비우면(예시 파일 값) `development`에서는 켜지고 `production`에서는 꺼집니다. 켜져 있어도 로그인한 본인 DID에만, `DXWORKS_BADGE_TEMPLATE_ID`와 `MOCK_ISSUER_TEMPLATE_IDS`(선택, 쉼표 구분)의 템플릿만, 사용자별 24시간 `MOCK_ISSUER_DAILY_LIMIT`건(기본 10)·모든 사용자를 합쳐 24시간 `MOCK_ISSUER_GLOBAL_DAILY_LIMIT`건(기본 200)까지 발급합니다 |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `GOOGLE_ALLOWED_REDIRECT_URIS` | (선택) 로그인·회원가입 화면의 "Google로 계속하기". ID·시크릿은 Google Cloud Console의 OAuth 클라이언트 값이며 팀 내부 채널로 받습니다. 둘 중 하나라도 비면 Google 로그인만 503(`Google OAuth is not configured`)이고 나머지 기능은 그대로입니다 |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_REDIRECT_URI` | (선택) "GitHub로 로그인". GitHub OAuth App(GitHub Settings > Developer settings > OAuth Apps)의 Client ID와 Client secret이며 팀 내부 채널로 받습니다(앱을 직접 만드는 방법은 [BE/README.md](../BE/README.md) "소셜 로그인"). 둘 중 하나라도 비면 GitHub 로그인만 503(`GitHub OAuth is not configured`) |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `NAVER_REDIRECT_URI` | (선택) "네이버 로그인". 네이버 개발자센터 애플리케이션의 Client ID·Client Secret이며 팀 내부 채널로 받습니다. 검수 전에는 애플리케이션에 등록한 멤버 아이디만 로그인할 수 있습니다. 둘 중 하나라도 비면 네이버 로그인만 503(`Naver OAuth is not configured`) |
| `OAUTH_TIMEOUT_SECONDS` | (선택) 세 소셜 로그인 제공자 호출 제한 시간(초, 1~120, 기본 10). 이 줄이 없거나 비어 있는 예전 `.env`는 `GOOGLE_OAUTH_TIMEOUT_SECONDS`를 그대로 읽습니다 |
| `IDENTITY_VERIFICATION_ENABLED`, `DB_MAX_CONNECTIONS`, `AI_MAX_CONCURRENCY` | (선택) 차례로 신원 VP 검증(`POST /api/v1/verify`의 `type: identity`) 사용 여부(기본 `false`. 화면이 쓰지 않으므로 그대로 둠), BE 프로세스 하나가 동시에 여는 DB 연결 수 상한(기본 20, 2 이상), BE 프로세스 하나가 AI로 동시에 보내는 공고·학습자료 묶음 요청 수 상한(기본 4, 1 이상). 로컬에서는 바꿀 필요가 없습니다 |
| `RESEARCHER_USER_IDS` | 공고·학습자료를 반입할 연구자 계정 UUID (5번에서 채움) |
| `CURRICULUM_LLM_USER_IDS` | 커리큘럼 LLM 모드를 쓸 계정 UUID (선택, 7번) |

소셜 로그인의 콜백 주소(`GOOGLE_REDIRECT_URI`·`GITHUB_REDIRECT_URI`·`NAVER_REDIRECT_URI`)는 로컬이면 예시 파일 값(`http://localhost:5173/auth/<google|github|naver>/callback`)을 그대로 두고, 같은 주소를 각 제공자 콘솔(GitHub은 OAuth App의 Authorization callback URL)에 등록합니다(빈 값으로 두면 그 버튼이 503 `<Label> OAuth redirect_uri is not configured`, 화면은 "<제공자> 로그인을 지금 사용할 수 없습니다"). 콘솔 설정과 계정 연동 규칙은 [BE/README.md](../BE/README.md) "소셜 로그인".

로그인 토큰은 설정과 관계없이 항상 필요합니다. 회원가입·로그인·소셜 로그인·`/health`·기업용 공유 링크 조회 말고는 `Authorization: Bearer <토큰>`이 없으면 401입니다. 예전에 만든 `BE/.env`에 `AUTH_REQUIRED=false` 줄이 남아 있으면 지웁니다(이제 쓰지 않으며, 남아 있으면 BE 시작 로그에 경고만 남습니다). 지금은 쓰지 않는 예전 소셜 로그인 설정도 같아서, 그 ID·시크릿 줄에 값이 남아 있으면 BE는 그 값을 읽지 않고 시작 로그에 `… 설정은 더 이상 쓰지 않습니다 …` 경고만 남기므로 경고에 나온 이름으로 시작하는 줄을 모두 지웁니다.

## 3. DB·BE·AI 실행 (Docker Compose)

```bash
(cd BE && docker compose up --build -d)
```

처음 실행하면 DB 스키마·시드·마이그레이션(04~13)이 자동 적용됩니다. 그 뒤에도 `docker compose up` 때마다 한 번 실행되고 끝나는 `migrate` 서비스(`python -m app.migrate --seed`)가 아직 적용하지 않은 마이그레이션만 적용하고(기록: DB의 `schema_migrations` 표), 성공해야 `backend`가 뜹니다. 상태 확인:

```bash
(cd BE && docker compose ps)
```

```bash
curl http://localhost:3000/health
```

```bash
curl http://localhost:8100/health
```

- BE API 문서: <http://localhost:3000/docs> (`APP_ENV=development`에서만 제공하며 Compose는 `development`로 띄웁니다)
- 로그 확인: `(cd BE && docker compose logs -f backend ai)`
- 종료(데이터 유지): `(cd BE && docker compose down)` / DB까지 초기화: `(cd BE && docker compose down -v)`
- 포트(DB 5432·BE 3000·AI 8100)는 이 PC(`127.0.0.1`)에서만 열립니다. DB 기본 비밀번호·시드 계정·모의 발급이 켜진 개발 설정이므로 다른 기기에서 접속하도록 바꾸지 않습니다.

> **이미 DB 볼륨이 있는 경우(이전 버전에서 업그레이드)** 위 `docker compose up --build -d`를 다시 실행하면 `migrate` 서비스가 기존 볼륨에 없는 마이그레이션(04~13 중 아직 적용하지 않은 것)을 자동으로 적용합니다. 새 BE를 붙인 DB에 09(`users.token_version`)가 없으면 로그인·가입과 토큰이 필요한 모든 요청이, 10(`mock_issuances`)이 없으면 모의 발급이, 11(`oauth_accounts`)이 없으면 소셜 로그인과 내 정보 조회가, 12(`market_postings`·`market_snapshots`)가 없으면 시장 통계 조회가 500입니다. 13은 대소문자를 구분하지 않는 이메일 유일 인덱스를 만들고 쓰지 않는 표 두 개(`student_credential_embeddings`·`user_skills`)를 지우며, 대소문자만 다른 이메일의 계정이 있으면 적용하지 않고 멈춥니다(목록과 조치는 [BE/README.md](../BE/README.md) "DB 마이그레이션"). 결과는 `(cd BE && docker compose logs migrate)`로 보고, 직접 다시 실행하려면:
> ```bash
> (cd BE && docker compose run --rm migrate)
> ```

## 4. FE 실행

새 터미널에서 실행합니다 (개발 서버가 계속 떠 있어야 합니다).

```bash
(cd FE && npm install && npm run dev)
```

브라우저에서 <http://localhost:5173>을 엽니다. `/api` 요청은 개발 서버가 BE(`localhost:3000`)로 넘겨 줍니다.

## 5. 처음 사용하기

**① 사용자: 가입 → 배지 발급·검증**
FE에서 회원가입하면 DID 지갑이 만들어집니다. **자격증명 발급** 화면에서 배지를 발급받고 "검증하고 지갑에 저장하기"를 누릅니다.
모의 발급은 로컬(`APP_ENV=development`)에서 기본으로 켜져 있고 사용자별 24시간 10건, 모든 사용자를 합쳐 24시간 200건까지입니다. 더 필요하면 `BE/.env`의 `MOCK_ISSUER_DAILY_LIMIT`·`MOCK_ISSUER_GLOBAL_DAILY_LIMIT`을 올리고 backend를 다시 띄웁니다. 전체 한도 200건은 20명이 10건씩 받으면 차므로, 여러 명이 함께 쓰는 실습이면 인원 × 사용자별 한도에 맞춰 올립니다(전체 한도에 닿으면 발급 화면에 "전체 모의 발급 한도(24시간)에 도달했습니다…"가 나옴).
`BE/.env`에 제공자의 ID·시크릿(`GOOGLE_*`·`GITHUB_*`·`NAVER_*`)을 넣었다면 "Google로 계속하기"·"GitHub로 로그인"·"네이버 로그인"으로도 가입·로그인할 수 있습니다. 계정은 이메일 하나에 하나이고, 같은 이메일의 계정이 이미 있으면 다음과 같습니다. 이메일·비밀번호로 가입한 계정에는 소셜 로그인이 붙지 않으므로(409) 이메일과 비밀번호로 로그인합니다. Google·GitHub는 제공자가 인증한 이메일만 받으므로(GitHub은 인증된 기본(primary) 이메일), 둘 중 하나로 만든 계정에는 같은 이메일의 다른 하나가 연동됩니다. 네이버는 이메일 인증 여부를 알려 주지 않아서 네이버 로그인은 같은 이메일의 기존 계정에 붙지 않고, 네이버로 만든 계정에도 Google·GitHub가 붙지 않습니다(409 "이미 다른 방법(이메일 또는 다른 소셜 로그인)으로 가입한 이메일입니다…"). 그때는 처음 가입한 방법으로 로그인합니다.
시드 계정(`user001@example.com` ~ `user045@example.com`, 비밀번호 `test1234`)은 화면 확인용입니다. 서명 원본이 없어 직무 비교에서는 근거로 쓰이지 않으므로, 새로 가입해 배지를 받으세요.

**② 연구자 지정**
공고·학습자료를 반입할 계정을 FE에서 가입한 뒤, 그 계정의 UUID를 확인합니다. 로그인 토큰을 셸 변수에 담아 두고(③에서도 씁니다), 이메일·비밀번호는 실제 값으로 바꿉니다.

```bash
TOKEN=$(curl -s -X POST http://localhost:3000/api/v1/users/login -H 'Content-Type: application/json' -d '{"email":"researcher@example.com","password":"<비밀번호>"}' | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
```

```bash
curl -s http://localhost:3000/api/v1/users/me -H "Authorization: Bearer $TOKEN" | python3 -c "import sys, json; print(json.load(sys.stdin)['data']['id'])"
```

출력된 UUID를 `BE/.env`의 `RESEARCHER_USER_IDS=`에 넣고(여러 명이면 쉼표로 구분) BE를 다시 띄웁니다.

```bash
(cd BE && docker compose up -d --force-recreate backend)
```

**③ 연구자: 공고·학습자료 반입**
요구사항마다 원문 인용(`quote`)과 원문 안의 정확한 위치(`start`, `end`, 글자 수 기준)가 있어야 합니다.
AI가 주석이 원문과 맞는지(예: "또는" 조건을 "그리고"로 바꾸지 않았는지) 검사하고, 틀리면 422로 이유를 알려 줍니다.
비교에는 `dataset_kind: "real"`이고 수집(`captured_at`) 7일 이내·게시 90일 이내인 공고만 쓰이므로, **이용 허가를 받은 실제 공고**를 반입하세요.
아래는 형식 예시입니다(회사·URL은 가상 값). 같은 터미널에서 ②의 `TOKEN`을 그대로 씁니다.

```bash
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > posting.json <<EOF
{
  "source": "manual", "external_id": "example-de-001",
  "source_url": "https://jobs.example.com/example-de-001",
  "company": "예시데이터(주)", "title": "주니어 데이터 엔지니어", "region": "서울", "seniority": "junior",
  "role_id": "data_engineer", "posted_at": "$NOW", "captured_at": "$NOW",
  "dataset_kind": "real", "rights_basis": "permission", "review_note": "게시처 허가를 받아 원문을 그대로 옮김",
  "raw_text": "SQL 평가 통과\nPython 또는 Scala 과정 수료\nSpark 실무 경험\n",
  "requirements": [
    {"key": "r0", "kind": "skill", "importance": "required", "quote": "SQL 평가 통과", "start": 0, "end": 9,
     "skill_ids": ["sql"], "minimum_evidence": "assessment"},
    {"key": "r1", "kind": "skill", "importance": "required", "quote": "Python 또는 Scala 과정 수료", "start": 10, "end": 31,
     "skill_ids": ["python", "scala"], "match": "any", "minimum_evidence": "completion"},
    {"key": "r2", "kind": "skill", "importance": "required", "quote": "Spark 실무 경험", "start": 32, "end": 43,
     "skill_ids": ["spark"]}
  ]
}
EOF
curl -s -X POST http://localhost:3000/api/v1/research/job-postings -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d @posting.json
```

```bash
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > resource.json <<EOF
{
  "source": "manual", "external_id": "example-spark-101",
  "source_url": "https://learning.example.com/spark-101",
  "title": "Apache Spark 입문 실습", "provider": "예시 교육기관",
  "raw_text": "Apache Spark 입문 실습\n무료",
  "skill_annotations": [{"skill_id": "spark", "quote": "Apache Spark", "start": 0, "end": 12}],
  "prerequisite_annotations": [], "prerequisites_reviewed": true,
  "level": "beginner", "minimum_study_hours": 4,
  "cost_krw": 0, "cost_annotation": {"quote": "무료", "start": 19, "end": 21},
  "captured_at": "$NOW", "available": true,
  "dataset_kind": "real", "rights_basis": "permission", "review_note": "제공처 허가를 받아 소개 문구를 그대로 옮김"
}
EOF
curl -s -X POST http://localhost:3000/api/v1/research/learning-resources -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d @resource.json
```

- 기술 ID: `sql`, `python`, `scala`, `spark`, `kafka`, `airflow`, `etl`, `data_modeling`, `git`, `linux`, `aws`, `docker`
- 원문에서 기술 후보만 먼저 보고 싶으면 `POST /api/v1/research/job-postings/preview`에 `{"raw_text": "..."}`를 보냅니다.
- 학습자료는 수집 30일 이내만 쓰입니다. 같은 `source`·`external_id`로 다시 반입하면 새 버전이 쌓이고 최신 버전이 쓰입니다.
- 각 필드의 규칙은 [BE/openapi.yaml](../BE/openapi.yaml)과 <http://localhost:3000/docs>를 참고하세요.

**④ 사용자: 직무 비교 → 학습 계획**
FE의 **직무 비교·커리큘럼** 화면에서 공고를 고르고 "검증하고 비교하기"를 누르면, BE가 모든 자격증명을 지금 다시 검증한 뒤 AI가 요구사항별로 비교합니다.
같은 화면에서 기간·주당 시간·예산을 정해 학습 계획을 만듭니다. 비교 결과는 5분 동안만 계획 생성에 쓸 수 있습니다.

## 6. Docker 없이 실행 (개발·디버깅)

DB만 컨테이너로 띄우고 BE와 AI를 각각 다른 터미널에서 실행합니다. `ai/.env`의 `AI_SERVICE_TOKEN`은 `BE/.env`와 같은 값이어야 합니다.

```bash
(cd BE && docker compose up -d postgres)
```

```bash
(cd BE && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m app.migrate && .venv/bin/python -m uvicorn main:app --reload --port 3000)
```

`app.migrate`는 `BE/.env`의 DB 접속 정보로 아직 적용하지 않은 마이그레이션만 적용합니다(이전 볼륨 업그레이드 포함, 최신이면 아무것도 하지 않음). `APP_ENV=production`이면 `--seed`를 거부하고, `DB_PASSWORD`가 비었거나 `password`면 접속하지 않으며, 공개된 시드 테스트 비밀번호를 쓰는 계정이 DB에 있으면 실패합니다(운영 절차는 [AWS 배포 체크리스트](../deploy/CHECKLIST.md)).

```bash
cp -n ai/.env.example ai/.env
```

```bash
(cd ai && python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/python -m uvicorn src.server.app:app --host 127.0.0.1 --port 8100)
```

로컬 실행 시 BE는 기본값 `AI_SERVICE_URL=http://127.0.0.1:8100`으로 AI에 접속합니다. FE는 4번과 같습니다.

## 7. (선택) 커리큘럼 LLM 모드

기본은 LLM 없이 만드는 템플릿 계획입니다. LLM(기본 공급자 Google Gemini API)을 쓰려면 `BE/.env`의 `CURRICULUM_LLM_USER_IDS`에 계정 UUID를 넣고, `ai/.env`(`cp -n ai/.env.example ai/.env`)에 `CURRICULUM_LLM_ENABLED=true`와 Google AI Studio에서 만든 `GEMINI_API_KEY`를 넣은 뒤 다시 띄웁니다. 키는 `ai/.env`에만 두고 저장소·FE에 넣지 않습니다.

```bash
(cd BE && docker compose up -d --force-recreate backend ai)
```

FE **직무 비교·커리큘럼** 화면에서 "LLM(외부 서비스 Google Gemini API)으로 단계 순서·시간을 배분" 동의를 체크해야 사용됩니다. 키를 넣은 뒤 연결은 `(cd ai && .venv/bin/python scripts/gemini_check.py)`로 한 번 확인합니다(실제 호출 1번. 이 스크립트는 Docker 이미지에 들어가지 않으므로 Compose로 띄웠어도 6번처럼 만든 `ai/.venv`로 실행). 키 발급·모델·데이터 정책·한도와 다른 공급자(OpenAI 호환 API, 로컬 Qwen(GPU)) 설정은 [ai/README.md](../ai/README.md) 6번을 보세요.

## 7-1. (선택) 직무별 채용 시장 통계 넣기

직무 비교 화면 위쪽의 "직무별 요구 역량 통계" 카드는 DB에 통계가 있을 때만 나타납니다(없으면 카드만 보이지 않고 나머지는 그대로 동작).
넣을 파일은 저장소의 `ai/data/market_snapshots/*.json`입니다. 팀이 수집한 공고를 AI 서비스로 집계한 뒤 `python -m app.jobs.market export`로 내보낸 직무별 최신 통계(빅데이터·개인정보보호·백엔드·프런트엔드·인프라 5개 직무)이며, 공고 원문은 들어 있지 않습니다.
Compose의 backend 컨테이너에는 `BE/`만 연결되므로 폴더를 먼저 `BE/` 아래로 복사한 뒤 실행하고, 끝나면 지웁니다(`BE/market_snapshots/`는 git이 무시).

```bash
cp -r ai/data/market_snapshots BE/market_snapshots
```

```bash
(cd BE && docker compose exec backend python -m app.jobs.market import --dir /app/market_snapshots)
```

```bash
rm -r BE/market_snapshots
```

Docker 없이 실행 중이면 복사 없이 `BE/`에서 `python -m app.jobs.market import --dir ../ai/data/market_snapshots`를 실행합니다. 이 명령은 `BE/.env`의 `DB_*`만 있으면 됩니다(AI 서비스는 부르지 않음).

- 종료 코드 0: 성공. 로그의 `가져온 통계: {'backend_developer': True, …}`에서 `True`는 새로 저장한 직무, `False`는 같은 내용이 이미 있어 넣지 않은 직무입니다(다시 실행해도 안전).
- 종료 코드 1: 아무것도 넣지 않았습니다. 이유는 `실패: …` 한 줄에 나옵니다: 폴더가 없거나 `*.json`이 없음(위 복사를 건너뜀), 형식이 틀리거나 해시가 맞지 않는 파일, 기준 시각(`as_of`)이 지금보다 5분 넘게 미래인 파일, 이름이 80자를 넘거나 경고가 20개·각 200자를 넘는 파일, 건수가 음수이거나 DB 정수 범위(2,147,483,647)를 넘는 파일, DB 접속 실패. 파일 검사에서 하나라도 걸리면 나머지도 넣지 않습니다(검사를 모두 통과한 뒤 저장하는 중에 DB 오류가 나면 그 앞의 파일은 이미 들어가 있고, 다시 실행하면 건너뜀).
- 종료 코드 2: 명령 인자 오류(`--dir` 빠짐 등)나 없는 수준 이름을 넣은 `LOG_LEVEL`(예: `verbose`). 아무것도 하지 않습니다.

공고를 새로 수집해 통계를 다시 만드는 방법과 수집 원칙은 [BE/README.md](../BE/README.md)의 "직무별 채용 시장 통계"를 보세요.

## 8. 테스트

BE 테스트는 테스트 전용 DB가 필요합니다(개발 DB는 건드리지 않음). 앱과 마이그레이션은 DB 확장을 쓰지 않지만, 예전 버전이 `pgcrypto`·`vector`를 만들어 둔 DB의 업그레이드를 재현하는 마이그레이션 테스트가 두 확장을 만들므로 Compose와 같은 pgvector 이미지를 씁니다.

```bash
docker run -d --name vc-be-test -e POSTGRES_PASSWORD=password -p 127.0.0.1:55432:5432 pgvector/pgvector:0.8.7-pg16-bookworm
```

```bash
(cd BE && python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q)
```

```bash
(cd ai && python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m pytest -q && .venv/bin/python scripts/sync_contract_to_be.py --check)
```

```bash
(cd FE && npm run lint && npm run build)
```

`requirements.txt`는 테스트한 버전을 `==`로 고정한 실행용 목록이고, 테스트 도구(pytest)는 `requirements-dev.txt`에 있습니다. 허용 범위는 `requirements.in`에서 고치고, BE·AI가 함께 쓰는 패키지는 같은 버전으로 맞춥니다(다시 만드는 방법은 각 `requirements.txt` 머리 주석).
GitHub Actions(`.github/workflows/tests.yml`)가 PR마다 같은 검사를 실행하고(job마다 20분 제한: 멈춘 테스트는 20분에 실패로 끝남), BE·AI Docker 이미지 빌드와 `BE/docker-compose.yml` 문법도 확인합니다(BE 이미지 안에서 `python -m app.migrate --help`와 `python -m app.jobs.market --help`가 뜨는지도 봄). FE 작업은 빌드한 `dist`에 외부 스크립트(`mcp.figma.com`, 다른 출처의 `<script src>`)나 개발 전용 안내(`localhost:3000`, `AI_SERVICE_TOKEN`·`AI_SERVICE_URL`, `DXWORKS_`, `GOOGLE_`, `GITHUB_`, `NAVER_`, `ai_contract`)가 있으면 실패합니다.
두 워크플로(`tests.yml`, Strix 보안 스캔 `main.yml`)는 읽기 전용 `GITHUB_TOKEN`(`permissions: contents: read`)으로 돌고, 체크아웃은 토큰을 git 설정에 남기지 않습니다(`persist-credentials: false`). 액션은 태그 대신 커밋 SHA와 버전 주석(`# vX.Y.Z`)으로 고정하고, `.github/dependabot.yml`이 매주 새 릴리스를 확인해 하나로 묶은 갱신 PR을 엽니다. Strix는 PyPI의 `strix-agent==1.7.0`으로 설치하므로 버전은 `main.yml`의 그 줄에서 올립니다. 비밀값(`STRIX_LLM`, `LLM_API_KEY`)은 Strix 실행 단계에만 넘깁니다. 이 규칙들은 BE 테스트 `tests/test_ci_workflows.py`가 검사합니다.
`ai/src/career/contracts/`(BE와 AI가 주고받는 형식)를 바꿨다면 `ai/`에서 `python scripts/sync_contract_to_be.py`로 BE 사본을 갱신하고 BE와 AI를 함께 다시 띄웁니다.

## 9. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| FE에 "BE 서버에 연결할 수 없습니다" | BE가 3000번에서 실행 중인지 (`curl http://localhost:3000/health`) |
| FE에 "AI 서버에 연결할 수 없습니다" | AI가 실행 중인지 (`curl http://localhost:8100/health`), BE의 `AI_SERVICE_URL` |
| "BE에 AI 서버 주소·토큰이 설정되지 않았습니다" | `BE/.env`의 `AI_SERVICE_TOKEN`이 비었거나 32바이트 미만 |
| "AI_SERVICE_TOKEN이 서로 다릅니다" | Docker 없이 실행할 때 `BE/.env`와 `ai/.env`의 토큰이 다름 |
| 발급·검증이 502/503 ("배지 발급·검증 서비스에서 오류가 발생했습니다" / "…지금 사용할 수 없습니다") | `DXWORKS_*` 값. 응답에는 `code`(`PROVIDER_ERROR`/`PROVIDER_NOT_CONFIGURED`)만 있고, 협력사 오류·빠진 설정 이름은 BE 로그의 `Provider error:` 줄에 있습니다(협력사가 되돌려 준 API 키·토큰·DID는 `[redacted]`로 가림. DID·JWT 모양이나 숫자가 섞인 32자 이상의 base64url 문자열은 보낸 값과 달라도 가리므로 협력사의 UUID·추적 ID·요청 ID도 `[redacted]`로 보이고 로그에 남지 않음. 글자와 `_`·`-`로만 된 긴 오류 코드는 그대로 보임. 그래서 되돌아온 값의 조각도 보낸 값 그대로가 아니고 숫자가 없으면 남을 수 있음). 503은 `DXWORKS_API_HOST`·`DXWORKS_API_KEY`·`DXWORKS_BADGE_TEMPLATE_ID` 중 필요한 값이 비어 있을 때입니다(`https://`가 아닌 호스트처럼 형식이 틀린 값은 BE가 시작할 때 거부). 502의 로그가 `DXWorks가 요청하지 않은 압축 응답을 반환했습니다`·`DXWorks 응답 본문이 너무 큽니다`·`전체 제한 시간 초과`·`호출 대기 시간 초과 (동시 호출 상한)`이면 BE가 협력사 응답에 건 한도(압축은 `gzip`·`deflate`만 상한 안에서 풀고 그 밖의 방식은 거부, 전송된 본문과 풀어낸 본문 각 1 MiB, 교환 한 번에 `DXWORKS_TIMEOUT_SECONDS`의 2배, 동시 16개와 그 자리 대기 2배)에 걸린 것입니다. AWS에서 `DXWorks 연결 실패: ConnectTimeout`(약 15초 뒤)이면 BE에서 나가는 길(NAT 게이트웨이·라우팅, 보안 그룹 아웃바운드, 협력사의 IP 허용 목록), `ConnectError`(바로)면 DNS·`DXWORKS_API_HOST`를 봅니다([deploy/README.md](../deploy/README.md) "문제 해결") |
| 운영 빌드 화면에 "잠시 후 다시 시도해주세요…"·"서버에서 일시적인 오류가 발생했습니다" | 운영 빌드(`npm run build`)는 서버 주소·설정 이름 같은 운영자용 안내를 보여 주지 않습니다(이 표의 안내 문구 일부는 `npm run dev`에서만). BE 로그의 `Provider error:`·`AI service error:` 줄과 브라우저 네트워크 탭의 응답 `code`를 확인 |
| API가 401 `Authentication required` (curl·Postman) | 토큰 없이 부름. 회원가입·로그인·`/health`·공유 링크 조회 말고는 `-H "Authorization: Bearer $TOKEN"`이 필요합니다(5번 ②의 `TOKEN`). `AUTH_REQUIRED=false`로는 끌 수 없습니다 |
| BE 시작 로그에 `AUTH_REQUIRED=false 설정은 더 이상 쓰지 않습니다` | 예전 `BE/.env`의 `AUTH_REQUIRED` 줄을 지움 (인증은 항상 켜져 있음) |
| 로그인이 "로그인 시도가 너무 많습니다." (429 `Too many attempts. Try again later.`) | 같은 이메일로 15분 안에 10번 틀림 → 응답의 `Retry-After`(초) 뒤에 다시 시도. 로그인에 성공하면 다시 세고, BE 프로세스마다 메모리에서 세므로 로컬은 BE를 다시 시작해도 지워짐. DB가 바빠 503 `Service is busy. Try again later.`로 답한 로그인 시도는 세지 않음(비밀번호를 확인하지 않았으므로 다시 시도해도 잠기지 않음). 운영에서 가입도 429면("회원가입 시도가 너무 많습니다.") WAF의 IP별 속도 제한([AWS 배포 체크리스트](../deploy/CHECKLIST.md). FE 로그인·가입 화면은 WAF·로드밸런서의 429도 본문과 관계없이 같은 문구로 보여 줌) |
| 발급 화면에 "이 서버에서는 모의 발급을 사용할 수 없습니다" (404 `Mock issuer is disabled`) | `MOCK_ISSUER_ENABLED`가 꺼짐. 비워 두면 `APP_ENV=production`(Docker 이미지 기본값)에서는 꺼지므로 시연 배포는 `MOCK_ISSUER_ENABLED=true`를 넣음 |
| 발급이 403 `Template not allowed` 또는 429 `Mock issuance limit reached; try again later`("모의 발급 한도(24시간)를 넘었습니다…") / 429 `Mock issuance service limit reached; try again later.`("전체 모의 발급 한도(24시간)에 도달했습니다…") / 429 `Mock issuance is paused after repeated failures. Try again shortly.`("발급 서버 오류가 이어져 모의 발급을 잠시 멈췄습니다…") | 403: 발급 화면의 Template ID를 비우거나 그 ID를 `BE/.env`의 `MOCK_ISSUER_TEMPLATE_IDS`에 추가. 첫 번째 429(그 사용자의 한도): 사용자별 24시간 한도 `MOCK_ISSUER_DAILY_LIMIT`(기본 10)에 닿았거나, 협력사 호출을 시도한 요청이 그 사용자에게서 24시간에 `max(30, 3 × MOCK_ISSUER_DAILY_LIMIT)`번을 넘음(성공과 연결 실패를 포함한 실패를 모두 셈. BE 프로세스마다 셈). 두 번째 429(전체 한도. 발급을 적게 한 사용자도 받음): 모든 사용자를 합친 24시간 한도 `MOCK_ISSUER_GLOBAL_DAILY_LIMIT`(기본 200)에 닿음(DB로 세는 이 한도. 그 밖에는 실패한 것 없이 한도만큼의 발급 요청이 그 BE 프로세스에서 한꺼번에 진행 중일 때도 이 문구). 협력사가 거절·실패로 답했거나 요청이 닿지 않은 발급은 두 발급 한도(DB)에 세지 않고(그 기록을 지우는 DB 작업까지 실패한 경우만 남음), 응답 대기 중 시간 초과처럼 결과를 모르는 실패는 배지가 이미 발급됐을 수 있어 셉니다(남은 기록과 결과를 모르는 실패는 그 사용자의 한도에는 24시간, 전체 한도에는 10분 동안만 셈. 그래서 시간 초과로 이어진 장애가 전체 한도를 채워도 협력사가 돌아온 뒤 길어야 10분에 1분쯤 더 지나면 다시 발급됨) → 로컬은 값을 올리고 backend 재시작(사용자별 시도 한도는 프로세스 메모리에 있어 재시작하면 지워짐). 세 번째 429(`Retry-After` 헤더 있음. 한도에 닿은 것이 아님): 협력사 호출을 시도했는데 성공하지 못한 발급이 그 BE 프로세스에서 24시간에 `MOCK_ISSUER_GLOBAL_DAILY_LIMIT`번 쌓여 발급을 멈춘 상태입니다. 협력사가 계속 실패하고 있는 것이므로 `Provider error:` 로그를 먼저 확인합니다. 멈춘 동안 BE는 1분에 한 건만 협력사로 보내 보고(그 밖의 요청은 협력사를 부르지 않고 이 429), 그 한 건이 성공하면 실패 기록을 지워 모두 바로 다시 발급할 수 있습니다(재시작하거나 24시간을 기다릴 필요 없음) → 협력사가 돌아온 뒤 `Retry-After`(1~60초) 뒤에 다시 시도 |
| 발급·검증이 403 `Forbidden: can only issue to your own DID` / `Forbidden: credential was issued to a different DID` | `holderDid`나 배지의 수신자 DID가 로그인한 계정의 DID와 다름(다른 계정 DID로 발급했거나 다른 계정의 배지를 검증함) → 지금 로그인한 계정의 DID로 다시 발급해 검증 |
| 공유 링크 조회가 503 `Share verification is busy. Try again later.` | 그 BE 태스크의 기업용 조회 검증기 호출 한도(분당 300번, 최근 60초 안에 확인한 원본은 세지 않음)를 넘음 → 응답의 `Retry-After`(초) 뒤에 다시 열기 |
| 검증·재검증·포트폴리오 저장·직무 비교가 429 `Too many verification requests. Try again later.` ("검증 요청이 너무 많습니다…") | 그 BE 태스크에서 검증기(DXWorks) 호출 몫이 모자람: 계정마다 분당 120번·시간당 1,200번, 태스크 전체로 분당 600번이고 요청 하나가 서명 원본 수만큼 씁니다. 발급 화면의 배지 검증(`POST /api/v1/verify`)은 검증기까지 가서 토큰이 거절된 요청(저장되지 않은 `success: false`, 403·409, 그 토큰 하나에 대한 검증기 오류 502)이 그 계정에서 10분에 10번을 넘어도 같은 429입니다(로그의 `route=verify_rejected`. 폐기·만료 상태로 저장된 배지와 DB가 바빠 503으로 끝난 검증은 세지 않음). 어느 경우든 DXWorks를 부르지 않았고 아무것도 저장하지 않았으므로 응답의 `Retry-After`(초. 모자란 몫이 다시 찰 때까지의 시간 중 가장 긴 값) 뒤에 다시 시도합니다. BE 로그에 `event=verifier_budget_denied` 줄이 남습니다 |
| 여러 화면이 503 `Service is busy. Try again later.` ("요청이 많아 처리가 지연되고 있습니다…") | DB가 바쁨. BE 로그의 `Database busy: method=… path=… reason=…` 줄에서 `reason`이 `DatabaseBusy`면 그 BE 태스크의 DB 연결 자리(`DB_MAX_CONNECTIONS`, 기본 20)를 5초 안에 얻지 못한 것, `QueryCanceled`면 DB 문장이 15초를 넘은 것, `LockNotAvailable`이면 잠금을 5초 안에 얻지 못한 것, `OperationalError: …`면 DB에 접속하지 못했거나 연결이 끊긴 것(뒤의 요약이 원인: 접속 거부·시간 초과·비밀번호 등)입니다. 응답의 `Retry-After`(5초) 뒤에 다시 시도하고, 계속되면 오래 걸리는 쿼리·잠금을 쥔 세션(`pg_stat_activity`)을 확인합니다 |
| 공고 목록·직무 비교·학습 계획이 429 `Too many analysis requests. Try again later.` ("분석 요청이 너무 많습니다…") | 그 계정이 AI 묶음 요청을 동시에 2개 넘게 보냈거나 분당 30번·시간당 300번을 넘음(BE 태스크마다 셈). DB·AI를 부르지 않았으므로 `Retry-After`(초) 뒤에 다시 시도. BE 로그에 `event=corpus_budget_denied` 줄이 남습니다 |
| 직무 비교·학습 계획이 429 `Daily … limit reached …` ("오늘 만들 수 있는 … 수를 모두 썼습니다…") | 그 계정의 24시간 한도: 직무 비교 200건, 학습 계획 200건, LLM 모드 학습 계획 `CURRICULUM_LLM_DAILY_LIMIT`건(기본 20. 템플릿 모드는 그대로 쓸 수 있음). `Retry-After`는 가장 오래된 건이 24시간을 지날 때까지의 초. BE 로그에 `event=daily_limit_denied` 줄이 남습니다 |
| LLM 모드 학습 계획이 429 `An LLM curriculum is already being generated for this account. …` ("이 계정의 LLM 학습 계획을 이미 만들고 있습니다…") | 같은 계정의 LLM 모드 계획이 아직 끝나지 않음(계정당 한 번에 한 건, BE 태스크마다 셈). Gemini·하루 몫을 쓰지 않았으므로 앞의 계획이 끝난 뒤 다시 시도. BE 로그에 `event=llm_in_flight_denied` 줄이 남습니다 |
| 공고 목록·직무 비교·학습 계획이 503 `code: AI_SERVICE_BUSY` ("분석 요청이 많습니다…") | 그 BE 태스크가 AI로 동시에 보내는 공고·학습자료 묶음 요청이 `AI_MAX_CONCURRENCY`(기본 4)개로 찬 채 10초가 지남. AI를 부르지 않았으므로 `Retry-After`(5초) 뒤에 다시 시도(직무 비교는 자격증명 재검증을 마친 뒤에 자리를 기다리므로, 이 503으로 끝나도 그 요청의 검증기 호출 몫은 이미 쓴 상태) |
| 포트폴리오·공유 링크 만들기가 409 `Portfolio limit reached.` / `Share link limit reached. Revoke unused links first.` | 사용자별 상한(포트폴리오 100개, 철회·만료되지 않은 공유 링크 100개) → 쓰지 않는 포트폴리오를 지우거나 링크를 철회. 두 목록 조회는 한 번에 200개까지 돌려줍니다 |
| 소셜 로그인 버튼이 "<제공자> 로그인을 지금 사용할 수 없습니다"(개발 서버에서는 "BE에 … OAuth 설정이 없습니다 …") (503 `Google OAuth is not configured`·`GitHub OAuth is not configured`·`Naver OAuth is not configured`) | 그 제공자의 `*_CLIENT_ID`·`*_CLIENT_SECRET`(예: `GITHUB_CLIENT_ID`·`GITHUB_CLIENT_SECRET`) 중 빈 값이 있음 → `BE/.env`에 둘 다 넣고 backend 재시작. 503 `<Label> OAuth redirect_uri is not configured`면 그 제공자의 콜백 주소(`*_REDIRECT_URI`)가 빈 값이거나 운영에서 넣지 않음 → 예시 파일 값(로컬)이나 FE의 `https://` 콜백 주소(운영)를 넣음 |
| 소셜 로그인 버튼이 "허용되지 않은 로그인 경로입니다…"(개발 서버에서는 "BE가 허용하지 않은 콜백 주소입니다 …") (400 `Invalid redirect_uri`) | FE가 보낸 콜백 주소(`<FE 주소>/auth/{google,github,naver}/callback`)가 BE 설정과 글자 그대로 같지 않음(Google은 `GOOGLE_REDIRECT_URI`·`GOOGLE_ALLOWED_REDIRECT_URIS` 중 하나, GitHub·네이버는 `GITHUB_REDIRECT_URI`·`NAVER_REDIRECT_URI` 하나): FE를 `http://localhost:5173`이 아닌 주소로 열었거나, 운영(`APP_ENV=production`)에서 콜백 주소를 FE 도메인과 다르게 넣음(콜백 주소를 아예 비웠으면 400이 아니라 503 `… redirect_uri is not configured`) |
| 소셜 로그인 뒤 "이 이메일은 <제공자>로 로그인할 수 없습니다" (409 `This email is registered with a password. Log in with your email and password.` 또는 `This email is already used by another sign-in method. Log in with that method.`) | 같은 이메일의 계정이 이미 있는데 붙일 수 없음. 첫째(비밀번호 계정): BE는 비밀번호가 있는 계정에 소셜 로그인을 붙이지 않습니다(가입은 이메일 소유를 확인하지 않으므로, 남의 주소로 먼저 만들어 둔 계정에 그 주소의 주인을 들이지 않기 위함) → 이메일과 비밀번호로 로그인. 둘째(다른 방법): 네이버 로그인이거나 그 계정이 네이버로 만든 계정이라 이메일이 양쪽에서 확인되지 않음(같은 이메일로 연동되는 것은 Google·GitHub끼리뿐. BE 감사 로그 `outcome=refused_unverified_link`) → 처음 가입한 방법으로 로그인 |
| 소셜 로그인 뒤 "이메일 제공 동의가 필요합니다." (400 `OAuth email is required`) / "<제공자> 계정의 이메일이 인증되지 않아 로그인할 수 없습니다." (400 `OAuth email is not verified`) | 첫째: 제공자가 이메일을 주지 않음(동의 화면에서 이메일 제공을 거절했거나 네이버 제공 정보에 이메일이 없음. GitHub은 승인 화면에서 이메일 권한(`user:email`)을 뺐거나 이메일 목록에 기본(primary) 이메일이 없음: 화면은 "GitHub 계정의 이메일을 확인할 수 없습니다. 이메일 권한(user:email)을 허용했는지 확인한 뒤 다시 시도해주세요.") → 콘솔 설정을 확인하고 이메일 제공에 동의해 다시 시도(GitHub은 Settings > Applications > Authorized OAuth Apps에서 그 앱의 승인을 취소한 뒤 다시 로그인하면 승인 화면이 다시 나옴). 둘째: Google·GitHub가 그 이메일을 인증되지 않은 이메일로 알림(GitHub은 기본 이메일의 `verified`가 참이어야 하고, 토큰 단계의 `unverified_user_email`도 같음: 화면은 "GitHub 계정의 기본 이메일이 인증되지 않았습니다. GitHub에서 이메일 인증을 마친 뒤 다시 시도해주세요.") → 제공자 계정에서 이메일을 인증 |
| 소셜 로그인이 "로그인 인증이 만료되었습니다…" (400) / "<제공자> 로그인 서비스에 일시적인 문제가 있습니다" (502 `Google OAuth error`·`GitHub OAuth error`·`Naver OAuth error`) / "로그인 시도가 너무 많습니다" (429) | 400: 인가 코드가 만료·재사용됨(GitHub `bad_verification_code`). BE 로그에 `Google OAuth token request rejected: error=redirect_uri_mismatch` 경고가 있으면 Google Cloud Console의 승인된 리디렉션 URI가 `GOOGLE_REDIRECT_URI`와 다른 것, `GitHub OAuth token request rejected: error=redirect_uri_mismatch`이면 GitHub OAuth App의 Authorization callback URL이 `GITHUB_REDIRECT_URI`와 다른 것, `Naver OAuth token request rejected: error=…`이면 네이버 Client ID·Secret·Callback URL을 확인. 502: 제공자 응답 실패(GitHub Client ID·secret이 틀린 `incorrect_client_credentials`는 BE 로그에 `GitHub OAuth token request rejected: error=incorrect_client_credentials` 경고도 남음)로, 원인은 BE 로그의 `Provider error:` 줄. 429: 그 BE 태스크의 그 제공자 토큰 교환 한도(제공자별 분당 300번, 모든 사용자가 함께 씀) → `Retry-After`(초) 뒤에 다시 시도 |
| `POST /api/v1/verify`가 400 `Identity verification is disabled` | `type: identity`(신원 VP 검증)는 기본으로 꺼져 있음. 화면은 쓰지 않으며, 직접 써야 할 때만 `IDENTITY_VERIFICATION_ENABLED=true`(사용자가 보낸 값에 회사 API 키를 붙여 DXWorks로 보내는 호출) |
| `/docs`·`/redoc`·`/openapi.json`이 404 | `APP_ENV=production`(Docker 이미지 기본값)에서는 API 문서를 제공하지 않음 → 계약은 [BE/openapi.yaml](../BE/openapi.yaml)로 보고, 로컬은 `APP_ENV=development` |
| 끝에 `/`를 붙인 경로가 404 (예: `/api/v1/portfolios/`, `/health/`) | BE는 끝의 `/`를 리디렉션하지 않음 → `/` 없이 호출 (로드밸런서 상태 확인 경로도 `/health`). 끝에 줄바꿈이 붙은 경로(`/api/v1/users/login%0A` 등)도 404 `Not Found` |
| 요청이 413 `Content Too Large` | 요청 본문이 그 경로의 상한을 넘음: 기본 64 KiB, 로그인·회원가입·소셜 로그인 콜백(`POST`) 16 KiB, `POST /api/v1/verify` 320 KiB(VC 토큰은 256 KiB까지), 연구자 공고·학습자료 반입과 미리보기(`POST /api/v1/research/*`) 2 MiB(공고 원문은 10만 자까지). 기본 상한을 쓰는 경로의 정상 요청은 가장 큰 것(커리큘럼 요청, 대안 선택 200개)도 35 KB쯤이라 닿지 않음 ([BE/README.md](../BE/README.md) "요청 본문 상한") |
| 422 응답에 보낸 값이 없음 | 요청 검증 실패(422) 본문은 오류마다 `type`·`loc`·`msg`만 싣고(받은 값 `input`·`ctx` 없음) 앞에서부터 20개까지만 실립니다. 64자를 넘는 키 이름은 `loc`에 `<long key>`로 나옵니다 |
| 422 `must be a single line without control characters` / `must not contain NUL or unpaired surrogate characters` / `Input should be a valid string, unable to parse raw data as a unicode string` | 이름·이메일·포트폴리오 제목·희망 직무 안에 줄바꿈·탭 같은 제어 문자가 있거나, 입력에 NUL(`\u0000`, 두 번째 메시지)·짝 없는 서로게이트(예: JSON `\ud800`, 세 번째 메시지)가 있음 → 지우고 다시 보냄 |
| 직무 비교 화면에 공고가 없음 | 연구자 반입 공고가 없거나, `dataset_kind`가 `real`이 아니거나, 수집 7일·게시 90일이 지남 |
| 공고 반입이 403 | 그 계정 UUID가 `RESEARCHER_USER_IDS`에 없음 (넣은 뒤 backend 재시작). 연구자인지를 본문 형식 검사보다 먼저 확인하므로, 연구자가 아니면 본문이 틀려도 422가 아니라 403 |
| 공고 반입이 422 | 응답 `detail`의 규칙 코드 (예: `ANNOTATED_SKILL_NOT_MENTIONED_IN_QUOTE`), `start`/`end` 위치. `TEXT_EXPANDS_TOO_MUCH_WHEN_NORMALIZED`면 원문·인용에 NFKC 정규화로 크게 늘어나는 호환 문자(예: U+FDFA)가 많음 → 풀어 쓴 문자로 고쳐 다시 반입 |
| 로그인 화면에 "로그인 시간이 만료되었습니다" | 토큰 만료(BE `JWT_EXPIRES_MINUTES`, 기본 1440분), 같은 계정이 다른 브라우저·기기에서 로그아웃함, DB를 초기화해 계정이 없어짐, 이전 버전에서 로그인해 토큰이 없음, 또는 `BE/.env`의 `JWT_SECRET`이 빈 채로 BE가 재시작됨 → 다시 로그인 (`JWT_SECRET` 설정) |
| 학습 계획 생성이 409 | 비교 결과 5분이 지남 → 다시 비교 |
| BE가 시작하지 않음 `APP_ENV=production requires ...` | `APP_ENV=production`(Docker 이미지 기본값)인데 오류에 나온 값이 없거나 쓸 수 없음: `JWT_SECRET`(32바이트 이상, 저장소의 테스트·자리표시 값 아님)·`HOLDER_KEY_ENCRYPTION_KEY`(테스트 키 아님)·`SERVICE_API_KEY`(비우거나 32바이트 이상 임의 값)·`DB_HOST`·`DB_NAME`·`DB_USER`(빈 값 아님)·`DB_PASSWORD`(빈 값·`password`가 아닌 값) → 2번 명령으로 새로 만든 값을 넣거나, 로컬이면 `APP_ENV=development`. `PGSSLMODE (verify-full or verify-ca in production)`·`PGSSLROOTCERT (readable CA bundle file)`은 운영 DB TLS 설정 → 작업 정의에 `PGSSLMODE=verify-full`을 넣고 `PGSSLROOTCERT`는 넣지 않거나 이미지 경로(`/app/certs/rds-global-bundle.pem`) 그대로 둠(빈 값·앞뒤 공백·줄바꿈 금지, [AWS 배포 체크리스트](../deploy/CHECKLIST.md)의 "RDS TLS"). 이 오류들은 traceback 없이 이 한 줄(stderr, 종료 코드 1)만 남습니다. `<이름> (not the same value as <다른 이름>)`은 `JWT_SECRET`·`HOLDER_KEY_ENCRYPTION_KEY`·`SERVICE_API_KEY`·`AI_SERVICE_TOKEN`·`DXWORKS_API_KEY`·`GOOGLE_CLIENT_SECRET`·`GITHUB_CLIENT_SECRET`·`NAVER_CLIENT_SECRET` 중 두 값이 같음(같은 쌍마다 하나씩 나옴) → 앞쪽 변수를 2번 명령으로 새로 만듦(`AI_SERVICE_TOKEN`은 BE와 AI에 같은 새 값, `SERVICE_API_KEY`는 비워도 됨. `JWT_SECRET`을 바꾸면 모든 사용자가 한 번 로그아웃됨. 협력사가 발급한 `DXWORKS_API_KEY`는 앞쪽에 나오지 않음. 제공자가 발급한 `GOOGLE_CLIENT_SECRET`·`GITHUB_CLIENT_SECRET`·`NAVER_CLIENT_SECRET`은 `DXWORKS_API_KEY`나 이 순서로 자기보다 앞의 시크릿과 같을 때만 앞쪽이고 그 제공자 콘솔에서 새 시크릿을 만듦(GitHub은 OAuth App의 Generate a new client secret). `HOLDER_KEY_ENCRYPTION_KEY`는 `DXWORKS_API_KEY`·`*_CLIENT_SECRET`과 같을 때만 앞쪽인데, 바꾸면 기존 홀더 키를 복호화할 수 없으므로(재암호화 도구 없음) 가입자가 있는 DB면 먼저 팀에서 정함). `GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET (set both, or leave both empty)`·`GOOGLE_REDIRECT_URI (set explicitly when Google OAuth is configured)`·`GOOGLE_REDIRECT_URI and GOOGLE_ALLOWED_REDIRECT_URIS (https:// addresses only)`는 Google 로그인 설정: ID와 시크릿을 함께 넣고(쓰지 않으면 둘 다 비움), `GOOGLE_REDIRECT_URI`에 FE의 `https://` 콜백 주소를 명시하고, 허용 목록에 `https://`가 아닌 주소를 두지 않음. GitHub·네이버도 같은 꼴(`GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET (set both, or leave both empty)`, `GITHUB_REDIRECT_URI (set explicitly when GitHub OAuth is configured)`, `GITHUB_REDIRECT_URI (https:// address only)`, 네이버는 `NAVER_`·`Naver`)이고 고치는 방법도 같음. `CORS_ORIGINS (scheme://host[:port] entries only: no *, null, path or trailing slash)`는 `*`·`null`이나 경로·끝의 `/`가 붙은 항목이 있음 → `https://app.example.com` 꼴로만 넣거나 비움 |
| BE가 시작하지 않음 `<변수 이름> must be ...` | 숫자 설정이 범위를 벗어남(비우면 기본값): `JWT_EXPIRES_MINUTES` 1~1440 정수(1440), `MOCK_ISSUER_DAILY_LIMIT`·`MOCK_ISSUER_GLOBAL_DAILY_LIMIT`·`AI_MAX_CONCURRENCY` 1 이상 정수(10·200·4), `DB_MAX_CONNECTIONS` 2 이상 정수(20), `DXWORKS_TIMEOUT_SECONDS`·`OAUTH_TIMEOUT_SECONDS`(없거나 비면 예전 이름 `GOOGLE_OAUTH_TIMEOUT_SECONDS`를 읽고 오류에도 그 이름이 나옴) 1~120초(15·10), `AI_TIMEOUT_SECONDS`·`AI_GENERATION_TIMEOUT_SECONDS` 1~600초(30·90). `DXWORKS_API_HOST must be an https:// URL ...`은 `https://` 주소가 아니거나(`http://`는 루프백만) 사용자 정보·쿼리·프래그먼트·공백이 있거나 포트가 1~65535 밖임, `DXWORKS_API_KEY must contain only ASCII characters`는 키에 ASCII가 아닌 문자가 있음 |
| BE가 시작하지 않음 `APP_ENV must be one of ...` | `APP_ENV` 오타(예: `prod`) → `development` 또는 `production` |
| `docker compose up`이 `migrate` 오류로 멈춤 | `(cd BE && docker compose logs migrate)`. "적용한 뒤 SQL이 바뀐 마이그레이션"이면 적용된 파일을 되돌리고 바꿀 내용은 다음 번호 파일로 추가 ([BE/README.md](../BE/README.md) "DB 마이그레이션"). `대소문자만 다른 이메일을 쓰는 계정이 있어 users_email_lower_key 인덱스를 만들 수 없습니다 (겹치는 이메일 N개)`면 13 마이그레이션이 멈춘 것(13은 통째로 롤백되고 그 앞 번호까지는 적용된 채로 남음. 운영 마이그레이션 태스크도 같음) → 그 아래 `HINT:` 줄의 조회로 겹치는 이메일을 찾아 이메일마다 계정을 하나만 남긴 뒤 다시 실행 |
| 시장 통계 `import`가 종료 코드 1 (`실패: 통계 JSON 파일이 없습니다: …` 등) | `--dir` 폴더가 없거나 `*.json`이 없음(Compose는 `BE/market_snapshots`로 먼저 복사) 또는 파일이 검사를 통과하지 못함 → 7-1번의 종료 코드 설명 |
| 운영 마이그레이션 태스크가 종료 코드 1 (`마이그레이션 실패: ...`) | `--seed`를 줌(운영에서는 거부) / 공개된 시드 테스트 비밀번호를 쓰는 계정이 있음(개발 DB 덤프) / `DB_PASSWORD`가 비었거나 `password` / `PGSSLMODE`가 `verify-full`·`verify-ca`가 아니거나 `PGSSLROOTCERT`가 읽을 수 있는 파일이 아님(`마이그레이션 실패: APP_ENV=production requires PGSSLMODE (verify-full or verify-ca in production), …`. 접속하기 전에 바로 끝남 → 작업 정의에 `PGSSLMODE=verify-full`) / `DB_APP_ROLE` 역할이 없거나 특권 계정(그 역할이 속한 역할에 슈퍼유저·`CREATEROLE`·`CREATEDB`·`REPLICATION`·`BYPASSRLS` 속성이 있으면 로그 줄에 그 역할 이름이 나옴) / `DB_APP_ROLE`이나 그 역할이 속한 역할(`NOINHERIT`·`WITH INHERIT FALSE` 소속 포함)이 `public` 스키마·DB에 CREATE 권한을 가지거나(PostgreSQL 14 이하에서 만들었거나 업그레이드·복원한 DB, `PUBLIC`에 준 DB CREATE 등) DB·`public` 스키마를 소유함 → 로그 줄이 걸린 검사(`public 스키마`·`DB "<DB 이름>"`), 그 권한·소유를 가진 소속 역할과 함께 알려 주는 GRANT·ALTER ... OWNER·REVOKE 문(예: 마이그레이션 계정이 슈퍼유저가 아니면 맨 앞의 `GRANT "<소유한 역할>" TO "<마이그레이션 계정>" WITH INHERIT TRUE;`, `ALTER DATABASE "<DB 이름>" OWNER TO "<마이그레이션 계정>";`, `ALTER SCHEMA public OWNER TO pg_database_owner;`, 지금 소유자 몫의 CREATE를 거둔 상태면 그 뒤의 `GRANT CREATE ON SCHEMA public TO pg_database_owner;`, `REVOKE CREATE ON SCHEMA public FROM PUBLIC;`, `REVOKE CREATE ON DATABASE "<DB 이름>" FROM "<소속 역할>";`)을 마이그레이션 계정으로 나온 순서대로 실행 / `DB_APP_ROLE`이나 그 역할이 속한 역할, 기본 제공 역할(`pg_database_owner`·`pg_monitor` 등), `public` 스키마에 CREATE 권한이 있거나 DB·`public` 스키마를 소유한 다른 역할(마이그레이션 계정·OID 10 제외)이 `public` 스키마에 객체를 소유함 → 로그 줄의 객체(10개까지)를 마이그레이션 계정으로 확인해 예상하지 못한 것은 `DROP`, 앱에 필요한 것은 `ALTER <종류> <이름> OWNER TO "<마이그레이션 계정>"`(앱 테이블이 나오면 DB·테이블을 소유하지 않은 다른 관리 계정으로 실행한 것이므로 소유한 계정(마스터 사용자)으로 다시 실행) / `DB_APP_ROLE`이나 그 역할이 속한 역할(또는 `PUBLIC`)이 `public` 테이블에 `TRIGGER` 권한이 있음(예전에 준 `GRANT ALL` 등) → 로그 줄의 `REVOKE TRIGGER ON ALL TABLES IN SCHEMA public FROM ...;`을 마이그레이션 계정으로 실행하고, 그 권한으로 만든 트리거는 REVOKE 뒤에도 남으므로 같은 줄에 나온 `public` 테이블의 트리거 중 예상하지 못한 것은 `DROP TRIGGER` / `public` 테이블의 트리거에 WHEN 조건이 있거나 그 트리거가 임시 스키마의 함수나 그 역할들이 소유한 함수를 부름 → 로그 줄의 트리거(10개까지)를 확인해 예상하지 못한 것은 `DROP TRIGGER`(임시 스키마 함수를 부르는 트리거는 그 함수를 만든 세션이 끝나면 없어짐) / `DB_APP_ROLE`이나 그 역할이 속한 역할(또는 `PUBLIC`)이 `public` 테이블에 `REFERENCES` 권한(열 단위 포함)이 있음 → 로그 줄의 `REVOKE REFERENCES ON ALL TABLES IN SCHEMA public FROM ...;`을 마이그레이션 계정으로 실행하고, 그 권한으로 만든 외래 키는 REVOKE 뒤에도 남으므로 같은 줄에 나온 `public` 밖 테이블의 외래 키 중 예상하지 못한 것은 `ALTER TABLE <스키마>.<테이블> DROP CONSTRAINT <이름>;` / 그 역할들(또는 `PUBLIC`)이 원장 `public.schema_migrations`에 `INSERT`·`UPDATE`·`DELETE`·`TRUNCATE` 권한이 있음 → 로그 줄의 `REVOKE ALL ON public.schema_migrations FROM ...;`을 실행하고, 그 권한이 있던 동안 바뀐 원장 행이 남으므로 원장이 실제로 적용한 마이그레이션과 맞는지 확인 / `DB_APP_ROLE`이나 그 역할이 속한 역할이 DB의 스키마를 소유함(예전 DB CREATE 권한으로 만든 것 등) → 그 안의 객체를 확인한 뒤 로그 줄의 `DROP SCHEMA "<스키마>" CASCADE;`나 `ALTER SCHEMA "<스키마>" OWNER TO "<마이그레이션 계정>";`(슈퍼유저가 아니면 그 앞의 `GRANT "<소유자>" TO "<마이그레이션 계정>" WITH INHERIT TRUE;`부터. ALTER SCHEMA는 그 안 객체의 소유자를 바꾸지 않으므로 예상하지 못한 객체는 따로 지움) → [AWS 배포 체크리스트](../deploy/CHECKLIST.md)의 "DB 마이그레이션"(RDS 마스터 사용자로 소유자를 바꾸는 방법 포함) |
| BE 로그에 `permission denied for table ...` (API 500) | 서비스 계정(`DB_APP_ROLE`)으로 바꾼 뒤 새 이미지의 마이그레이션 태스크를 `DB_APP_ROLE` 없이 실행함 → `DB_APP_ROLE`을 넣어 다시 실행 |
| DB 접속 오류 `root certificate file ... does not exist`·`certificate verify failed`·`does not match host name`, 또는 운영 BE가 `DB startup check failed: …` 한 줄을 남기고 끝남(종료 코드 3) | `PGSSLROOTCERT`가 RDS CA 묶음 파일을 가리키는지(이미지 기본값 `/app/certs/rds-global-bundle.pem`), `DB_HOST`가 RDS 엔드포인트 이름 그대로인지(CNAME·IP 아님). 시작 점검 줄의 요약(`check DB_HOST and DB_PORT`·`check PGSSLROOTCERT: RDS CA bundle`·`run the migration task first` 등)별 원인은 [deploy/README.md](../deploy/README.md) "문제 해결" |

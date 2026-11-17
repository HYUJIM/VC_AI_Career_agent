# 배포 체크리스트 (NCP 서버 한 대 + Docker Compose)

배포 순서와 명령은 [deploy/README.md](README.md), 파일별 변수 표는 [deploy/ENV_MATRIX.md](ENV_MATRIX.md), 로컬 실행·문제 해결은
[서비스 실행 방법](../docs/GETTING_STARTED.md)에 있고, 이 체크리스트는 항목별 기준입니다.

구성: NCP 서버 한 대(Ubuntu, 2 vCPU / 4 GB / SSD 50 GB / 공인 IP 1개)의 [docker-compose.yml](docker-compose.yml). nginx가 FE 정적 파일·HTTPS·IP별
요청 제한을 맡고 `/api/*`를 backend(BE, 3000)로 넘기며, BE는 ai(8100)와 postgres(5432, TLS)에 서버 안 Docker 네트워크로만 닿습니다. 아래
`<도메인>`은 `deploy/.env`의 `SERVER_NAME`입니다. `BE/docker-compose.yml`은 로컬 개발 전용(소스 마운트·`--reload`·root·기본 비밀번호 DB)이고 공개된
비밀번호의 시드 계정(`user001@example.com` 등)도 만들므로 이 서버나 다른 공개 호스트에서 띄우지 않습니다.
인증은 설정과 관계없이 항상 켜져 있습니다(토큰 없는 요청은 401, 예전 `AUTH_REQUIRED` 변수는 없어짐). 모의 발급(FE `/issue` 화면)은 운영 이미지
(`APP_ENV=production`)에서 기본으로 꺼져 있고, `deploy/backend.env.example`이 시연용으로 `MOCK_ISSUER_ENABLED=true`를 넣어 둡니다. 운영 이미지에서는
API 문서(`/docs`·`/redoc`·`/openapi.json`)와 BE 단독 테스트용 `GET /api/v1/users/oauth/{google,github,naver}/callback`이 404이고, 신원 VP 검증
(`type: identity`)은 꺼져 있습니다.
아래에 나오는 BE 자체 한도(로그인 실패 제한, 검증기 호출 한도와 소셜 로그인(제공자별) 토큰 교환 한도, 배지 검증의 거절 창, 모의 발급의 시도 한도·실패
차단기, DB 연결·AI 동시 호출 상한, 사용자별 AI 묶음 요청 한도, LLM 모드 학습 계획의 시도 창)는 모두 **BE 프로세스마다** 메모리에서 셉니다. 이 구성은
backend 컨테이너 하나에 uvicorn 프로세스 하나라 그 값이 곧 서비스 전체 한도이고, backend 컨테이너를 다시 시작하거나 다시 만들면(배포 포함) 지워집니다.
DB로 세는 한도(모의 발급의 사용자별·전체 24시간 한도, 포트폴리오·공유 링크 개수, 사용자별 24시간 직무 비교·학습 계획·저장된 LLM 학습 계획 수)는
남습니다. BE 한도는 IP로 나누지 않으므로 IP별 제한은 nginx가 맡습니다(아래 "HTTPS·네트워크").

## 이미지·컨테이너

- [ ] 이미지는 서버에서 `deploy/scripts/deploy.sh`(`docker compose build --pull`)가 같은 체크아웃으로 함께 빌드합니다: BE(`vc-career/backend:local`,
  migrate도 같은 이미지), AI(`vc-career/ai:local`), nginx(`vc-career/web:local`, FE 빌드 포함). BE·AI의 공유 계약 ID가 다르면 직무 비교 API가 502이므로
  한쪽만 다른 커밋으로 빌드하지 않습니다. 태그가 `:local` 하나라 이전 이미지는 남지 않습니다(`deploy.sh`가 지움). 되돌릴 때는 이전 커밋을 받아
  (`git -C /opt/vc-career checkout <커밋>`) `deploy.sh`를 다시 실행합니다(마이그레이션은 되돌리지 않으므로 DB는 새 스키마 그대로).
- [ ] BE·AI 이미지는 `python:3.11.15-slim-bookworm` 기반, 비루트(uid/gid 10001), `--reload` 없는 uvicorn 한 프로세스이고, 의존성은
  `requirements.txt`의 고정 버전입니다. `docker-compose.yml`은 backend·ai의 명령·사용자를 덮어쓰지 않습니다(migrate만 명령 `python -m app.migrate`).
  `UVICORN_*` 환경변수는 uvicorn 실행 옵션으로 읽히므로 `backend.env`·`ai.env`에 넣지 않습니다(특히 `UVICORN_RELOAD`는 운영 이미지에서 리로더를 켬).
- [ ] BE 이미지의 Python 버전(3.11, 유니코드 14)과 DB 이미지(`pgvector/pgvector:0.8.7-pg16-bookworm`, glibc 2.36)는 함께 맞춰 올립니다: 한쪽만 올리면
  로그인 실패 제한이 이메일을 접는 방식이 DB `lower()`와 달라질 수 있고, 그때는 [BE/README.md](../BE/README.md) "로그인 실패 제한"의 예외 동작을
  따릅니다. PostgreSQL 주 버전(16)을 바꾸려면 덤프·복원이 필요합니다(같은 볼륨을 새 주 버전으로 띄우지 못함).
- [ ] 하드닝(`docker-compose.yml`): migrate·backend·ai는 읽기 전용 루트 파일 시스템(`read_only`) + `/tmp` tmpfs, `cap_drop: ALL`, `no-new-privileges`이고,
  nginx는 비루트(uid 101) 이미지에 `cap_drop: ALL`·`no-new-privileges`입니다. 쓰기 가능한 마운트가 필요한 것은 `python -m app.jobs.market export`뿐입니다.
  메모리 상한은 postgres 768 MB, backend 1.5 GB, ai 1 GB, nginx 128 MB이고, 이미지 빌드 때를 대비한 스왑 2 GB는 `setup-server.sh`가 만듭니다.

## BE·AI 환경변수·비밀값

값은 서버의 세 파일에만 둡니다(권한 600, 커밋하지 않음, `scripts/init-secrets.sh`가 만들고 비밀값을 무작위로 채움): `deploy/.env`(compose가 읽는
도메인·DB 비밀번호·`AI_SERVICE_TOKEN`), `deploy/backend.env`(BE만), `deploy/ai.env`(AI만). `APP_ENV`·`DB_*`·`PGSSL*`·`AI_SERVICE_URL`·
`AI_SERVICE_ALLOW_HTTP`·`AI_SERVICE_TOKEN`·`FORWARDED_ALLOW_IPS`는 `docker-compose.yml`이 정하므로 `backend.env`에 넣어도 무시됩니다. 값을 바꾸면
`docker compose up -d backend`(또는 `ai`)로 컨테이너를 다시 만듭니다(`restart`로는 새 값이 들어가지 않음). 변수별 위치·기본값·틀렸을 때는
[ENV_MATRIX.md](ENV_MATRIX.md)에 있습니다.

- [ ] `APP_ENV=production`(compose가 고정): 아래 비밀값이 없거나 이 저장소에 공개된 테스트·예시 값(`BE/tests`·`BE/.env.example`의 값, `x`·`0x01` 반복
  같은 자리표시 값)이면, 또는 `DB_HOST`·`DB_NAME`·`DB_USER`가 비었거나 `DB_PASSWORD`가 비었거나 로컬 기본값 `password`면, 또는 `JWT_SECRET`·
  `HOLDER_KEY_ENCRYPTION_KEY`·`SERVICE_API_KEY`·`AI_SERVICE_TOKEN`·`DXWORKS_API_KEY`·`GOOGLE_CLIENT_SECRET`·`GITHUB_CLIENT_SECRET`·`NAVER_CLIENT_SECRET`
  중 넣은 두 값이 서로 같으면, 또는 `PGSSLMODE`가 `verify-full`·`verify-ca`가 아니거나 `PGSSLROOTCERT`가 읽을 수 있는 파일이 아니면(아래 "DB TLS"),
  또는 아래 소셜 로그인·`CORS_ORIGINS` 규칙에 맞지 않으면 BE가 시작하지 않습니다. 이때 BE는 traceback 없이 stderr에 `APP_ENV=production requires …`
  한 줄(변수 이름만, 값 없음)을 남기고 종료 코드 1로 끝나며, `restart: unless-stopped`라 계속 다시 시작합니다(`docker compose ps`의 `Restarting`,
  `docker compose logs --tail=50 backend`). 줄 끝의 `set the same values on every task/worker (e.g. AWS Secrets Manager or SSM)`는 코드에 고정된 예전
  문구이고, 이 구성에서 고칠 곳은 `deploy/backend.env`(DB·AI 토큰은 `deploy/.env`)입니다. migrate도 같은 `DB_PASSWORD`·TLS 규칙으로 접속하지 않고
  `마이그레이션 실패:` 한 줄과 종료 코드 1로 끝납니다. 비밀값을 직접 만들 때는 [서비스 실행 방법](../docs/GETTING_STARTED.md) 2번 명령을 쓰고, 파일에는
  `KEY=값` 한 줄로 넣습니다(값 앞에 `KEY=`를 한 번 더 붙이지 않음).
- [ ] `JWT_SECRET`(`backend.env`): 32바이트 이상. 바꾸면 모든 사용자가 한 번 로그아웃됩니다.
- [ ] `JWT_EXPIRES_MINUTES`(선택): 토큰 수명(분). 1~1440 정수만 받습니다(기본 1440, 그 밖이면 BE가 시작하지 않음). 로그아웃하지 않고 떠난
  세션(공용 PC 등)이 살아 있는 최대 시간이라 짧을수록(예: 480) 안전하지만 다시 로그인하는 일이 잦아집니다. 로그아웃(`POST /api/v1/users/logout`)은
  이 값과 관계없이 그 계정의 토큰을 바로 모두 무효로 만듭니다.
- [ ] `HOLDER_KEY_ENCRYPTION_KEY`(`backend.env`): 32바이트 base64. 바꾸거나 잃어버리면 기존 홀더 키를 복호화할 수 없으므로 `deploy/.env`와 함께
  서버 밖(비밀번호 관리자 등)에도 백업합니다. 형식이 틀리면(base64가 아니거나 32바이트가 아님, `HOLDER_KEY_ENCRYPTION_KEY=` 접두사를 붙인 값 등) BE가
  `HOLDER_KEY_ENCRYPTION_KEY must be base64`·`HOLDER_KEY_ENCRYPTION_KEY must decode to 32 bytes` 한 줄(값 없음)과 종료 코드 1로 시작하지 않습니다.
- [ ] `AI_SERVICE_TOKEN`(`deploy/.env`, compose가 backend·ai에 같은 값을 넣음): 32바이트 이상. `JWT_SECRET`·`HOLDER_KEY_ENCRYPTION_KEY`·
  `SERVICE_API_KEY`·`DXWORKS_API_KEY`·`GOOGLE_CLIENT_SECRET`·`GITHUB_CLIENT_SECRET`·`NAVER_CLIENT_SECRET`과는 다른 값입니다(같으면 BE가 시작하지 않음).
- [ ] `DXWORKS_API_HOST`(`https://` 주소), `DXWORKS_API_KEY`(ASCII 문자만), `DXWORKS_BADGE_TEMPLATE_ID`(`backend.env`). 호스트가 `https://` 주소가
  아니거나(`http://`는 루프백만) 사용자 정보·쿼리·프래그먼트·공백이 들어 있으면, 또는 키에 ASCII가 아닌 문자가 있으면 BE가 시작하지 않습니다(오류에는
  변수 이름만 나옴). 비워 두면 BE는 뜨고 발급·검증·재검증·포트폴리오 생성·직무 비교가 503 `PROVIDER_NOT_CONFIGURED`입니다.
  `DXWORKS_TIMEOUT_SECONDS`(선택, 기본 15)는 1~120만 받습니다.
- [ ] DXWorks 호출 보호(설정값이 아니라 코드에 고정된 한도이고 BE 프로세스 메모리에서 셈. 운영자가 넣을 값은 없고, 아래 nginx IP별 제한과 로그만 봄):
  - 로그인 사용자가 검증기를 부르는 API(`POST /api/v1/verify`, 재검증, 근거 조회, 포트폴리오 생성, 직무 비교)는 부르기 전에 필요한 호출 수(서명 원본 수)만큼
    몫을 가져갑니다. 계정마다 분당 120번·시간당 1,200번(서비스 키는 한 몫을 함께 씀), BE 프로세스 전체로 분당 600번이고, 하나라도 모자라면 DXWorks를 한 번도
    부르지 않고 429 `Too many verification requests. Try again later.` + `Retry-After`(모자란 몫이 다시 찰 때까지의 시간 중 가장 긴 값)로 답합니다. 시간 몫을
    다 쓴 계정은 분당 20번만 부를 수 있어, 프로세스 몫(분당 600번)을 계속 비워 두려면 계정이 30개쯤 필요합니다(시간 한도가 없던 때는 6개. 지금도 처음 10여
    분은 6개로 가능). 시간당 1,200번은 몫이 다시 차는 속도라, 가득 찬 몫으로 시작하는 계정의 첫 한 시간은 1,200 + 1,200번까지입니다. 새로 가입한 계정도
    저마다 가득 찬 몫을 가져오고 BE는 가입 수를 제한하지 않으므로, nginx의 가입(`/api/v1/users/register`) IP별 제한도 이 보호의 일부입니다.
  - 배지 검증(`POST /api/v1/verify`)은 DXWorks까지 가서 그 토큰이 거절된 요청을 계정마다 10분에 10번까지만 받습니다(넘으면 DXWorks를 부르지 않고 같은
    429). 세는 것은 저장하지 않는 `success: false`(해석할 수 없는 원본, 수신자 없음, 발급자 미확인), 다른 DID에 발급된 배지(403), 다른 사용자가 등록한
    배지(409), 그 토큰 하나에 대한 검증기 오류(502), 그리고 DXWorks에 묻는 동안 끊긴 요청입니다. 통과한 검증, 폐기·만료 상태로 저장된 배지, 검증기 장애,
    DXWorks가 답한 뒤 BE 쪽 문제(DB가 바빠 503 등)로 끝난 요청은 세지 않습니다. 모양만 맞춘 쓰레기 토큰을 계정 하나가 분당 한도만큼 DXWorks로 보내지
    못하게 하려는 것입니다.
  - 로그인 없는 공유 링크 조회는 따로 BE 프로세스마다 분당 300번입니다(503 + `Retry-After`). 같은 서명 원본은 60초 동안 다시 묻지 않고, 같은 원본을 동시에
    여는 조회는 먼저 물은 응답을 함께 기다립니다(`DXWORKS_TIMEOUT_SECONDS`의 4배까지).
  - 동시에 DXWorks로 나가는 요청은 BE 프로세스마다 16개까지입니다. 응답 본문은 1 MiB까지이고, 압축은 요청하지 않지만 `gzip`·`deflate`로 온 응답은 전송된
    크기와 풀어낸 크기를 각각 1 MiB까지만 받으며 직접 풉니다(그 밖의 압축 방식은 거부). 시간은 교환(연결·전송·본문 수신, 연결 재시도 포함)이
    `DXWORKS_TIMEOUT_SECONDS`의 2배까지, 그 전에 동시 호출 자리를 기다리는 시간도 2배까지라 호출 한 번은 최악의 경우 4배(기본 15초면 60초. nginx는
    `/api/*` 응답을 125초까지 기다림, 아래 "시간 제한")입니다. 넘으면 그 호출을 실패로 처리합니다(배지 검증·모의 발급은 502, 재검증은 검증기 장애면 502).
  - `token`이 JWT(compact JWS) 모양이 아니면 DXWorks로 보내지 않습니다(배지는 200 `success: false`, 신원은 422). 조각 끝의 `=` 패딩과 SD-JWT 형식
    (`<jws>~…`)은 받습니다. 배지를 모양 때문에 거절하면 감사 로그 `event=verify_shape_refused`가 남습니다. 검증기 장애(연결 실패·시간 초과·5xx·408·429·
    401·403·3xx)가 한 번 확인되면 그 요청의 남은 자격증명은 부르지 않습니다.
  - 직무 비교는 재검증 전에 AI 설정과 대상 공고를 먼저 확인하므로, 503 `AI_SERVICE_NOT_CONFIGURED`·404로 끝날 요청은 DXWorks를 부르지 않습니다. AI 묶음
    요청 자리(`AI_MAX_CONCURRENCY`)는 재검증을 마친 뒤에 잡습니다(자리를 쥔 채 DXWorks를 기다리지 않으므로, DXWorks가 느리거나 멈춰도 공고 목록·학습자료
    검색·커리큘럼이 그 뒤에 서지 않음). 그래서 자리가 없어 503 `AI_SERVICE_BUSY`로 끝난 직무 비교는 그 요청의 DXWorks 호출을 이미 쓴 뒤이고, 그 양은 위
    계정별 몫에 묶입니다. 재검증 한 건(`POST /api/v1/credentials/{id}/reverify`)에서 DXWorks가 그 자격증명만 거절하면(400·404·422 등) 502가 아니라 200
    `success: false`입니다(502/503은 검증기 장애일 때만).
- [ ] 신원 VP 검증: `IDENTITY_VERIFICATION_ENABLED`는 넣지 않습니다(기본 `false`. 꺼져 있으면 `type: identity`는 DXWorks를 부르지 않고 400
  `Identity verification is disabled`). 화면이 쓰지 않는 기능이고, 켜면 사용자가 보낸 값에 회사 API 키가 붙어 DXWorks로 나갑니다.
- [ ] 모의 발급(FE `/issue` 화면, 가입 후 첫 배지): 시연 배포에서 쓰려면 `backend.env`에 `MOCK_ISSUER_ENABLED=true`(예시 파일 값)를 둡니다(없으면 발급
  화면이 404 `Mock issuer is disabled`). 켜면 BE 시작 로그에 경고가 한 줄 남고, 로그인한 본인 DID에만, `DXWORKS_BADGE_TEMPLATE_ID`(+ 선택
  `MOCK_ISSUER_TEMPLATE_IDS`, 쉼표 구분)의 템플릿만(그 밖은 403 `Template not allowed`), 사용자별 24시간 `MOCK_ISSUER_DAILY_LIMIT`건(기본 10)·모든
  사용자를 합쳐 24시간 `MOCK_ISSUER_GLOBAL_DAILY_LIMIT`건(기본 200)까지 회사 API 키로 발급합니다(넘으면 429). 가입은 누구나 할 수 있어 계정을 여러 개
  만들면 사용자별 한도가 곱해지므로, 전체 한도는 시연 인원 × 사용자별 한도보다 조금 크게만 잡습니다(기본 200은 20명 × 10건이라, 그보다 많은 인원이
  쓰면 올림). 429의 문구는 세 가지입니다: 그 사용자의 한도면 `Mock issuance limit reached; try again later`, 전체 한도(모든 사용자를 합친 24시간 발급
  수(DB), 또는 실패한 것 없이 한도만큼의 발급 요청이 한꺼번에 진행 중)면 `Mock issuance service limit reached; try again later.`(FE는 "전체 모의 발급
  한도(24시간)에 도달했습니다…"로 따로 안내), 협력사 실패가 이어져 발급을 잠시 멈춘 동안이면 `Mock issuance is paused after repeated failures. Try
  again shortly.` + `Retry-After`(FE는 "발급 서버 오류가 이어져 모의 발급을 잠시 멈췄습니다. 1분쯤 뒤에 다시 시도해주세요."). 전체 한도는 성공한 발급을
  24시간 동안 세고, 결과를 모르는 실패(응답 대기 중 시간 초과 등)는 만든 뒤 10분 동안만 셉니다(그 사용자의 한도에는 24시간): 시간 초과로 이어진 장애
  뒤에도 협력사가 돌아오면 길어야 10분에 1분쯤 더 지나 다시 발급됩니다. 이와 별도로 BE 프로세스 메모리에서 세는 것이 둘 있습니다. 하나는 사용자별 시도
  한도로, 협력사 호출을 시도한 요청은 실패해도(연결 실패 포함) 사용자별 24시간 `max(30, 3 × MOCK_ISSUER_DAILY_LIMIT)`번까지입니다(사용자 한도 문구).
  다른 하나는 실패 차단기로, 협력사 호출을 시도했는데 성공하지 못한 발급이 모든 사용자를 합쳐 24시간에 `MOCK_ISSUER_GLOBAL_DAILY_LIMIT`번 쌓이면 발급을
  멈추고 1분에 한 건만 협력사로 보내 봅니다(그 밖의 요청은 협력사를 부르지 않고 세 번째 문구의 429). 그 한 건이 성공하면 실패 기록을 모두 지워 바로 다시
  엽니다(재시작하거나 24시간을 기다리지 않음). 협력사가 계속 실패하는 동안 BE가 회사 API 키로 보내는 실패한 발급 요청은 그 한도만큼, 그 뒤로는 분당
  한 번입니다(탐침이 성공할 때마다 실패 기록이 지워지는 경우는 아래 "하지 않은 것"). 이 템플릿에는 시연용 배지만 두고, 실제 자격·이수를 뜻하는 템플릿은
  넣지 않습니다.
- [ ] 소셜 로그인(선택. 제공자마다 따로 켭니다. 쓰지 않는 제공자는 그 `*_CLIENT_ID`·`*_CLIENT_SECRET`을 비움 → 그 버튼만 503으로 동작하지 않음):
  - 공통: 도메인과 HTTPS가 준비된 뒤([README.md](README.md) ⑦·⑧) 쓰는 제공자마다 ID·시크릿·콜백 주소를 `backend.env`에 함께 넣습니다. 콜백은 FE의
    `https://` 주소입니다: `GOOGLE_REDIRECT_URI=https://<도메인>/auth/google/callback`, `GITHUB_REDIRECT_URI=https://<도메인>/auth/github/callback`,
    `NAVER_REDIRECT_URI=https://<도메인>/auth/naver/callback`. 콜백 주소는 FE가 BE에 보내는 주소(브라우저의 FE 출처 + `/auth/<제공자>/callback`)와 글자 그대로
    같아야 하고(다르면 그 버튼이 400 `Invalid redirect_uri`), 같은 주소를 제공자 콘솔에도 등록합니다(운영 앱·클라이언트에는 localhost 주소를 넣지 않음).
    BE API 주소가 아니라 FE 주소입니다. Google만 `GOOGLE_ALLOWED_REDIRECT_URIS`(선택, 쉼표 구분)로 FE 도메인을 여러 개 받고, GitHub·네이버는 주소 하나만
    받습니다. Google은 IP 주소나 `http://` 콜백을 받지 않으므로 공인 IP로 띄운 HTTP 모드에서는 소셜 로그인을 시험할 수 없습니다(다른 이름에서도 FE가 보내는
    콜백 주소가 달라 400 `Invalid redirect_uri`).
  - 운영에서는 ID·시크릿 중 하나만 넣었거나, 둘을 넣었는데 콜백 주소가 비었거나, 허용하는 콜백 주소 중 `https://`가 아닌 것이 있으면 BE가 시작하지
    않습니다(운영은 localhost 기본값을 대신 쓰지 않음). `*_CLIENT_SECRET`은 `JWT_SECRET`·`HOLDER_KEY_ENCRYPTION_KEY`·`SERVICE_API_KEY`·
    `AI_SERVICE_TOKEN`·`DXWORKS_API_KEY`와도, 서로도 다른 값이어야 합니다(같으면 BE가 시작하지 않음). 운영용 OAuth 클라이언트·앱은 로컬 개발용과 따로
    만듭니다(GitHub은 같은 OAuth App에 운영 콜백 주소를 더해도 됨, 아래).
  - Google: OAuth 동의 화면이 "테스트" 상태면 등록한 테스트 사용자(최대 100명)만 로그인할 수 있으므로, 시연 계정을 테스트 사용자로 등록하거나 공개하려면
    게시 상태로 바꿉니다. 승인된 리디렉션 URI에 위 콜백 주소를 넣습니다.
  - GitHub: OAuth App(GitHub App 아님)의 Authorization callback URL에 운영 FE 콜백 주소(`https://<도메인>/auth/github/callback`)를 정확히 등록합니다
    (와일드카드 일치는 끔. 앱 하나에 콜백 주소 10개까지라 개발용 앱에 더해도 되고, 운영용 앱을 따로 만들어도 됨). Homepage URL은 `https://<도메인>`으로
    둡니다. "Expire user access tokens"는 켜 두고(새 앱의 기본값, 액세스 토큰 8시간 만료. BE는 refresh token을 쓰지 않음) "Enable Device Flow"는 끕니다.
    조직(organization) 소유 앱은 그 조직 구성원만 로그인될 수 있으므로 외부 사용자가 쓸 앱은 조직 소유로 두지 않습니다. BE는 `scope=user:email`만 요청하고
    GitHub 계정의 인증된 기본(primary) 이메일만 받으며, 토큰 요청마다 `GITHUB_CLIENT_SECRET`을 보냅니다. 시크릿을 GitHub Actions의 secret·variable로
    보관한다면 그 이름은 `GITHUB_`로 시작할 수 없으므로 다른 이름으로 저장하고, 서버의 `backend.env`에는 그대로 `GITHUB_CLIENT_ID`·`GITHUB_CLIENT_SECRET`·
    `GITHUB_REDIRECT_URI`로 넣습니다.
  - 네이버: 검수를 받기 전에는 애플리케이션 관리자와 멤버관리에 등록한 아이디만 로그인할 수 있으므로, 서비스를 열기 전에 검수를 요청합니다(제공 정보로
    고른 이메일을 실제로 쓰는지 봄). 서비스 URL(`https://<도메인>`)·네이버 로그인 Callback URL(위 콜백 주소)을 등록합니다. 시연은 검수 없이 멤버관리에
    등록한 아이디로 할 수 있습니다. 네이버는 이메일 인증 여부를 알려 주지 않으므로 아래 계정 규칙대로 기존 계정에 연동하지 않습니다.
  - 계정 규칙: 같은 이메일의 기존 계정에 비밀번호가 있으면 소셜 로그인을 그 계정에 붙이지 않고 409 `This email is registered with a password. Log in with
    your email and password.`로 답합니다. 비밀번호 없는 계정에는 이번 로그인의 이메일이 제공자가 인증한 것(Google·GitHub)이고 그 계정에 같은 이메일의
    Google 또는 GitHub 연동이 있을 때만 붙이고, 아니면(네이버 로그인, 네이버로 만든 계정) 409 `This email is already used by another sign-in method. Log in
    with that method.`입니다. 이미 연동된 제공자 계정은 그대로 로그인됩니다.
  - 콜백의 토큰 교환은 BE 프로세스마다 제공자별로 분당 300번까지(그 제공자의 모든 사용자가 함께 씀)이고, 넘으면 제공자를 부르지 않고 429 + `Retry-After`이며
    감사 로그 `event=oauth_exchange_denied … provider=<제공자>`가 남습니다. 제공자 응답 실패는 502 `Google OAuth error`·`GitHub OAuth error`·
    `Naver OAuth error`이고 원인은 BE 로그 `Provider error` 줄에만 남습니다.
- [ ] DB 접속: compose가 `DB_HOST=postgres`·`DB_PORT=5432`·`DB_NAME`(`deploy/.env`, 기본 `vc_career_agent`)과 BE 서비스 계정 `DB_USER=vc_app`·
  `DB_PASSWORD`(`deploy/.env`의 `DB_APP_PASSWORD`)를 넣습니다(DB 소유 계정 `vc_owner`는 migrate만 씀, 아래 "DB 마이그레이션"). 필요하면 `backend.env`에
  `DB_CONNECT_TIMEOUT`(초, 기본 10).
- [ ] DB 연결 수: BE 프로세스 하나는 DB 연결을 동시에 `DB_MAX_CONNECTIONS`개(기본 20, 2 이상)까지만 엽니다(연결 풀이 아니라 요청마다 새 연결을 열고 닫으며
  동시에 열린 수만 묶음). postgres 컨테이너는 `max_connections`를 따로 정하지 않아 PostgreSQL 기본값(보통 100. 확인:
  `docker compose exec postgres psql -U postgres -c 'SHOW max_connections;'`)이므로 BE 20 + migrate 1 + 운영자·백업 접속은 그 안에 듭니다.
  `DB_MAX_CONNECTIONS`를 크게 올리면 이 합이 `max_connections`보다 작은지 봅니다. 넘으면 BE의 상한에 닿기 전에 DB가 접속을 거절하고
  (`too many connections`), 그 요청은 503 `Service is busy. Try again later.`로 실패합니다(BE 로그 `Database busy: … reason=…too many connections…`.
  요약의 `RDS max_connections`는 코드에 고정된 예전 이름이고, 이 구성에서는 postgres 컨테이너의 `max_connections`).
  - 앱 세션에는 문장 15초(`statement_timeout`), 잠금 대기 5초(`lock_timeout`), 트랜잭션을 연 채 쉬는 세션 30초(`idle_in_transaction_session_timeout`)
    제한이 접속할 때마다 걸립니다(DB·역할에 건 값보다 우선). 연결 자리를 5초 안에 얻지 못했거나 문장·잠금 제한에 걸렸거나 DB에 접속하지 못한 요청은
    503 `Service is busy. Try again later.` + `Retry-After: 5`이고, BE 로그에 `Database busy: …` 경고가 한 줄 남습니다(추적 없음).
  - migrate(`python -m app.migrate`)는 이 상한과 시간 제한을 받지 않습니다.
- [ ] DB TLS: compose가 backend·migrate(그리고 `import-market.sh`가 띄우는 일회성 backend)에 `PGSSLMODE=verify-full`과
  `PGSSLROOTCERT=/run/vc-db-tls/ca.crt`를 넣습니다. 이 CA는 `init-secrets.sh`가 서버마다 새로 만든 것입니다(`deploy/secrets/db-tls/`: CA와 이름
  `postgres`의 서버 인증서, 10년). postgres는 `postgres/start.sh`로 TLS(최소 1.2)를 켜고, `postgres/pg_hba.conf`로 TCP 접속은 TLS + SCRAM 비밀번호만
  받습니다. 그래서 `DB_HOST`는 인증서 이름과 같은 `postgres`여야 합니다(`verify-full`의 호스트 이름 확인. `require`·`prefer`는 인증서를 확인하지 않아
  가짜 서버를 막지 못함). `APP_ENV=production`에서 `PGSSLMODE`가 `verify-full`·`verify-ca`가 아니거나(빠짐·빈 값·대문자 포함) `PGSSLROOTCERT`가 읽을 수
  있는 파일이 아니면(`ca.crt` 없이 `docker compose up`을 실행해 Docker가 그 자리에 빈 디렉터리를 만든 경우 포함. `deploy.sh`는 먼저 파일을 확인함) BE와
  migrate는 DB에 접속하기 전에 `PGSSLMODE (verify-full or verify-ca in production)`·`PGSSLROOTCERT (readable CA bundle file)`을 알리고 시작을 거부합니다.
  BE 이미지에 든 RDS CA 묶음(`/app/certs/rds-global-bundle.pem`, 이미지의 `PGSSLROOTCERT` 기본값)은 이 구성에서 쓰지 않습니다
  ([BE/certs/README.md](../BE/certs/README.md)). 시작 점검 요약의 `RDS` 문구는 코드에 고정된 예전 이름입니다: `check PGSSLROOTCERT: RDS CA bundle`은
  `secrets/db-tls/ca.crt`가 서버 인증서를 서명한 CA가 아님, `set DB_HOST to the RDS endpoint name …`은 `DB_HOST`가 `postgres`가 아님,
  `check PGSSLMODE: RDS requires SSL`은 TLS 없이 접속함을 뜻합니다. `secrets/db-tls/`의 파일을 일부만 다시 만들지 않습니다(CA와 서버 인증서의 짝이 깨짐).
  로컬 Compose(`APP_ENV=development`, TLS 없는 Postgres)는 `PGSSLMODE`를 넣지 않아 libpq 기본값 `prefer`로 그대로 접속합니다(`PGSSLMODE=`처럼 비워 둔
  줄도 넣지 않은 것으로 봄).
- [ ] `CORS_ORIGINS`는 넣지 않습니다(FE와 `/api`가 같은 출처). 운영에서는 변수가 없거나 비어 있으면 다른 출처를 하나도 허용하지 않습니다(로컬 주소
  기본값은 `development`에서만 씀). FE를 다른 출처로 옮길 때만 FE 출처를 정확히 넣습니다(`https://app.example.com`, 끝에 `/` 없음, 여러 개면 쉼표 구분).
  `*`·`null`이나 경로·끝의 `/`·쿼리가 붙은 항목처럼 `scheme://host[:port]` 꼴이 아닌 값이 있으면 BE가 시작하지 않습니다.
- [ ] BE→AI: compose가 `AI_SERVICE_URL=http://ai:8100`·`AI_SERVICE_ALLOW_HTTP=true`를 넣습니다. BE→AI 요청에는 서비스 토큰과 학생의 검증된
  이력(DID·자격명)이 실리지만, 이 구간은 같은 서버 안 Docker 네트워크(`backend`, backend·ai만 붙음)라 서버 밖으로 나가지 않습니다. ai는 포트를 열지
  않습니다. AI를 다른 서버로 옮기면 평문 `http://`를 쓰지 않고 HTTPS 주소를 넣고 `AI_SERVICE_ALLOW_HTTP`를 뺍니다.
- [ ] `SERVICE_API_KEY`는 비워 둡니다(쓰는 서비스가 없고, 넣으면 모든 DID를 읽는 키가 됩니다. 넣는다면 `JWT_SECRET`·`HOLDER_KEY_ENCRYPTION_KEY`·
  `AI_SERVICE_TOKEN`·`DXWORKS_API_KEY`·`GOOGLE_CLIENT_SECRET`·`GITHUB_CLIENT_SECRET`·`NAVER_CLIENT_SECRET`과 다른 32바이트 이상의 새 임의 값이어야 BE가
  시작합니다). `RESEARCHER_USER_IDS`는 연구자 계정 UUID만([README.md](README.md) ⑥). `CURRICULUM_LLM_USER_IDS`는 예시 파일이 `*`(로그인한 모든 계정)이고,
  좁히려면 계정 UUID만 넣습니다.
- [ ] `AI_MAX_CONCURRENCY`(선택, 기본 4, 1 이상): BE 프로세스가 AI 서비스로 동시에 보내는 공고·학습자료 묶음 요청(공고 목록, 직무 비교, 학습자료 검색,
  커리큘럼 계획) 수의 상한입니다. 한 사용자는 그 가운데 동시에 2자리까지만 쥐고 묶음 요청은 분당 30번·시간당 300번까지이며, 넘으면 기다리지 않고 429
  `Too many analysis requests. Try again later.` + `Retry-After`입니다(코드에 고정된 값). 요청마다 검토된 공고·학습자료 전체를 실어 보내므로 backend
  (1.5 GB)·ai(1 GB) 컨테이너 메모리 상한에 맞춰 둡니다. 자리가 10초 안에 나지 않으면 AI를 부르지 않고 503 `code: AI_SERVICE_BUSY` + `Retry-After: 5`입니다.
  자리는 묶음을 DB에서 읽기 전에 잡으므로, 자리를 기다리는 요청은 묶음을 메모리에 올리지 않습니다. 직무 비교는 DXWorks 재검증을 마친 뒤에 자리를 잡습니다
  (자리를 쥔 채 DXWorks를 기다리지 않음). 그래서 503 `AI_SERVICE_BUSY`로 끝난 직무 비교도 그 요청의 DXWorks 호출은 이미 쓴 상태입니다(계정별 검증기 호출
  몫에 묶임).
- [ ] `LOG_LEVEL`은 넣지 않거나 `INFO`로 둡니다(빈 값도 `INFO`. `DEBUG`·`INFO`·`WARNING`·`ERROR`·`CRITICAL`만 받고, 그 밖의 값이면 BE·migrate가
  `LOG_LEVEL must be one of: …` 한 줄과 종료 코드 1로 끝남). `DEBUG`면 외부 호출마다 전체 URL(비공개 DXWorks 호스트 포함)을 남기는 httpx 로그와 접속
  호스트·응답 헤더를 남기는 httpcore 로그가 다시 나옵니다. `WARNING` 이상으로 올리면 아래 "상태 확인·로그"의 감사 로그(`vc-be.audit`, INFO)가 남지
  않습니다.
- [ ] AI 서비스는 `AI_SERVICE_TOKEN`(`deploy/.env`)만 필수입니다. LLM 모드(기본 공급자 Google Gemini API)는 `deploy/ai.env`의
  `CURRICULUM_LLM_ENABLED=true`·`GEMINI_API_KEY`이고(그 밖의 `CURRICULUM_LLM_*`는 [ai/README.md](../ai/README.md) 6번), 바꾼 뒤
  `docker compose up -d ai`입니다. 실제 사용자에게 열 때는 유료 등급 키를 씁니다(무료 등급은 Google이 입력·출력을 제품 개선에 쓸 수 있음). BE의
  `CURRICULUM_LLM_DAILY_LIMIT`(선택, 기본 20, 1 이상)은 허용된 계정 하나가 24시간에 만드는 LLM 모드 학습 계획 수입니다(저장된 LLM 계획은 DB로 세고, 외부
  LLM까지 간 실패한 시도는 BE 프로세스 메모리로 셈. 넘으면 429 + `Retry-After`). `CURRICULUM_LLM_GLOBAL_DAILY_LIMIT`(기본 200)은 모든 계정을 합친 값입니다.
  AI의 `CURRICULUM_LLM_MAX_CONCURRENCY`(기본 2)는 AI 프로세스의 동시 LLM 생성 수입니다.

## DB 마이그레이션

- [ ] 계정: 새 DB 볼륨을 처음 만들 때 `postgres/initdb/10-roles.sh`가 `vc_owner`(DB와 테이블을 소유하는 마이그레이션 계정. 슈퍼유저·`CREATEROLE`·
  `CREATEDB` 아님)와 `vc_app`(BE 서비스 계정, 로그인만)을 만들고 `DB_NAME` 데이터베이스를 `vc_owner` 소유로 만듭니다. PostgreSQL 16이라 `public` 스키마는
  `pg_database_owner`(곧 `vc_owner`) 소유이고 `PUBLIC`에는 `public` CREATE 권한이 없습니다. 비밀번호는 `deploy/.env`의 `DB_OWNER_PASSWORD`·
  `DB_APP_PASSWORD`이고, 볼륨을 만든 뒤에 이 파일만 바꾸면 접속이 실패합니다([README.md](README.md) "비밀번호 바꾸기"). `postgres` 슈퍼유저는 운영자가
  컨테이너 안에서만 씁니다(`docker compose exec postgres psql -U postgres -d vc_career_agent`).
- [ ] DB 확장은 필요 없습니다. 기본 스키마와 마이그레이션(04~13)은 확장을 만들지 않습니다(`gen_random_uuid()`는 PostgreSQL 13부터 기본 제공). 예전
  버전으로 만든 DB에 남아 있는 `pgcrypto`·`vector`는 그대로 둬도 됩니다(마이그레이션이 지우지 않고, 앱은 쓰지 않음). 로컬 Compose·CI·이 구성의 DB
  이미지는 같은 `pgvector/pgvector:0.8.7-pg16-bookworm`입니다.
- [ ] 새 PG16 볼륨은 `PUBLIC`에 `public` 스키마 CREATE가 없으므로 예전 RDS용 절차(`REVOKE CREATE ON SCHEMA public FROM PUBLIC;`와 점검 조회)는 필요
  없습니다. PostgreSQL 14 이하에서 만든 DB를 복원했다면 migrate의 `마이그레이션 실패:` 줄이 실행할 문을 알려 줍니다(아래).
- [ ] `vc_app`에는 따로 권한을 주지 않습니다. 슈퍼유저·`CREATEROLE`·`CREATEDB`·`REPLICATION`·`BYPASSRLS` 속성, DB나 `public` 스키마의 소유·CREATE
  권한, 그런 역할(과 `vc_owner`)의 소속(`NOINHERIT`·`GRANT ... WITH INHERIT FALSE` 소속 포함), `public` 테이블의 `GRANT ALL`·`TRIGGER`·`REFERENCES`(열
  단위 포함), 원장 `schema_migrations` 쓰기 권한, `vc_app`이나 그 소속 역할이 소유한 스키마, `vc_app`으로 `public`에 만든 객체가 있으면 migrate가
  거부합니다(그 권한으로 만든 트리거·외래 키·원장 행은 권한을 거둔 뒤에도 남음. `vc_app` 자신에게 준 그 밖의 DML이 아닌 권한(`TRUNCATE` 등)은 migrate가
  실행할 때마다 거둠).
- [ ] migrate 서비스(같은 BE 이미지, `python -m app.migrate`, **`--seed` 없음**: `APP_ENV=production`이면 거부)는 `deploy.sh`(`docker compose up -d`)마다
  backend보다 먼저 한 번 돌고 끝납니다. `vc_owner`로 접속하고 `DB_APP_ROLE=vc_app`, `PGSSLMODE=verify-full`입니다. 실행기는 끝날 때마다 `public`
  테이블·시퀀스에서 `vc_app`의 권한을 모두 거둔 뒤 앱 테이블 SELECT·INSERT·UPDATE·DELETE와 시퀀스 사용 권한만 다시 줍니다(새 테이블 포함, 원장
  `schema_migrations` 제외). 위 검사는 마이그레이션 SQL을 하나라도 실행하기 전에 하고 권한을 주기 직전에 다시 하므로, 처음부터 걸리면 아무것도 적용하거나
  주지 않습니다.
- [ ] migrate가 종료 코드 1이면 backend가 새로 뜨지 않고 `deploy.sh`가 멈춥니다(`service "migrate" didn't complete successfully`). `docker compose logs
  migrate`의 `마이그레이션 실패:` 줄을 봅니다. DB 접속 실패는 `DB에 접속하지 못했습니다: <종류>: <요약>`으로 나오고, 인증서·호스트 이름·비밀번호·없는
  역할·DB 같은 오류는 60초를 기다리지 않습니다(요약별 원인은 [ENV_MATRIX.md](ENV_MATRIX.md)). 권한 검사에 걸리면 그 줄이 걸린 검사·소속 역할 이름과
  나온 순서대로 실행할 GRANT·ALTER ... OWNER·REVOKE 문(예: `ALTER DATABASE "<DB 이름>" OWNER TO "vc_owner";`, `REVOKE CREATE ON DATABASE "<DB 이름>" FROM
  PUBLIC;`, `REVOKE TRIGGER ON ALL TABLES IN SCHEMA public FROM "vc_app";`, `REVOKE REFERENCES ON ALL TABLES IN SCHEMA public FROM "vc_app";`,
  `REVOKE ALL ON public.schema_migrations FROM "vc_app";`, `DROP SCHEMA "<스키마>" CASCADE; 또는 ALTER SCHEMA "<스키마>" OWNER TO "vc_owner";`)이나 소유한
  객체·스키마·트리거·외래 키 목록을 알려 줍니다. 그 문은 `postgres` 슈퍼유저로(`docker compose exec postgres psql -U postgres -d vc_career_agent`) 나온
  순서대로 실행합니다. 슈퍼유저로 실행할 때는 맨 앞의 "마이그레이션 계정이 슈퍼유저가 아니면 … 먼저 지금 소유자의 권한을 받습니다: `GRANT … TO
  "vc_owner" …`" 부분이 필요 없습니다. 원장 쓰기 권한으로 걸렸으면 그 권한으로 넣거나 바꾼 원장 행이 남으므로 원장이 실제로 적용한 마이그레이션과 맞는지
  확인합니다. 그다음 `deploy.sh`를 다시 실행합니다.
- [ ] 이번 BE는 04~13이 적용된 DB를 씁니다: 09(`users.token_version`)가 없으면 로그인·가입과 토큰이 필요한 모든 요청이, 10(`mock_issuances`)이 없으면
  모의 발급이, 11(`oauth_accounts`)이 없으면 소셜 로그인과 내 정보 조회가, 12(`market_postings`·`market_snapshots`)가 없으면 시장 통계 조회가 500입니다.
  13은 `users`에 대소문자를 구분하지 않는 이메일 유일 인덱스(`users_email_lower_key`)를 만들고, 중복 인덱스 `idx_users_did`와 쓰지 않는 표
  `student_credential_embeddings`·`user_skills`를 지웁니다(그 표에 직접 넣어 둔 행이 있으면 함께 없어짐. 확장은 지우지 않음). 대소문자만 다른 이메일의
  계정이 있으면 13은 적용되지 않고 종료 코드 1이며, 로그의 `HINT:` 줄에 있는 조회(`SELECT lower(email), count(*) FROM users GROUP BY 1 HAVING count(*) > 1;`)로
  찾아 이메일마다 계정을 하나만 남긴 뒤 다시 실행합니다(로그에는 겹치는 수만 나오고 이메일 주소는 나오지 않음). 여러 개가 동시에 실행돼도 advisory lock으로
  하나씩 적용됩니다. 13은 `users` 표에 배타 잠금이 잠깐 필요하므로 그 DB에 오래 열린 트랜잭션이 없을 때(백업 cron의 `pg_dump`가 도는 04:00 무렵은
  피함) 배포합니다(아래 "하지 않은 것"의 마이그레이션 항목).
- [ ] 개발·공용 DB를 덤프해 운영 postgres로 옮기지 않습니다. 공개된 시드 테스트 비밀번호(04 마이그레이션의 해시)를 쓰는 계정이 있으면 migrate가 1로
  끝납니다(이미 적용한 마이그레이션은 남음). 그 계정은 가상 데이터이므로 확인한 뒤 지우고(`docker compose exec postgres psql -U postgres -d vc_career_agent`로
  들어가 `DELETE FROM users WHERE password_hash = '<BE/DB/migrations/04_users_password_hash.sql의 해시>';`, 그 계정의 자격증명·공유 링크 등은 함께 지워짐)
  `deploy.sh`를 다시 실행합니다.
- [ ] 연구자가 학습자료를 반입한 기존 DB라면 이번 AI를 배포하기 전에 [BE/README.md](../BE/README.md)의 "학습자료 선수 조건 점검"을 한 번 실행합니다.
  공고·학습자료를 반입한 기존 DB는 같은 곳의 "결합 문자 점검"도 한 번 실행합니다(결합 문자가 30개 넘게 이어진 구간이 저장돼 있는지 확인. 실제 문장에는
  없는 형태).
- [ ] (선택) 직무별 채용 시장 통계: 넣지 않으면 직무 비교 화면의 "직무별 요구 역량 통계" 카드만 보이지 않습니다. 서비스가 뜬 뒤
  `deploy/scripts/import-market.sh`를 한 번 실행합니다. 저장소의 `ai/data/market_snapshots/*.json`(집계 결과, 공고 원문 없음)을 읽기 전용으로 붙인 일회성
  backend 컨테이너(`docker compose run --rm --no-deps`, `vc_app`·같은 TLS)에서 `python -m app.jobs.market import --dir /app/market_snapshots`를 돌립니다
  (BE 이미지에는 스냅샷이 들어 있지 않음). 종료 코드 0이면 성공(로그 `가져온 통계: {…}`의 `False`는 같은 내용이 이미 있음), 1이면 `실패: …` 한 줄의
  이유를 봅니다([서비스 실행 방법](../docs/GETTING_STARTED.md) 7-1번). 공고 수집(`collect`)과 통계 계산(`snapshot`)은 이 절차에 들어 있지 않습니다:
  `collect`는 외부 채용 사이트를 읽고 `snapshot`은 AI 서비스와(운영이면) BE와 같은 운영 비밀값이 있어야 돌므로, 운영에서 돌리기 전에
  [BE/README.md](../BE/README.md) "직무별 채용 시장 통계"의 수집 원칙을 먼저 확인합니다.

## FE

- [ ] FE는 nginx 이미지([nginx/Dockerfile](nginx/Dockerfile))가 빌드합니다: `node:22-bookworm-slim`에서 `npm ci && npm run build`(Node.js 22.12 이상
  필요)를 하고, 결과물(`dist/`)을 비루트 nginx(`nginxinc/nginx-unprivileged`)가 서비스합니다. `VITE_API_BASE_URL`은 빌드 때 번들에 들어가는 값인데
  Dockerfile이 빈 값으로 고정해 같은 출처의 `/api`를 씁니다(CORS 불필요). FE는 도메인을 몰라도 되므로(API는 상대 경로, OAuth 콜백은
  `window.location.origin`) 도메인을 바꿔도 다시 빌드하지 않습니다.
- [ ] 캐시([nginx/vc/app.conf](nginx/vc/app.conf)): `index.html`과 SPA 경로는 `Cache-Control: no-cache`, 해시가 붙은 `assets/`는
  `public, max-age=31536000, immutable`, 그 밖에 마지막 경로 조각에 점이 있는 파일(`favicon.svg` 등)은 1시간입니다. 새 이미지에는 새 빌드의 `assets/`만
  있으므로, 배포 전에 열어 둔 탭이 옛 자산을 더 불러오면 404입니다(새로고침하면 됨). 정적 파일만 gzip으로 압축하고 API 응답은 압축하지 않습니다(토큰이
  담긴 응답에 BREACH류 공격 여지를 두지 않음).
- [ ] `/api/*`는 캐시하지 않고 `Authorization` 헤더를 포함한 요청을 그대로 backend로 넘깁니다. `proxy_pass` 주소 끝에 `/`를 붙이면 `/api`가 잘려 모든
  요청이 404가 됩니다. API 응답의 `Cache-Control: no-store`와 보안 헤더는 BE가 직접 붙입니다.
- [ ] SPA history fallback: `/dashboard`·`/auth/<제공자>/callback`·`/share/<token>`처럼 마지막 경로 조각에 점이 없는 경로는 `index.html`(200)로, 점이 있는데
  없는 파일은 404로 답합니다(`index.html`로 바꾸지 않음). `/api/*`는 이 처리를 타지 않으므로 BE의 403/404 JSON이 그대로 갑니다.
- [ ] 보안 응답 헤더([nginx/vc/security-headers.conf](nginx/vc/security-headers.conf), 정적 응답마다. nginx는 `location`에 `add_header`가 하나라도 있으면
  바깥 것을 물려받지 않으므로 정적 `location`을 더할 때 이 파일을 함께 include합니다):
  - `Strict-Transport-Security: max-age=31536000` (HTTPS 모드에서만. `includeSubDomains`·`preload`는 쓰지 않음)
  - `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` (`X-XSS-Protection`은 넣지 않음)
  - `Content-Security-Policy`(한 줄): `default-src 'self'; script-src 'self'; style-src 'self' https://cdn.jsdelivr.net; font-src 'self' data: https://cdn.jsdelivr.net; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`
  - Pretendard 웹폰트는 아직 jsDelivr CDN에서 받으므로(`FE/src/main.tsx`가 스크립트로 붙이는 스타일시트 `<link>`. `index.html`에는 없어 CDN이 느려도 첫
    화면을 막지 않음. 자체 호스팅은 [FE/README.md](../FE/README.md) TODO) `style-src`·`font-src`에서 `https://cdn.jsdelivr.net`을 빼면 글꼴이 시스템 글꼴로
    바뀝니다. FE가 다른 출처의 리소스를 새로 쓰면 이 파일도 함께 바꾸고 `deploy.sh`로 nginx 이미지를 다시 빌드합니다.

## HTTPS·네트워크

- [ ] 실제 사용자에게는 HTTPS로만 서비스합니다. `SERVER_NAME`이 비었거나 인증서가 없으면 nginx는 HTTP 모드(공인 IP, 80)로 뜨고 비밀번호·토큰이 평문으로
  오가므로 첫 확인용으로만 씁니다(소셜 로그인도 쓸 수 없음). DNS A 레코드 → `SERVER_NAME` → `sudo deploy/scripts/issue-cert.sh`([README.md](README.md) ⑦)
  뒤에는 `nginx/40-vc-site.sh`가 HTTPS 모드를 고릅니다:
  - 80은 Let's Encrypt 인증(`/.well-known/acme-challenge/`)만 받고 나머지는 `https://<도메인>`으로 301.
  - 443은 `server_name <도메인>`만 서비스하고, 다른 이름(IP 등)으로 오면 TLS 연결 자체를 거절합니다(`ssl_reject_handshake`).
  - TLS 1.2·1.3(Mozilla "intermediate", [nginx/vc/tls.conf](nginx/vc/tls.conf)), HTTP/2.
  - 인증서는 certbot systemd 타이머가 갱신하고, 갱신되면 `install-cert.sh`가 `deploy/secrets/tls/`로 복사한 뒤 nginx를 다시 시작합니다(몇 초 끊김).
  - API 응답의 HSTS는 BE가 `APP_ENV=production`에서 직접 붙입니다.
- [ ] 밖으로 열린 포트는 22·80·443뿐입니다. NCP ACG 인바운드는 22 ← 운영자 공인 IP만, 80·443 ← `0.0.0.0/0`(80은 인증서 발급·갱신에도 필요)이고, 서버의
  ufw도 같은 셋입니다(`setup-server.sh`). `docker-compose.yml`에서 포트를 여는 것은 nginx(80→8080, 443→8443)뿐이고 backend(3000)·ai(8100)·postgres(5432)는
  포트를 열지 않습니다. Docker가 연 포트는 ufw 규칙과 별개로 열리므로 compose에 `ports:`를 더하지 않습니다(DB는 `docker compose exec postgres psql`로만 봄).
  postgres가 붙은 `db` 네트워크는 `internal`이라 밖으로 나가는 길도 없습니다.
- [ ] IP별 요청 제한(예전 WAF 속도 규칙을 옮긴 것, [nginx/http.conf](nginx/http.conf)·[nginx/vc/app.conf](nginx/vc/app.conf)): nginx가 맨 앞이라 실제 접속 IP로
  셉니다. 키가 IP + 경로 이름이라 경로마다 따로 세고, `$uri`(%인코딩을 풀고 정규화한 경로)로 맞추므로 `/api/v1/users/%6Cogin`도 로그인으로 셉니다. 넘으면
  BE를 부르지 않고 429 `{"detail":"Too many attempts. Try again later."}` + `Retry-After: 60`입니다. FE는 429면 본문과 관계없이 로그인·가입 화면에서 BE 로그인
  제한의 429와 같은 안내("로그인 시도가 너무 많습니다."·"회원가입 시도가 너무 많습니다." + "보안을 위해 잠시 동안 … 제한되었습니다. 잠시 후 다시
  시도해주세요."), 발급 화면에서 "시도가 너무 많습니다. 잠시 후 다시 시도해주세요."를 보여 줍니다.
  - `vc_slow`(IP·경로마다 분당 20번 + 처음 몰아서 20번, 5분에 약 100번): `POST /api/v1/users/register`, `POST /api/v1/verify`(사용자는 배지를 받을 때마다 한
    번만 부름), `POST /api/v1/portfolios`, `POST /api/v1/career-analyses`(요청 하나가 고른·가진 자격증명 수만큼 DXWorks를 부름), `POST /api/v1/curricula`(LLM
    모드는 외부 Gemini API), `POST /api/mock-issuer/issue`, `GET /api/v1/subjects/<did>/evidence`(FE가 쓰지 않는 API, 요청 하나가 자격증명 수만큼 DXWorks를
    부름), `POST /api/v1/users/oauth/<google|github|naver>/callback`(제공자마다, 사용자는 로그인 한 번에 한두 번만 부름), `POST /api/v1/research/*`(연구자 반입).
  - `vc_fast`(분당 60번 + 처음 몰아서 60번, 5분에 약 300번): `POST /api/v1/users/login`, `GET /api/v1/job-postings`, `GET /api/v1/learning-resources`,
    `GET /api/v1/public/shares/*`, `POST /api/v1/credentials/<id>/reverify`(지갑의 일괄 재검증은 자격증명마다 요청을 하나씩 보냄),
    `GET /api/v1/users/oauth/<google|github|naver>/authorize`(제공자마다). 그 밖의 `/api/*`와 정적 파일은 세지 않습니다.
  - DXWorks·AI 묶음·소셜 로그인 경로를 IP로도 묶는 이유: BE는 계정마다 묶지만(검증기 분당 120번·시간당 1,200번, AI 묶음 동시 2개·분당 30번·시간당 300번)
    가입은 누구나 할 수 있어, 계정을 여러 개 만든 한 IP가 모든 사용자가 함께 쓰는 BE 프로세스 몫(검증기 분당 600번, AI 묶음 자리 `AI_MAX_CONCURRENCY`,
    제공자별 토큰 교환 분당 300번)을 다 쓸 수 있습니다. 토큰 교환 몫은 그 제공자의 모든 사용자가 함께 쓰므로, 누가 몰아 보내는 동안에는 그 제공자의 다른
    사용자 로그인도 429가 됩니다(BE 감사 로그 `event=oauth_exchange_denied … provider=<제공자>`. 다른 제공자는 막히지 않음). 그 전에 IP별로 막는 것은 이
    제한뿐입니다.
  - `/verify`·재검증·콜백 값을 낮게 둔 이유: 한 IP가 계속 보낼 수 있는 양이 `/verify` 분당 20회, 재검증 분당 60회, 콜백 분당 20회라, 한 IP가 혼자서는 BE
    프로세스 몫(검증기 분당 600번, 제공자별 토큰 교환 분당 300번)을 비울 수 없고, 계속 비워 두려면 `/verify`로는 IP 30개, 재검증으로는 10개, 콜백으로는
    15개쯤이 필요합니다(나눗셈으로 낸 값).
  - 값은 시작 값입니다(부하 시험으로 정한 값은 아님). 로그인·가입·공유 링크 값은 강의실 하나가 NAT IP 하나로 함께 써도 넘지 않게 잡았습니다. `/verify`·
    재검증·콜백은 같은 IP를 쓰는 단체 실습에서 닿을 수 있습니다: 5분에 `/verify` 약 100회는 40명이 배지를 2개씩 검증하는 정도(80회)까지이고, 재검증 약
    300회는 자격증명 10개인 지갑 30개의 일괄 재검증입니다. 더 큰 실습이면 `http.conf`의 `rate`나 `app.conf`의 `burst`를 올리고 `deploy.sh`로 다시 빌드합니다.
    막힌 요청은 nginx 오류 로그에 `limiting requests, excess: … by zone "vc_slow"`(또는 `vc_fast`)로 남습니다.
  - 카운터는 nginx 메모리에 있어 nginx를 다시 시작하면(배포, 인증서 갱신) 지워집니다. 예전 WAF의 관리형 규칙(IP 평판·알려진 공격 입력)에 해당하는 것은
    없습니다.
  - BE 자체 제한(같은 이메일 15분 10번 실패 → 429인 로그인 실패 제한과 위 "BE·AI 환경변수·비밀값"의 검증기 호출·거절 창, 공유 링크 조회, 토큰 교환,
    모의 발급, AI 묶음 요청, 24시간 한도)은
    모두 이메일·계정·프로세스 단위이고 IP별 제한은 없으므로 이 nginx 제한이 필요합니다. nginx가 묶는 것은 IP별 요청 수이고, DXWorks·소셜 로그인 제공자로
    나가는 호출 수 자체는 BE 한도가 묶습니다.
- [ ] 요청 본문 크기: nginx는 3 MiB(`client_max_body_size 3m`)까지 받고, BE가 경로별 상한(`BE/main.py`의 `body_limit`)을 넘는 본문을 413으로 끊습니다
  (`Content-Length`로 알리면 읽기 전에, chunked 전송이면 상한 남짓까지만 읽고). 상한은 기본 64 KiB, 토큰 없이 부르는 `POST /api/v1/users/login`·
  `POST /api/v1/users/register`·`POST /api/v1/users/oauth/{google,github,naver}/callback` 16 KiB, `POST /api/v1/verify` 320 KiB, 연구자용
  `POST /api/v1/research/*` 세 경로 2 MiB입니다. nginx 값을 줄인다면 `/api/v1/research/*`(공고 반입 최대 약 1.75 MiB)와 `/api/v1/verify`(VC-JWT 최대
  256 KiB)를 막지 않게 합니다. 연구자용 세 경로는 본문 형식 검사보다 먼저 연구자인지 확인하므로, 연구자가 아닌 계정이 보낸 큰 본문은 형식 검사 없이
  403입니다(본문을 읽고 JSON으로 푸는 것까지는 함).
- [ ] 나가는 연결: BE는 DXWorks·AI·소셜 로그인 제공자 호출에 `HTTP_PROXY`·`HTTPS_PROXY`·`ALL_PROXY` 같은 프록시 환경변수를 쓰지 않고 리디렉션도 따라가지
  않습니다. 서버는 DXWorks와 켠 소셜 로그인 제공자의 HTTPS(443) 호스트에 나갈 수 있어야 합니다: Google `oauth2.googleapis.com`·
  `openidconnect.googleapis.com`, GitHub `github.com`(토큰)·`api.github.com`(사용자 정보·이메일 목록), 네이버 `nid.naver.com`(토큰)·`openapi.naver.com`
  (사용자 정보). 인가 화면(`accounts.google.com`·`github.com`·`nid.naver.com`)은 사용자 브라우저가 엽니다. AI는 LLM 모드를 켰을 때만
  `generativelanguage.googleapis.com`(다른 공급자면 `CURRICULUM_LLM_BASE_URL` 호스트)으로 나갑니다. 빌드에는 Docker Hub·PyPI·npm, 인증서에는 Let's
  Encrypt가 필요합니다. NCP ACG 아웃바운드를 좁힌다면 [README.md](README.md) ①의 최소 규칙(TCP 80·443, UDP 53·123)을 둡니다. DXWorks가 IP 허용 목록을 쓰면
  서버에서 실제로 나가는 IP를 확인해 알립니다.
- [ ] 클라이언트 IP: nginx가 `X-Forwarded-For`를 이어 붙이지 않고 실제 접속 IP로 덮어쓰고(`X-Real-IP`·`X-Forwarded-Proto`도 넣음), compose가 backend에
  `FORWARDED_ALLOW_IPS="*"`를 넣어 uvicorn이 그 값을 믿습니다(backend 포트는 열지 않아 Docker 네트워크 안에서만 닿음). 그래서 BE 접근 로그의 IP는 사용자
  IP입니다. BE는 클라이언트 IP를 한도에 쓰지 않고(로그인 실패 제한도 이메일별), 요청의 스킴·`Host`로 주소를 만들지 않습니다(HSTS도 `APP_ENV`로만 정함).
- [ ] 시간 제한: LLM 커리큘럼은 BE가 AI를 최대 90초(`AI_GENERATION_TIMEOUT_SECONDS`) 기다리고 FE는 120초까지 기다립니다. DXWorks 호출 한 번은 최악의 경우
  `DXWORKS_TIMEOUT_SECONDS`의 4배(기본 60초)입니다.
  - nginx `/api/*`는 연결 5초, 보내기·읽기 125초(`proxy_send_timeout`·`proxy_read_timeout`)를 기다립니다. 이보다 오래 걸리면 nginx가 먼저 504를 돌려줘 FE는
    "서버에 연결할 수 없습니다"를 보여 주는데, BE는 요청을 끝까지 처리하므로(모의 발급·LLM 커리큘럼은 성공했을 수 있음) 사용자가 다시 시도하면 일과 협력사
    호출이 겹칩니다. `AI_GENERATION_TIMEOUT_SECONDS`나 `DXWORKS_TIMEOUT_SECONDS`(×4)를 125초 가까이 올리면 이 값도 함께 올립니다.
  - `proxy_next_upstream off`: 응답이 늦거나 실패해도 다시 보내지 않습니다(DXWorks를 부르는 `GET /api/v1/public/shares/{token}`·
    `GET /api/v1/subjects/{did}/evidence`가 되풀이되지 않음).
  - 배포·재시작 때 처리 중인 요청이 잘리지 않게 backend·ai는 `stop_grace_period: 120s`입니다(uvicorn은 종료 신호를 받으면 처리 중인 요청을 마치고 끝남).
    컨테이너가 하나라 무중단 배포는 아니고, backend를 다시 만드는 동안 `/api/*`는 502입니다. nginx는 요청마다 Docker DNS로 backend를 다시 찾으므로
    nginx를 다시 시작하지 않아도 됩니다.

## 상태 확인·로그

- [ ] `/health`(인증 없음, DB는 확인하지 않음): BE 3000번, AI 8100번. 두 이미지의 `HEALTHCHECK`(30초마다, 제한 5초, curl 없이 python)가 `docker compose ps`의
  `healthy`가 되고, compose는 그것으로 시작 순서를 잡습니다(ai healthy → backend, backend healthy → nginx). BE의 `/health`는 워커 스레드를 쓰지 않고 이벤트
  루프에서 바로 답하므로, DB 연결 자리나 잠금을 기다리는 요청이 워커 스레드를 모두 잡고 있어도(각 최대 5초) 상태 확인이 함께 밀리지 않습니다. 끝에 `/`를
  붙인 `/health/`는 404입니다. Docker는 `unhealthy`가 된 컨테이너를 다시 시작하지 않습니다(`restart: unless-stopped`는 프로세스가 끝났을 때만).
- [ ] 운영 시작 DB 점검: `/health`가 DB를 보지 않는 대신, BE는 `APP_ENV=production`에서 요청을 받기 전에 한 번 요청과 같은 계정·TLS로 `SELECT 1`과
  `SELECT 1 FROM users LIMIT 1`을 실행합니다. 실패하면 5초마다 다시 해 보고(시도마다 `DB_CONNECT_TIMEOUT`, 기본 10초까지), 약 30초 안에 통과하지 못하면
  `DB startup check failed: <오류 종류>: <요약>` 한 줄(호스트·사용자 이름·비밀번호 없음)과 `Application startup failed. Exiting.`을 남기고 종료 코드 3으로
  끝나며, compose가 다시 시작합니다(계속 실패하면 `Restarting`이 반복되고, 첫 배포면 nginx도 뜨지 않음). 요약별 원인은 [ENV_MATRIX.md](ENV_MATRIX.md)
  "docker-compose.yml에 고정된 값"과 [README.md](README.md) "문제 해결". DB를 보는 공개 경로(`/ready` 등)는 만들지 않았고, 떠 있는 BE의 DB가 나중에 끊기는
  것은 아래 로그로 봅니다.
- [ ] 로그: 모든 컨테이너가 stdout·stderr로 남기고(BE·AI는 `PYTHONUNBUFFERED=1`) Docker `json-file` 드라이버가 컨테이너마다 10 MB × 5개까지 보관합니다
  (`docker compose logs`). 컨테이너를 다시 만들면(배포, 설정 반영의 `up -d`) 그 전 로그는 함께 지워지므로 필요한 줄은 먼저 파일로 남깁니다
  (`docker compose logs --no-color backend > backend-$(date +%F).log`). BE는 `LOG_LEVEL`로 수준을 정합니다(AI는 읽지 않음). 예전 CloudWatch 지표 필터 대신
  grep으로 봅니다(`cd /opt/vc-career/deploy`에서):
  ```bash
  docker compose logs --since 24h backend | grep -E 'AI service error|Credential re-verification failed|Provider error|Public share view rejected|Database busy|Mock issuance outcome unknown|Market snapshot integrity check failed|OAuth token request rejected|Rejected unstorable text|Unhandled error'
  docker compose logs backend migrate | grep -E 'DB startup check failed|APP_ENV=production requires|마이그레이션 실패'
  docker compose logs --since 24h nginx | grep 'limiting requests'
  ```
  협력사 오류 코드·메시지와 빠진 설정 이름은 응답에 싣지 않고 `Provider error` 줄(한 줄, 300자까지)에만 남습니다.
- [ ] 감사 로그: 로거 `vc-be.audit`가 INFO로 한 줄씩 남깁니다(같은 stdout, 줄 머리 `INFO:vc-be.audit:`. 이메일·DID·토큰·이름은 싣지 않음). `LOG_LEVEL`을
  `WARNING` 이상으로 올리면 남지 않습니다.
  - `event=verifier_calls user=<사용자 UUID|service|anonymous> route=<verify_badge|verify_identity|reverify|evidence|portfolio_create|career_analysis|share_view> calls=<부른 수> failed=<그중 실패 수>`: DXWorks 검증기를 부른 요청마다.
  - `event=verifier_budget_denied user=<사용자 UUID|service> route=<…> retry_after=<초>`: 호출 한도로 429를 돌려준 요청마다. 토큰이 거절된 배지 검증이 10분에
    10번을 넘어 막힌 요청은 `route=verify_rejected`.
  - `event=verify_shape_refused user=<사용자 UUID>`: `POST /api/v1/verify`의 배지 토큰이 JWT 모양 검사에 걸려 DXWorks로 보내지 않은 요청마다(이때는
    `verifier_calls` 줄이 없음).
  - `event=login_locked key=<이메일 SHA-256의 앞 12자리> retry_after=<초>`: 로그인 실패 제한으로 잠긴 이메일에 온 시도마다.
  - `event=oauth_login outcome=<created|linked|login|refused_password_account|refused_unverified_link> user=<사용자 UUID> provider=<google|github|naver>`:
    소셜 로그인 결과마다(두 `refused_*`의 `user`는 그 로그인을 붙이지 않은 기존 계정: 비밀번호 계정, 또는 이메일이 양쪽에서 확인되지 않은 계정).
  - `event=oauth_exchange_denied retry_after=<초> provider=<google|github|naver>`: 그 제공자의 토큰 교환 한도(BE 프로세스마다 제공자별 분당 300번)로 429를
    돌려준 콜백마다.
  - `event=corpus_budget_denied user=<사용자 UUID> route=</v1/job-postings/search|/v1/learning-resources/search|/v1/career-analyses|/v1/curricula/context> reason=<in_flight|rate> retry_after=<초>`:
    사용자별 AI 묶음 요청 한도(동시 2개·분당 30번·시간당 300번)로 429를 돌려준 요청마다.
  - `event=daily_limit_denied user=<사용자 UUID> route=<career_analysis|curriculum|curriculum_llm> retry_after=<초>`: 사용자별 24시간 한도(직무 비교 200건,
    학습 계획 200건, LLM 학습 계획 `CURRICULUM_LLM_DAILY_LIMIT`건)로 429를 돌려준 요청마다.
  - `event=llm_service_limit_denied user=<사용자 UUID> retry_after=<초>`: 모든 계정을 합친 24시간 LLM 학습 계획 한도(`CURRICULUM_LLM_GLOBAL_DAILY_LIMIT`,
    기본 200)로 429를 돌려준 요청마다. `CURRICULUM_LLM_USER_IDS=*`로 연 뒤 자주 보이면 가입 남용이나 한도 조정을 확인합니다.
  - `event=llm_in_flight_denied user=<사용자 UUID>`: 같은 계정의 LLM 모드 학습 계획이 이미 진행 중이라 429를 돌려준 요청마다(계정당 동시 1건).
  - 찾아보기(`docker compose logs --since 1h backend | grep …`): `event=verifier_budget_denied`(검증기 호출 한도에 닿음. 계속 나오면 한 계정·IP가 몰아
    보내는지 `user`로 확인), `event=login_locked`(잠긴 이메일에 대한 시도), `event=oauth_login`이면서 `outcome=refused_password_account`(같은 `user`에
    반복되면 그 주소를 남이 먼저 가입해 둔 것일 수 있음), `event=oauth_exchange_denied`(그 `provider`의 소셜 로그인이 모든 사용자에게 429가 되고 있음.
    nginx의 콜백 제한과 nginx 로그의 `limiting requests`를 확인), `event=verify_shape_refused`(발급한 배지의 검증에서 나오면 토큰 형식이 모양 검사와 맞지
    않는 것. 아래 "배포 후 확인"). `verifier_calls` 줄의 `calls`를 분 단위로 더한 값이 BE가 DXWorks로 보낸 검증 호출 수이고, 로그인 사용자 몫의 프로세스
    한도는 분당 600입니다.
- [ ] 접근 로그: BE 접근 로그는 공유 링크 토큰과 DID를 가리고(`/api/v1/public/shares/[redacted]`, `/api/v1/subjects/[did]/...`), 소셜 로그인 콜백 경로
  (`…/google/callback?…`·`…/github/callback?…`·`…/naver/callback?…`)의 쿼리를 통째로 가립니다(`?[redacted]`). nginx 접근 로그(nginx 컨테이너 stdout)는
  요청 줄을 그대로 남기므로 공유 링크 토큰(아직 URL 경로에 들어감. 경로에서 빼는 변경은 하지 않음)과, 제공자가 FE 콜백 주소
  (`https://<도메인>/auth/<제공자>/callback?code=…&state=…`)로 돌려보낸 쿼리(한 번만 쓸 수 있는 인가 코드와 `state`)가 남습니다(FE는 콜백 화면에서
  주소창의 쿼리를 바로 지우고 `POST` 본문으로 BE에 보냄). 특히 네이버는 PKCE를 쓰지 않아, 사용자의 `POST`가 끝나지 않은 콜백 주소(탭을 닫았거나 네트워크
  오류)를 로그에서 본 사람이 코드가 만료되기 전에 그 `code`·`state`로 대신 로그인할 수 있습니다. 로그는 `docker` 그룹(root와 같은 권한)만 읽으므로 서버
  계정을 운영자로 좁히고, 로그를 서버 밖으로 옮길 때는 이 줄을 지우거나 접근을 좁힙니다(nginx 로그 형식은 바꾸지 않았음).

## 배포 후 확인

- [ ] 가입 → 로그인 → 지갑, 모의 발급 → 검증(`MOCK_ISSUER_ENABLED=true`일 때), `/career` 비교·학습 계획, `/dashboard`·`/career` 새로고침. 브라우저 개발자
  도구 콘솔을 연 채로 하고 `Content Security Policy` 오류가 없는지, Pretendard 글꼴이 적용됐는지 봅니다.
- [ ] DXWorks 첫 연결 확인(실제 협력사 서버로는 아직 확인하지 못한 네 가지이므로 실제 사용자에게 열기 전에 시험 계정으로 한 번 확인):
  - 발급한 배지의 검증: 배지를 하나 발급해 `POST /api/v1/verify`로 보냅니다(`MOCK_ISSUER_ENABLED=true`면 발급 화면에서 "검증하고 지갑에 저장하기"까지.
    모의 발급을 켜지 않는 배포면 협력사가 실제로 발급한 배지 토큰을 API로 직접 보냄). `success: true`로 저장되고 BE 로그에 `event=verifier_calls …
    route=verify_badge calls=1 failed=0` 줄이 남으면 됩니다. 응답이 200 `success: false`·`Badge verification failed.`(화면은 "배지 검증에 실패했습니다")인데
    로그에 `event=verify_shape_refused` 줄이 있고 `event=verifier_calls` 줄이 없으면, 실제 토큰의 형식이 BE의 JWT 모양 검사(compact JWS, 조각 끝의 `=`
    패딩, SD-JWT만 받음)와 맞지 않아 DXWorks로 보내지도 않은 것입니다. 위조 배지를 거절할 때와 같은 응답이라 화면만으로는 구분되지 않고, 이 상태에서는
    발급한 배지를 지갑에 넣을 수 없으므로 서비스를 열기 전에 팀에 알립니다(`BE/app/domain/credential.py`의 `is_compact_jws`를 실제 형식에 맞춰야 함).
  - 압축 응답: 배지 검증이나 모의 발급이 502이고 BE 로그의 `Provider error:` 줄이 `DXWorks가 요청하지 않은 압축 응답을 반환했습니다`면, 협력사(또는 중간
    CDN)가 BE의 `Accept-Encoding: identity` 요청과 달리 `gzip`·`deflate`가 아닌 방식(`br`·`zstd`, 겹친 압축 등)으로 압축한 응답을 보낸 것입니다. `gzip`·
    `deflate` 응답은 BE가 1 MiB 상한 안에서 직접 풀지만 그 밖의 방식은 거절하므로, 이 줄이 나오면 모든 DXWorks 호출이 실패합니다(코드 수정이 필요하니
    팀에 알림).
  - 쓰레기 토큰에 대한 협력사 응답: JWS 모양만 맞춘 쓰레기 토큰 하나를 `POST /api/v1/verify`로 보내고(`{"type": "badge", "token":
    "eyJhbGciOiJFZERTQSJ9.e30.AAAA"}`) 협력사가 무엇으로 답했는지 적어 둡니다. BE 응답이 200 `success: false`면 협력사가 2xx로 거절한 것이고, 502면 BE 로그
    `Provider error: … (upstream=<상태 코드>)` 줄의 상태 코드를 봅니다. 2xx나 4xx(400·404·422 등)면 위 "10분에 10번" 거절 창이 쓰레기 토큰을 묶습니다.
    5xx(또는 401·403·408·429·3xx, 응답 없음)면 BE가 검증기 장애로 보아 거절 창에 세지 않으므로, 쓰레기 토큰은 계정별 시간당 한도로만 묶입니다(아래
    "하지 않은 것").
  - 공개 항목이 덧붙은 배지: 위에서 발급한 실제 배지 토큰 끝에 쓰레기 공개 항목 하나를 붙여(`<jws>~AAAA~`) 같은 API로 보내고 협력사가 받아들이는지 적어
    둡니다. BE의 모양 검사는 SD-JWT 형식을 받으므로 이 토큰은 DXWorks까지 가고, 협력사가 덧붙은 부분을 무시하고 통과시키면(`success: true`) 그 문자열
    그대로 서명 원본으로 저장되어 공유 링크 조회에도 나갑니다.
- [ ] `curl -i 'https://<도메인>/api/v1/users/oauth/google/callback?code=x&state=y'`가 404 JSON(`Not Found`)인지 확인합니다(`github`·`naver`도 같음).
  400(`Invalid or expired OAuth state`)이면 BE가 `APP_ENV=development`로 떠 있는 것입니다(API 문서와 개발용 기본값이 켜진 상태).
- [ ] 켠 소셜 로그인마다 버튼으로 가입·로그인이 되는지, 이메일·비밀번호로 먼저 가입해 둔 주소의 소셜 계정으로는 "이 이메일은 <제공자>로 로그인할 수
  없습니다" 안내(409)가 나오는지 확인합니다. GitHub은 승인 화면에 이메일 권한(`user:email`)이 나오고 인증된 기본 이메일로 가입되는지(이메일 권한을 빼면
  "이메일 제공 동의가 필요합니다.", 400 `OAuth email is required`), 네이버는 검수 전이면 멤버로 등록한 아이디로 확인합니다. GitHub 로그인이 콜백에서 계속
  실패하면(400·502) BE 로그의 `GitHub OAuth token request rejected: error=…`·`Provider error` 줄을 봅니다(`redirect_uri_mismatch`는 OAuth App의 callback
  URL과 `GITHUB_REDIRECT_URI`, `incorrect_client_credentials`는 `GITHUB_CLIENT_ID`·`GITHUB_CLIENT_SECRET`, `invalid_grant`가 계속 나오면 PKCE
  `code_verifier` 처리를 확인).
- [ ] 토큰 없이 `curl -i https://<도메인>/api/v1/subjects/did:key:z6MkDeployCheck/careers`가 401 JSON(`Authentication required`)인지 확인합니다.
  404(`User not found or not verified yet.`)가 오면 토큰 없는 요청을 받던 이전 이미지가 배포된 것입니다.
- [ ] 로그아웃한 뒤 그 전 토큰으로 `curl -i -H "Authorization: Bearer <토큰>" https://<도메인>/api/v1/users/me`가 401(`Invalid token`)인지 확인합니다.
- [ ] `curl -i https://<도메인>/api/v1/public/shares/nonexistent`가 404 JSON(`Share link not found or expired`)인지 확인합니다(HTML이면 BE가 아니라 nginx가
  답한 것: 502면 backend 상태). 이 응답에 `cache-control: no-store`·`x-content-type-options: nosniff`·`strict-transport-security`가 있어야 합니다.
- [ ] `curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' http://<도메인>/dashboard`가 `301 https://<도메인>/dashboard`인지 확인합니다.
- [ ] `curl -s -D - -o /dev/null https://<도메인>/dashboard`에 `strict-transport-security`·`content-security-policy`·`x-frame-options: DENY`·
  `x-content-type-options: nosniff`·`referrer-policy`가 있는지 확인합니다.
- [ ] SPA fallback: `curl -s -o /dev/null -w '%{http_code} %{content_type}\n' 'https://<도메인>/auth/google/callback?x=1'`(`/share/abc`도)이 `200 text/html`인지
  확인합니다.
- [ ] 밖에서 backend·ai·postgres 포트에 닿지 않는지: 서버 밖 PC에서 `curl -s -m 5 -o /dev/null -w '%{http_code}\n' http://<서버 공인 IP>:3000/health`(`:8100`도)가
  연결 실패(`000`)이고 `nc -z -w 5 <서버 공인 IP> 5432`가 실패해야 합니다. 서버에서는 `docker compose ps`의 PORTS에서 호스트로 이어진(`->`) 포트가 nginx의
  `80->8080`·`443->8443`뿐이어야 합니다(backend·ai·postgres는 `3000/tcp`처럼 화살표 없이 보임). `curl -sk -m 5 https://<서버 공인 IP>/`는 TLS 연결이 거절되어야 합니다(HTTPS 모드).
- [ ] DB까지 닿는지: `curl -s -o /dev/null -w '%{http_code}\n' -X POST https://<도메인>/api/v1/users/login -H 'Content-Type: application/json' -d
  '{"email":"smoke-test@example.com","password":"not-a-real-password"}'`가 401이면 BE가 DB에서 계정을 찾아본 것이고(DB·TLS·권한 정상), 500·503이면 DB
  설정을 봅니다(`/health`는 DB를 보지 않음). 로그인한 뒤 `/career`의 공고 목록이 뜨면 BE→AI(`AI_SERVICE_TOKEN`)도 정상입니다.
- [ ] 모의 발급을 켰다면 허용하지 않은 템플릿이 거절되는지 확인합니다: 로그인 토큰으로 `POST /api/mock-issuer/issue` `{"holderDid": "<본인 DID>",
  "templateId": "not-allowed"}` → 403 `Template not allowed`.
- [ ] (선택) IP별 제한: `for i in $(seq 1 90); do curl -s -o /dev/null -w '%{http_code}\n' https://<도메인>/api/v1/job-postings; done | sort | uniq -c`가
  401(토큰 없음) 60여 개와 429로 나뉘는지 봅니다(그 IP의 공고 목록이 1분쯤 막힘).
- [ ] 인증서 갱신: `sudo certbot renew --dry-run`이 성공하고 `systemctl list-timers | grep certbot`에 타이머가 있는지 확인합니다.
- [ ] 백업: root crontab에 `backup-db.sh`가 있고(README ⑩) 다음 날 `/var/backups/vc-career`에 덤프가 생겼는지, 서버 밖에도 사본을 두는지 확인합니다.

## 하지 않은 것 (운영 전에 알아 둘 것)

- DXWorks 응답: `valid: true`만 있고 서명·발급자 신뢰 결과가 빠진 2xx 응답도 지금은 통과(`verified`)로 처리합니다. 두 항목이 반드시 있어야 통과시키는
  검사는 실제 응답 표본을 확인한 뒤로 미뤘습니다. (`valid`가 참·거짓이 아닌 응답은 판정으로 읽지 않고 검증기 오류로 처리합니다.) 실제 DXWorks가 발급한
  토큰이 BE의 JWT 모양 검사를 통과하는지, 실제 서버가 어떤 압축으로 응답하는지도 실제 서버로는 확인하지 못했습니다(위 "배포 후 확인"의 DXWorks 첫 연결
  확인).
- DXWorks 호출: 호출마다 새 연결을 엽니다. 요청한 쪽이 연결을 끊어도 이미 시작한 재검증은 끝까지 돕니다(호출 수는 위 한도로 묶임). 근거 조회
  API(`GET /api/v1/subjects/{did}/evidence`)와 서비스 키(`SERVICE_API_KEY`)는 그대로 있습니다.
- 한도의 범위: BE 자체 한도는 BE 프로세스 메모리에서 세고 backend 컨테이너를 다시 시작하거나 다시 만들면(배포마다) 지워집니다. nginx의 IP별 제한도 nginx를
  다시 시작하면(배포, 인증서 갱신) 지워집니다. 직무 비교·학습 계획은 사용자별로 24시간에 각 200건(DB로 셈), LLM 모드 학습 계획은
  `CURRICULUM_LLM_DAILY_LIMIT`건(기본 20)까지이고, 지난 직무 비교·학습 계획을 지우는 정리 작업은 없습니다(계정 하나가 쌓는 양은 하루 약 400건으로 묶임).
  검증기 호출에는 하루 단위 한도가 없고 가입 수도 제한하지 않으므로, 계정을 30개쯤 만들어 계속 부르면 BE 프로세스의 검증기 몫(분당 600번)을 비워 둘 수
  있고 그동안 다른 사용자의 검증·재검증·포트폴리오 저장·직무 비교는 429입니다(그때도 로그인 사용자 몫으로 DXWorks에 나가는 호출은 분당 600번 수준으로
  묶임). 소셜 로그인 토큰 교환 몫(제공자별 분당 300번)도 여러 IP에서 몰아 보내면 비울 수 있습니다. 둘 다 nginx의 IP별 제한과 감사 로그
  (`event=verifier_budget_denied`·`event=oauth_exchange_denied`)로 보고 대응합니다.
- 배지 검증의 거절 창: 검증기 장애로 보는 답(5xx·401·403·408·429·3xx, 응답 없음)은 창에 세지 않습니다(실제 장애 때 사용자가 잠기지 않게). 그래서 실제
  DXWorks가 쓰레기 토큰에 4xx가 아니라 5xx로 답한다면, 쓰레기 토큰은 10분에 10번이 아니라 계정별 호출 몫(첫 한 시간 2,400번쯤, 그 뒤 분당 20번)만큼
  DXWorks로 나갑니다. 장애로 끝난 검증을 따로 세는 창은 두지 않았습니다(위 "배포 후 확인"에서 실제 응답을 확인). 신원 VP 검증(`type: identity`, 기본으로
  꺼져 있음)에는 거절 창이 없어, 켜면 계정별 호출 몫만 적용됩니다.
- 일괄 재검증: DXWorks가 장애 응답(5xx 등) 없이 모든 호출에 판정이 아닌 200이나 400으로 답하는 상태에서는 재검증 한 건이 502가 아니라 200
  `success: false`(`verifier_error`)입니다. 그래서 FE의 일괄 재검증이 첫 항목에서 멈추지 않고 자격증명마다 한 번씩 부릅니다(계정별 호출 몫 안이고 저장된
  상태는 바뀌지 않음. 화면에는 모든 항목이 "검증 서버가 확인하지 못함"으로 나옴).
- 공유 링크 조회의 대기: 같은 서명 원본을 먼저 물은 조회를 기다리는 시간은 조회 전체가 아니라 자격증명마다 `DXWORKS_TIMEOUT_SECONDS`의 4배까지입니다.
  자격증명은 5개씩 처리하므로, 먼저 물은 호출이 끝내 끝나지 않으면(어댑터의 시간 제한까지 듣지 않는 고장이 겹친 경우) 조회 하나가 (자격증명 수 ÷ 5,
  올림) × 4배만큼 걸릴 수 있습니다(50개, 기본 15초면 10분. nginx는 125초에 504를 돌려주지만 BE는 끝까지 기다림). 한 번 기다림이 시간 초과로 끝난 뒤 남은
  자격증명의 기다림을 건너뛰는 처리는 하지 않았습니다.
- 모의 발급의 실패 차단기: 발급을 멈춘 동안 1분에 한 건 나가는 요청은 그때 먼저 온 요청이므로 어느 계정이든 차지할 수 있습니다(DB 한도나 설정 누락으로
  협력사에 가지 못한 요청은 그 기회를 쓰지 않음). 그 한 건만 성공하고 나머지는 실패하는 협력사(예: 우리를 분당 한 건쯤으로 제한)나, 허용 템플릿 가운데
  발급되는 것과 협력사가 거절하는 것을 번갈아 보내는 계정들 앞에서는 성공할 때마다 실패 기록이 지워져 다시 `MOCK_ISSUER_GLOBAL_DAILY_LIMIT`번까지
  나가므로, 그때 실패한 발급 요청의 하루 양은 차단기가 아니라 계정 수 × 사용자별 시도 한도(기본 하루 30번)가 묶습니다(해시까지 기록된 성공한 발급은 DB
  발급 한도가 그대로 묶고, 해시를 기록하지 못한 발급은 진행 중 창 10분이 지나면 사용자별 하루 발급 한도 × 계정 수만 묶음).
- 관리형 지갑(홀더 키): 가입할 때 사용자마다 홀더 개인키를 만들어 암호화해 `holder_keys`에 저장하지만, 그 키를 복호화하거나 그 키로 서명하는 코드
  (`BE/app/services/key_custody.py`의 `sign`)를 부르는 곳은 아직 없습니다(신원 자격증명 기능용으로 남겨 둔 것). 그래서 `HOLDER_KEY_ENCRYPTION_KEY`가
  바뀌어 기존 키를 복호화할 수 없게 되어도 BE는 알아채지 못하고 `GET /api/v1/users/me`는 계속 `wallet.custody: managed`로 답합니다. 시작할 때 마스터 키를
  확인하는 검사와 재암호화 도구는 없습니다. 쓰는 기능이 생기기 전까지 개인키를 저장하지 않게 할지는 정하지 않았습니다.
- 소셜 로그인: 비밀번호로 가입한 계정에 나중에 소셜 로그인을 붙이는 기능은 없습니다. 이 규칙 이전에 연동된 계정(비밀번호 + Google)은 두 방법 모두 계속
  로그인됩니다. 서버는 PKCE를 필수로 요구하지 않고 `state`를 브라우저에 묶지 않습니다(FE가 `state`와 PKCE를 확인. 세 제공자 같음). 제공자 쪽 이메일이
  바뀌어도 `users.email`은 그대로입니다. `redirect=true`(302로 제공자에 보내기)와 개발용 `GET` 콜백 경로는 남아 있습니다(운영에서 `GET` 콜백은 404).
  이메일 검사가 연동 확인보다 먼저라, 이미 연동된 사용자도 제공자에서 이메일 제공 동의(GitHub은 이메일 권한)를 철회하면 400 `OAuth email is required`,
  Google·GitHub이 이메일을 인증되지 않았다고 알리면 400 `OAuth email is not verified`로 로그인이 막힙니다. 보이지 않는 서식 문자나 소문자로 바꾸면 ASCII가
  되는 문자(켈빈 기호 U+212A 등)가 든 제공자 이메일은 다른 계정과 같게 읽히거나 똑같아 보이므로 502로 받지 않습니다.
  - 네이버: 이메일 인증 여부를 알려 주지 않으므로, 네이버로 만든 계정의 이메일은 그 사람이 소유하지 않은 주소일 수 있습니다. 그런 계정은 다른 로그인
    방법과 연동되지 않고, 그 주소의 진짜 주인은 같은 이메일로 새로 가입할 수 없습니다(이메일 가입은 400 `Email already registered`, Google·GitHub 로그인은
    409 `This email is already used by another sign-in method. …`). 남의 주소로 만든 네이버 계정을 찾거나 지우는 관리 도구는 없습니다. 네이버는 기존 OAuth
    2.0 엔드포인트(`/oauth2.0/*`)를 쓰고 PKCE를 보내지 않습니다(네이버 OIDC 엔드포인트는 쓰지 않음). 그래서 콜백 주소의 `code`·`state`가 새면(위 nginx
    접근 로그) 코드가 만료되기 전에는 누구나 그 값으로 로그인을 마칠 수 있습니다(`state`를 브라우저 쿠키에 묶는 처리는 하지 않음). 또 네이버 이메일은
    사용자가 정하는 값이므로, 아무 주소나 네이버 연락처 이메일로 넣어 보면 그 주소가 어떤 방법(비밀번호·Google/GitHub·네이버)으로 가입됐는지 409 문구로
    알 수 있습니다(이메일 가입도 이미 있는 주소인지는 알려 줌).
  - GitHub: 로그인에 받은 액세스 토큰은 저장·로그하지 않고 버리기만 하고 폐기 API(`DELETE /applications/{client_id}/token`)는 부르지 않습니다. 새 OAuth
    App은 토큰이 8시간 뒤 만료되므로 OAuth App 설정의 "Expire user access tokens"를 켜 둡니다(끄면 토큰이 1년 동안 쓰지 않아야 자동 폐기됨). 이메일 목록
    (`/user/emails`)은 첫 100개만 보고 그 안에 기본(primary) 이메일이 없으면 400 `OAuth email is required`입니다. `users.noreply.github.com` 주소도 기본·인증
    이메일이면 계정 이메일로 씁니다(따로 막지 않음). 사용자가 이메일 권한을 뺀 경우와 GitHub 레이트 리밋의 403을 구분하지 않아 둘 다 400
    `OAuth email is required`입니다. 토큰 요청 오류의 HTTP 상태와 PKCE 불일치 오류 이름은 GitHub 문서에 없어, 2xx·4xx 응답의 `bad_verification_code`·
    `redirect_uri_mismatch`·`invalid_grant`(400 `Invalid or expired OAuth authorization code`)와 `unverified_user_email`(400 `OAuth email is not verified`)만
    사용자 쪽 400으로 보고, 3xx·5xx는 `error` 내용과 관계없이 502로 봅니다. GitHub의 인증(verified) 표시는 한 번 인증한 기록이라 다시 확인하지 않습니다:
    예전에 그 주소를 쓰던 사람(퇴사·도메인 만료 등)의 GitHub 계정에 그 주소가 인증된 기본 이메일로 남아 있으면, 지금 주인이 Google로 만든 같은 이메일의
    계정에 연동될 수 있습니다(반대로 그런 GitHub 계정으로 먼저 만든 계정에는 지금 주인의 Google 로그인이 연동됨). 연동 전에 기존 방법으로 다시 로그인하게
    하는 처리는 하지 않았습니다.
  - Google 계정의 이메일이 바뀌면 그 계정의 Google 연동 이메일(로그인할 때마다 갱신)이 계정 이메일과 달라져, 같은 이메일의 GitHub 로그인이 그 계정에
    연동되지 않고 409가 됩니다(잘못 붙는 일은 없음. GitHub 기본 이메일이 바뀐 경우도 반대로 같음).
  - 예전에 쓰던(지금은 로그인 경로가 없는) 제공자의 연동 행이 개발 DB에 남아 있으면 지우지 않고 그대로 둡니다(로그인·연동 확인에 쓰지 않고,
    `GET /api/v1/users/me`의 `oauth_providers`에는 이름이 나오지만 FE는 모르는 이름을 무시함. 마이그레이션 없음). 그 제공자로만 만든 비밀번호 없는 개발용
    계정은 더 이상 로그인할 수 없고, 같은 이메일의 GitHub·Google 로그인도 409(다른 방법으로 가입한 이메일)입니다. 필요하면 그 계정을 지우고 다시 가입합니다.
  - GitHub·네이버의 실제 동작(로컬 콜백 주소 허용, 오류 응답 본문·상태 코드, 승인 화면)은 실제 서버로 확인하지 못했습니다(테스트는 가짜 응답만 씀. GitHub은
    OAuth App을 만들고 키를 넣은 뒤 따로 확인). 서비스를 열기 전에 위 "배포 후 확인"으로 한 번 확인합니다.
- DB: 연결 풀이 아니라 요청마다 새 연결을 엽니다(같은 서버 안이지만 매번 TLS·SCRAM 인증 절차). DB 접속 실패·연결 수 초과는 503 `Service is busy`이고,
  트랜잭션을 연 채 쉬다 끊긴 세션의 요청만 500입니다. 철회·만료된 공유 링크와 지난 직무 비교·커리큘럼을 지우는 정리 작업, 목록의 페이지 나누기는
  없습니다(목록은 200개까지). DB는 같은 서버의 컨테이너 하나이고 복제본이 없습니다(백업은 [README.md](README.md) ⑩).
- 마이그레이션의 잠금: 13(쓰지 않는 인덱스·표 삭제)과 `users`를 고치는 04·09는 `users` 표에 배타 잠금(`ACCESS EXCLUSIVE`)이 필요하고, 실행기에는 잠금 대기
  제한이 없습니다. 그 DB에 `users`를 읽는 트랜잭션이 열려 있으면(백업의 `pg_dump`, 운영자가 열어 둔 `psql` 등) 마이그레이션이 그 뒤에서 기다리고, 그동안
  `users`를 읽는 요청(로그인한 모든 요청)이 다시 그 뒤에 줄을 섭니다. 지금 BE는 5초 뒤 503으로 답하지만, `docker compose up -d`가 migrate를 돌리는 동안
  아직 떠 있는 이전 버전 backend는 잠금 제한이 없어 끝날 때까지 멈춥니다. 배포하기 전에 `pg_stat_activity`로 오래 열린 트랜잭션이 없는지 확인하고
  요청이 적은 시간에 배포하며, 몇 초 안에 끝나지 않으면 다른 SSH 세션에서 `docker compose stop migrate`로 멈춘 뒤 다시 실행합니다(13은 한 트랜잭션이라 멈추면
  통째로 롤백).
- AI 서비스: BE는 요청마다 검토된 공고·학습자료 전체를 AI로 보냅니다(캐시 없음). `AI_MAX_CONCURRENCY`는 BE 프로세스 전체 상한이고 한 계정은 그 가운데
  동시에 2자리까지만 쥐므로(분당 30번), 계정 두 개면 기본 4자리를 모두 쥘 수 있습니다(nginx의 IP별 제한과 감사 로그 `event=corpus_budget_denied`로 봄).
  AI 서비스의 `/docs`는 켜져 있습니다(ai는 포트를 열지 않아 밖에서 닿지 않음). AI의 `GET /v1/market/catalog`는 남아 있지만 지금은 BE·FE 어디에서도
  부르지 않습니다. `POST /v1/market/snapshots`에는 요청 전체의 계산량 상한이 없어, 서비스 토큰을 가진 쪽이 계약 상한 안에서 일부러 만든 요청 하나가 CPU 약
  286초를 씁니다([ai/docs/CAREER_AI_SERVICE.md](../ai/docs/CAREER_AI_SERVICE.md)). 로컬 Qwen 실행과 `requirements-gpu.txt`는 이번에 확인하지 않았습니다.
- AI 업그레이드: 결합 문자가 30개 넘게 이어진 구간을 자르는 정규화 변경 때문에, 그런 구간이 든 공고·학습자료가 이미 저장된 DB에서는 그 공고가 들어가는
  공고 목록·직무 비교가 409가 될 수 있습니다(일부러 만든 입력에서만 확인된 경우). 기존 DB에 새 AI를 올리기 전에 [BE/README.md](../BE/README.md)의
  "결합 문자 점검"으로 한 번 확인합니다. 자동으로 고쳐 주는 마이그레이션은 없습니다.
- 시장 통계: 출처는 한 곳이고 공고 번호는 사람이 모읍니다. 수집 코드는 robots.txt를 읽지 않으므로 수집 전에 사람이 확인합니다([BE/README.md](../BE/README.md)
  "직무별 채용 시장 통계"). 공고 한 건을 읽는 30초 상한은 본문 데이터가 올 때만 재므로, 응답 머리말을 아주 천천히 보내는 서버에는 걸리지 않습니다(읽기
  한 번마다의 15초 제한만 있어, 머리말을 한 줄씩 5초마다 보내는 시험 서버는 보내는 동안 계속(75초) 수집을 붙잡았음). 전화번호·이메일 가리기는 `/`·`_`·
  `~`로 나눈 번호, 대표번호, `(at)` 표기, `@` 앞이 한글인 주소를 잡지 않습니다.
- FE: Pretendard 웹폰트는 CDN에서 받습니다(자체 호스팅·SRI 없음). 자동 테스트가 없어 CI는 lint·타입 검사·빌드와 `dist` 검사만 합니다. 포트폴리오 화면의
  대화는 모의 응답입니다.
- CI: Strix 보안 스캔 워크플로(`.github/workflows/main.yml`)는 PR마다 돌지만 저장소 Secrets에 `STRIX_LLM`·`LLM_API_KEY` 중 하나라도 없으면 스캔을 건너뛰고
  성공으로 끝납니다. Secrets를 등록하기 전에는 실제 스캔이 돌지 않는 상태이고, 등록해 쓸지 워크플로를 지울지는 정하지 않았습니다.
- 설정 검사: 운영 설정 검사·범위 검사 오류와 없는 수준 이름의 `LOG_LEVEL`(예: `verbose`), 형식이 틀린 `HOLDER_KEY_ENCRYPTION_KEY`, AI의 잘못된
  `AI_MAX_BODY_BYTES`는 traceback 없이 한 줄(종료 코드 1, 시장 통계 배치의 `LOG_LEVEL`은 오류 한 줄과 종료 코드 2)로 끝납니다. 숫자가 아닌 `DB_PORT`는
  접속할 때마다 실패합니다(운영 BE는 시작 DB 점검에서 `ValueError: DB_PORT and DB_CONNECT_TIMEOUT must be integers`로 끝남). nginx IP별 제한 값은 부하
  시험으로 정한 값이 아닙니다.
- NCP 배포: 서버 한 대라 그 서버가 멈추면 서비스도 멈추고, 스테이징 환경이 따로 없으며, 배포는 컨테이너를 다시 만드는 방식이라 잠깐 끊깁니다. CI는
  `deploy/` 구성(compose·nginx 설정·스크립트)을 검사하지 않고 BE 이미지를 운영 모드로 띄워 보지 않습니다. Dockerfile은 패키지 해시 검사와 기본 이미지
  다이제스트 고정을 하지 않고, `deploy.sh`는 `--pull`로 그때의 `node:22`·`nginx-unprivileged:stable-alpine`·`python` 태그를 받습니다. 나머지는
  [README.md](README.md) "받아들이는 잔여 위험".

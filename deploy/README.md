# NCP 배포 (서버 한 대 + Docker Compose)

네이버 클라우드(NCP) 서버 한 대에 FE·BE·AI·DB를 모두 올리는 운영 구성입니다. 로컬 실행은 [서비스 실행 방법](../docs/GETTING_STARTED.md),
항목별 점검은 [CHECKLIST.md](CHECKLIST.md), 변수 표는 [ENV_MATRIX.md](ENV_MATRIX.md)에 있습니다.

```
인터넷 ── 80/443 ──> nginx  (FE 정적 파일, HTTPS, IP별 요청 제한, /api/* → backend)
                      └─ edge ──> backend:3000 ── backend ──> ai:8100 ──> Google Gemini API (LLM 모드일 때)
                                    │                └──> DXWorks API, Google·GitHub·네이버 로그인 (HTTPS)
                                    └─ db (밖으로 나가는 길 없음) ──> postgres:5432 (TLS + 비밀번호)
```

- 밖으로 여는 포트는 **22(SSH)·80·443**뿐입니다. backend(3000)·ai(8100)·postgres(5432)는 서버 안 Docker 네트워크에서만 닿습니다.
- 서버 사양: 2 vCPU / 메모리 4 GB / SSD 50 GB / 공인 IP 1개. 컨테이너 메모리 상한은 postgres 768 MB, backend 1.5 GB, ai 1 GB,
  nginx 128 MB이고, `setup-server.sh`가 이미지 빌드 때를 대비해 스왑 2 GB를 만듭니다. 쉴 때 세 앱 컨테이너는 합쳐서 약 150 MB를 씁니다.
- 예전 AWS 구성(ECS·RDS·CloudFront·WAF)은 이 구성으로 바뀌었습니다. 대응: CloudFront·S3 → nginx, WAF 속도 규칙 → nginx
  `limit_req`, RDS → postgres 컨테이너(TLS), Secrets Manager → 서버의 `deploy/.env`·`backend.env`·`ai.env`(권한 600),
  ECS 작업 정의 → `docker-compose.yml`, CloudWatch → `docker compose logs`.

## OS

**Ubuntu 24.04 LTS**(또는 22.04)를 고릅니다. `scripts/setup-server.sh`는 Ubuntu만 다룹니다. Rocky Linux도 Docker는 돌지만 패키지 관리자
(dnf)·방화벽(firewalld)이 다르고 SELinux 때문에 바인드 마운트에 `:z` 라벨이 필요해 이 문서의 명령을 그대로 쓸 수 없습니다.

## 이 폴더의 파일

| 파일 | 내용 |
|---|---|
| `docker-compose.yml` | 운영 구성 (postgres, migrate, backend, ai, nginx) |
| `.env.example` → `.env` | compose가 읽는 값: 도메인 `SERVER_NAME`, DB 비밀번호 3개, `AI_SERVICE_TOKEN` |
| `backend.env.example` → `backend.env` | BE만 읽는 값: JWT·지갑 키, DXWorks, 소셜 로그인, 모의 발급, 권한·한도 |
| `ai.env.example` → `ai.env` | AI만 읽는 값: Gemini 키와 LLM 설정 |
| `nginx/` | FE 빌드 + nginx 이미지(`Dockerfile`), HTTP/HTTPS 설정, 보안 헤더, IP별 요청 제한 |
| `postgres/` | DB TLS 시작 래퍼, 접속 규칙(`pg_hba.conf`), 첫 실행 때 계정을 만드는 `initdb/10-roles.sh` |
| `scripts/setup-server.sh` | 서버 준비 (Docker, 방화벽, certbot, 자동 보안 업데이트, 스왑) |
| `scripts/init-secrets.sh` | `.env`·`backend.env`·`ai.env`와 DB TLS 인증서를 만듦 (있으면 건드리지 않음) |
| `scripts/deploy.sh` | 빌드 + 시작 (업데이트할 때마다) |
| `scripts/issue-cert.sh`, `install-cert.sh` | Let's Encrypt 인증서 발급·설치 (갱신은 자동) |
| `scripts/backup-db.sh` | DB 백업 (매일 cron) |
| `scripts/import-market.sh` | (선택) 직무별 시장 통계 반입 |

`.env`·`backend.env`·`ai.env`·`secrets/`는 [.gitignore](.gitignore)에 있어 커밋되지 않습니다.

## 배포 순서

### ① 서버 만들기 (NCP 콘솔, 서버 담당자)

- 서버: Ubuntu 24.04, 2 vCPU / 4 GB, SSD 50 GB, 공인 IP 1개
- ACG(방화벽) 인바운드: TCP 22 ← 운영자 공인 IP만, TCP 80·443 ← `0.0.0.0/0`. 아웃바운드: 전체 허용(또는 최소 TCP 80·443, UDP 53·123).
  서버가 나가야 하는 곳: Docker Hub·PyPI·npm(빌드), Let's Encrypt, Google Gemini API, DXWorks, Google·GitHub·네이버 로그인.
- SSH: 운영자 공개키를 서버 계정의 `~/.ssh/authorized_keys`에 넣습니다.

### ② 접속하고 코드 받기

```bash
ssh -i ~/.ssh/ncp_vc_career <계정>@<서버 공인 IP>
sudo install -d -o "$USER" -g "$USER" /opt/vc-career
git clone https://github.com/HYUJIM/VC_AI_Career_agent.git /opt/vc-career
cd /opt/vc-career
```

저장소가 비공개면 GitHub 저장소의 Deploy key(읽기 전용)로 서버에서 만든 키를 등록하고 `git@github.com:…` 주소로 받거나,
내 PC에서 `rsync -az --exclude node_modules --exclude .venv ./ <계정>@<IP>:/opt/vc-career/`로 올립니다.

### ③ 서버 준비 (한 번)

```bash
sudo deploy/scripts/setup-server.sh
exit   # docker 그룹을 적용하려고 한 번 나갔다가 다시 접속
```

Docker Engine·Compose 플러그인(Docker 공식 저장소), ufw(22·80·443), certbot, 자동 보안 업데이트, 스왑 2 GB, 시간대 Asia/Seoul,
`/var/www/certbot`, `/var/backups/vc-career`를 준비합니다. 여러 번 실행해도 됩니다.

### ④ 비밀값 만들기

```bash
cd /opt/vc-career
deploy/scripts/init-secrets.sh
nano deploy/backend.env   # DXWORKS_*, (나중에) 소셜 로그인
nano deploy/ai.env        # LLM을 쓰면 CURRICULUM_LLM_ENABLED=true, GEMINI_API_KEY
```

- DB 비밀번호 3개·`AI_SERVICE_TOKEN`·`JWT_SECRET`·`HOLDER_KEY_ENCRYPTION_KEY`는 무작위로 채워지고 화면에 나오지 않습니다.
- `deploy/secrets/db-tls/`에 이 서버 전용 CA와 postgres 서버 인증서(10년)를 만듭니다. BE·migrate는 `PGSSLMODE=verify-full`로 확인합니다.
- **`deploy/.env`와 `deploy/backend.env`를 서버 밖(비밀번호 관리자 등)에도 백업합니다.** `HOLDER_KEY_ENCRYPTION_KEY`를 잃으면 사용자
  지갑 키를 복호화할 수 없고, DB 비밀번호는 DB 볼륨을 만든 뒤에는 이 파일에만 있습니다.
- 모든 계정의 LLM 모드(`CURRICULUM_LLM_USER_IDS=*`)와 모의 발급(`MOCK_ISSUER_ENABLED=true`)은 예시 파일에 켜져 있습니다.

### ⑤ 첫 배포 (도메인 전, 공인 IP로 HTTP)

```bash
deploy/scripts/deploy.sh
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost/   # 200
```

순서: postgres(새 볼륨이면 `vc_owner`·`vc_app` 계정과 DB 생성) → migrate(스키마, 시드 없음, 끝나면 종료) → ai → backend(healthy)
→ nginx. 처음에는 이미지 빌드로 5~10분 걸립니다. 브라우저에서 `http://<서버 공인 IP>/`가 열리면 됩니다.
이 단계는 비밀번호가 평문 HTTP로 오가므로 확인용으로만 쓰고, 실제 사용자는 ⑦ 이후에 받습니다.

### ⑥ 관리자(연구자) 계정

화면에서 관리자 계정으로 가입한 뒤 그 UUID를 찾아 `RESEARCHER_USER_IDS`에 넣습니다.

```bash
cd /opt/vc-career/deploy
docker compose exec postgres psql -U postgres -d vc_career_agent -c "SELECT id, email FROM users ORDER BY created_at DESC LIMIT 5;"
nano backend.env          # RESEARCHER_USER_IDS=<UUID>
docker compose up -d backend
```

### ⑦ 도메인과 HTTPS

1. DNS에 A 레코드: `<도메인>` → 서버 공인 IP (전파 확인: `dig +short <도메인>`).
2. `deploy/.env`에 `SERVER_NAME=<도메인>` (예 `career.example.com`, `https://` 없이) → `deploy/scripts/deploy.sh`.
3. 인증서: `sudo deploy/scripts/issue-cert.sh --email <운영자 이메일>` (Let's Encrypt 약관 동의는 certbot이 물음).
   먼저 시험하려면 `--staging`을 붙이고, 확인한 뒤 `--force-renewal` 없이 다시 실행합니다.
4. `https://<도메인>/`이 열리고 `http://`는 `https://`로 넘어가면 됩니다. IP로 HTTPS에 접속하면 TLS 연결이 거절됩니다.

`issue-cert.sh`는 먼저 `http://<도메인>/.well-known/acme-challenge/…`가 이 서버로 오는지 확인합니다(서버가 자기 공인 IP로 돌아오지 못하는
네트워크면 이 확인만 실패하므로 `SKIP_PROBE=1`). 인증서는 `deploy/secrets/tls/`로 복사되고 nginx가 다시 시작하면서 HTTPS 설정을 고릅니다.
갱신은 certbot의 systemd 타이머가 하고(`systemctl list-timers | grep certbot`), 갱신되면 `install-cert.sh`가 nginx를 다시 시작합니다.
갱신 시험: `sudo certbot renew --dry-run`.

### ⑧ 소셜 로그인 (도메인·HTTPS 뒤)

콜백은 모두 FE 주소 `https://<도메인>/auth/<google|github|naver>/callback`입니다. Google은 IP 주소나 `http://` 콜백을 받지 않으므로 ⑦이 먼저입니다.

1. 제공자 콘솔: Google(OAuth 클라이언트 "웹 애플리케이션", 승인된 리디렉션 URI), GitHub(OAuth App, Authorization callback URL),
   네이버(서비스 URL·Callback URL)에 위 주소를 등록합니다. 운영용 클라이언트에는 localhost 주소를 넣지 않습니다.
2. `deploy/backend.env`에 쓰는 제공자마다 `*_CLIENT_ID`·`*_CLIENT_SECRET`·`*_REDIRECT_URI`를 함께 넣고 `docker compose up -d backend`.
   하나라도 빠지거나 콜백이 `https://`가 아니면 BE가 시작하지 않고 `APP_ENV=production requires …` 한 줄을 남깁니다.
3. Google 동의 화면이 "테스트"면 등록한 테스트 사용자만, 네이버는 검수 전에는 멤버로 등록한 아이디만 로그인됩니다.

### ⑨ (선택) 직무별 시장 통계

```bash
deploy/scripts/import-market.sh
```

### ⑩ 백업

```bash
sudo crontab -e
# 매일 04:00, 14일 보관
0 4 * * * /opt/vc-career/deploy/scripts/backup-db.sh >> /var/log/vc-career-backup.log 2>&1
```

백업은 같은 디스크(`/var/backups/vc-career`)에 남으므로 가끔 내 PC로 옮깁니다:
`scp -i ~/.ssh/ncp_vc_career <계정>@<IP>:/var/backups/vc-career/<파일>.dump .` (백업 폴더는 root만 읽으므로 먼저 `sudo cp`로 홈에 복사).
NCP 콘솔의 서버 스냅샷도 함께 쓰면 좋습니다.

## 운영

| 할 일 | 명령 (`cd /opt/vc-career/deploy`에서) |
|---|---|
| 새 코드 배포 | `git -C /opt/vc-career pull && ./scripts/deploy.sh` |
| 상태 | `docker compose ps` (backend·ai·postgres는 `healthy`) |
| 로그 | `docker compose logs -f --tail=100 backend` (`ai`, `nginx`, `migrate`도 같음). 컨테이너마다 10 MB × 5개까지 남김 |
| 설정만 바꿈 (`backend.env`·`ai.env`) | `docker compose up -d backend` 또는 `ai` (다시 만들어야 값이 들어감, `restart`로는 안 됨) |
| 재시작 | `docker compose restart backend` |
| DB 콘솔 | `docker compose exec postgres psql -U postgres -d vc_career_agent` |
| 디스크 | `df -h /`, `docker system df` (`deploy.sh`가 옛 이미지를 지움) |

### 복원

```bash
docker compose stop nginx backend
docker compose exec -T postgres pg_restore -U postgres -d vc_career_agent --clean --if-exists < /var/backups/vc-career/<파일>.dump
docker compose start backend nginx
```

다른 서버로 옮길 때는 같은 `deploy/.env`·`backend.env`(특히 `HOLDER_KEY_ENCRYPTION_KEY`)를 먼저 복사한 뒤 새 볼륨을 만들고 복원합니다.

### 비밀번호 바꾸기

- DB 계정: `docker compose exec postgres psql -U postgres -c '\password vc_app'`로 바꾸고 `.env`의 `DB_APP_PASSWORD`를 같은 값으로 고친 뒤
  `docker compose up -d backend` (`vc_owner`는 `DB_OWNER_PASSWORD`, migrate가 씀). `.env`만 바꾸면 접속이 실패합니다.
- `AI_SERVICE_TOKEN`: `.env`를 고치고 `docker compose up -d ai backend`.
- `JWT_SECRET`: 바꾸면 모든 사용자가 한 번 로그아웃됩니다. `HOLDER_KEY_ENCRYPTION_KEY`는 **바꾸지 않습니다**.

## 문제 해결

| 증상 | 확인 |
|---|---|
| `deploy.sh`가 `required variable … is missing` | `scripts/init-secrets.sh`를 실행했는지, `deploy/.env`에 그 값이 있는지 |
| backend가 계속 재시작 | `docker compose logs --tail=50 backend`. `APP_ENV=production requires …` 한 줄이면 그 변수(`backend.env`), `DB startup check failed: …`면 DB 접속 |
| migrate 실패 (`service "migrate" didn't complete successfully`) | `docker compose logs migrate`의 `마이그레이션 실패:` 줄 |
| `password authentication failed` | `.env`의 DB 비밀번호를 볼륨을 만든 뒤에 바꿨는지 (위 "비밀번호 바꾸기") |
| 화면은 뜨는데 발급·검증·직무 비교가 503 `PROVIDER_NOT_CONFIGURED` | `backend.env`의 `DXWORKS_*` |
| LLM 모드만 503 `GENERATOR_CONFIGURATION_REQUIRED` | `ai.env`의 `CURRICULUM_LLM_ENABLED=true`·`GEMINI_API_KEY`, 그 뒤 `docker compose up -d ai` |
| 소셜 로그인 400 `Invalid redirect_uri` | 브라우저 주소(`https://<도메인>`)와 `*_REDIRECT_URI`·제공자 콘솔 값이 글자 그대로 같은지 |
| 429 `Too many attempts. Try again later.` | nginx의 IP별 제한(`nginx/http.conf`). 같은 IP의 단체 실습이면 그 경로의 값을 올리고 `deploy.sh` |
| 인증서 발급 실패 | DNS A 레코드, ACG·ufw 80, `docker compose ps`의 nginx, `sudo certbot certificates` |

## 받아들이는 잔여 위험

- 서버 한 대라 그 서버가 멈추면 서비스도 멈춥니다(재부팅 뒤에는 `restart: unless-stopped`로 다시 뜸). 백업이 같은 디스크에만 있으면 함께 잃습니다.
- IP별 제한은 nginx 메모리에서 세고 nginx를 다시 시작하면 지워집니다. BE 자체 한도(로그인 실패, 검증기 호출, LLM 하루 한도 등)도 프로세스
  메모리 또는 DB로 셉니다. 예전 AWS WAF의 관리형 규칙(IP 평판·알려진 공격 입력)에 해당하는 것은 없습니다.
- Docker가 연 포트(80·443)는 ufw 규칙과 별개로 열립니다. 이 구성은 그 둘만 열고, 바깥 방화벽은 NCP ACG입니다.
- BE↔AI 구간은 같은 서버 안 Docker 네트워크의 평문 HTTP입니다(서버 밖으로 나가지 않음). DB 구간은 TLS입니다.
- 무료 등급 Gemini 키는 Google이 입력·출력을 제품 개선에 쓸 수 있습니다. 실제 사용자에게 열 때는 유료 등급 키를 씁니다.

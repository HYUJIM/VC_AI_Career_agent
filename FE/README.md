# FE 파트 - VC AI Career Agent 프론트엔드

디지털 배지·자격증명(VC) 기반 초개인화 AI 커리어 에이전트 지갑의 프론트엔드입니다.
BE(FastAPI, `../BE`)가 제공하는 REST API를 소비하는 **SPA(Single Page Application)** 입니다.

## 기술 스택

- **React 19 + TypeScript + Vite** — 빠른 개발 서버/빌드, SPA에 적합 (SSR 불필요한 개인 지갑 서비스)
- **Tailwind CSS v4** — 유틸리티 기반 스타일링 (Figma 디자인→코드 플러그인과의 호환성이 좋음)
- **React Router** — 클라이언트 라우팅
- **TanStack Query (React Query)** — 서버 상태(캐싱/로딩/에러) 관리
- **Zustand (persist)** — 로그인한 유저 정보(DID 포함)와 액세스 토큰을 로컬에 보관하는 가벼운 전역 상태
- **React Hook Form + Zod** — 폼 상태 및 유효성 검증
- **Axios** — HTTP 클라이언트

## 폴더 구조

```
src/
├── api/            # BE 엔드포인트별 호출 함수 (auth.ts, credentials.ts, career.ts, portfolios.ts, shares.ts)
├── components/     # 공통 UI (Layout, ProtectedRoute, StatusBadge, VerificationChecks, dashboard/*, portfolio/*, ui/*)
├── lib/            # axios 인스턴스, 에러 파싱, PKCE·OAuth 상태 검증, 클립보드, 개발용 미리보기 데이터 등 공용 유틸
├── pages/          # 라우트 단위 페이지 (Login, Register, GoogleCallback, Dashboard, IssueCredential, CareerPlan, PortfolioBuilder, SharedView)
├── store/          # Zustand 전역 상태 (authStore)
└── types/          # BE 응답 타입 정의 (api.ts, career.ts)
```

## 로컬 실행 방법

BE 서버(`../BE`)가 먼저 `http://localhost:3000` 에서 실행되고 있어야 합니다.
BE 실행법은 `../BE/README.md` 참고. 아래 둘 중 **하나만** 씁니다 (둘 다 3000번 포트를 쓰며, 먼저 최상위 README의 "환경 변수 설정"대로 `BE/.env` 를 준비합니다).

- 전체 컨테이너(DB·BE·AI): `cd BE && docker compose up --build -d` — DB 마이그레이션은 `migrate` 서비스가 자동으로 적용하고, BE가 컨테이너에서 3000번으로 뜨므로 uvicorn을 따로 실행하지 않습니다.
- BE만 로컬: `cd BE && docker compose up -d postgres` 로 DB만 띄운 뒤 `python -m app.migrate` 로 마이그레이션을 적용하고 `python -m uvicorn main:app --reload --port 3000` 을 실행합니다. `/career` 는 AI 서비스도 필요합니다 (최상위 README의 "Docker 없이 실행").

```bash
cd FE
npm install
cp .env.example .env.local   # 선택. 필요 시 VITE_API_BASE_URL 수정 (.env.local 은 git에 올라가지 않음)
npm run dev
```

- 개발 서버: `http://localhost:5173`
- `/api` 로 시작하는 요청은 `vite.config.ts` 의 프록시 설정을 통해 BE(`http://localhost:3000`)로 전달됩니다.

## 운영 빌드 (배포)

- `VITE_API_BASE_URL` 은 빌드(`npm run build`) 때 번들에 들어가는 값이라 바꾸면 다시 빌드해야 합니다. 지정하지 않거나 비워 두면 `/api` 를 씁니다.
- 기본값 `/api` 는 FE와 BE를 같은 도메인에서 서비스하고 `/api/*` 요청을 BE로 넘기는 구성에서 씁니다 (예: CloudFront 동작 `/api/*` → BE, nginx `location /api/ { proxy_pass http://backend:3000; }`). 환경마다 다시 빌드할 필요가 없고 CORS 설정도 필요 없습니다.
- CloudFront `/api/*` 동작은 캐시하지 않고(`Managed-CachingDisabled`) `Authorization` 헤더를 BE로 넘겨야 합니다 (예: 원본 요청 정책 `Managed-AllViewerExceptHostHeader`). 기본 설정은 GET 요청의 `Authorization` 을 지우므로 BE가 401을 주고, FE는 이를 로그인 만료로 보고 바로 로그아웃합니다. 자세한 항목은 최상위 README "AWS 배포 체크리스트".
- nginx `proxy_pass` 주소 끝에 `/` 를 붙이면 `/api` 가 잘려 모든 요청이 404가 됩니다.
- BE가 다른 도메인이면 `/api` 까지 넣고(예: `https://api.example.com/api`) BE `CORS_ORIGINS` 에 FE 주소를 추가합니다.
- 운영은 HTTPS로 서비스합니다 (예: S3+CloudFront, ALB+ACM). HTTPS 페이지에서 `http://` API 호출은 브라우저가 막으므로(mixed content) BE 주소도 `https://` 여야 합니다.
- **다른 출처의 스크립트를 넣지 않습니다.** 다른 출처 코드는 이 사이트 권한으로 실행되어 localStorage의 로그인 토큰과 비밀번호 입력을 읽을 수 있습니다. Figma html-to-design 캡처 스크립트는 `vite.config.ts`의 개발 서버 전용 플러그인(`apply: 'serve'`)이 `npm run dev` 화면에만 넣고, `npm run build`·`npm run preview` 결과(`dist`)에는 들어가지 않습니다. CI FE 작업은 빌드 후 `dist`에 `mcp.figma.com`이나 외부 `<script src>`가 있으면 실패합니다.
- **웹폰트(Pretendard):** 지금은 `index.html`의 `<link>`로 jsDelivr CDN(`https://cdn.jsdelivr.net`)에서 받습니다. 운영 CSP의 `style-src`·`font-src`에 `https://cdn.jsdelivr.net`이 없으면 시스템 글꼴로 바뀝니다. 자체 호스팅은 아래 TODO 참고.
- **보안 헤더:** HTTP→HTTPS 리디렉션과 HSTS·CSP·`X-Frame-Options` 등은 정적 호스팅(CloudFront 응답 헤더 정책)이 붙이며, 값은 최상위 README "AWS 배포 체크리스트"에 있습니다. CSP는 `script-src 'self'`(eval 불허)로 둘 수 있게, `src/lib/zodConfig.ts`가 `main.tsx`의 첫 import로 zod의 eval 시험(`new Function`)을 끕니다(검증 동작·메시지는 같음). 스키마보다 먼저 실행되어야 하므로 이 import 순서를 바꾸지 않습니다.
- SPA라서 `/dashboard` 같은 경로를 새로고침하면 정적 호스팅이 `index.html` 을 돌려줘야 합니다. 이때 `/api/*` 의 404·403 응답까지 `index.html` 로 바꾸지 않게 합니다 (CloudFront 사용자 정의 오류 응답은 배포 전체에 적용되므로, 기본(S3) 동작에만 CloudFront Function 등으로 처리).

## 구현된 화면과 BE 엔드포인트 매핑

| 화면 | 설명 | 연동 BE 엔드포인트 |
| --- | --- | --- |
| `/register` | 회원가입 (이름·이메일·비밀번호, DID 자동 발급) | `POST /api/v1/users/register` |
| `/login` | 이메일 + 비밀번호 로그인 | `POST /api/v1/users/login` |
| `/dashboard` | 자격증명 지갑 (status별 구분 표시, 상세에서 **지금 다시 검증하기**, 관리형 지갑 표시) | `GET /api/v1/subjects/{did}/careers`, `POST /api/v1/credentials/{record_id}/reverify`, `GET /api/v1/users/me` |
| `/issue` | 모의 배지 발급 → 검증 → 저장 (2단계 플로우) | `POST /api/mock-issuer/issue`, `POST /api/v1/verify` |
| `/career` | 직무 비교·커리큘럼: 공고 선택 → 실행 시점 재검증 후 AI 비교 → 학습 계획 (로그인 토큰 필요, BE·AI 서버 필요) | `GET /api/v1/job-postings`, `GET /api/v1/skills`, `POST /api/v1/career-analyses`, `POST /api/v1/curricula` |
| `/portfolio` | AI 포트폴리오 빌더 — 대화(AI 연동 전 모의 응답) + **검증 후 저장(VC_ONLY)** + 저장 목록 + **선택 공개 링크** | `GET /api/v1/subjects/{did}/careers`, `/api/v1/portfolios`, `/api/v1/shares` |
| `/share/:token` | 기업용 조회 (로그인 불필요, 열 때마다 재검증, 서명 원본 제공) | `GET /api/v1/public/shares/{token}` |
| `/auth/google/callback` | Google OAuth 2.0 콜백 처리 (state·PKCE S256 검증 후 코드 교환) | `GET /api/v1/users/oauth/google/authorize`, `POST /api/v1/users/oauth/google/callback` |

API 계약은 `BE/openapi.yaml`(v0.7.0 + Google OAuth 2.0)을 기준으로 합니다.

- **로그인 토큰:** 로그인·가입·소셜 로그인 응답의 `access_token`을 저장하고 공개 API를 제외한 모든 요청에 `Authorization: Bearer` 헤더로 붙입니다 (`lib/apiClient.ts`). 공개 엔드포인트 이외의 요청에 BE가 401(토큰 만료·무효, 토큰 없는 이전 세션)을 반환하면 응답 인터셉터가 자동으로 로그아웃하고 로그인 화면에 "로그인 시간이 만료되었습니다" 배너를 띄웁니다. 응답의 `expires_in`으로 만료 시각을 저장해 두고, 그 시각이 지나면 요청이 없어도 같은 배너와 함께 로그아웃합니다(만료된 세션으로 앱을 열면 첫 화면 전에 로그아웃).
- **로그아웃:** 화면에서 로그아웃하면 BE `POST /api/v1/users/logout`(응답을 기다리지 않음. 이 요청이 실패하면 서버의 토큰은 만료 시각까지 유효)으로 이 계정의 토큰을 서버에서도 무효로 하고, 같은 브라우저의 다른 탭도 바로 로그아웃됩니다("로그아웃되었습니다" 배너). 다른 브라우저·기기의 같은 계정은 다음 요청에서 401을 받아 로그아웃됩니다. 한 탭에서 로그인하면, 다른 계정으로 로그인돼 있던 탭은 새 계정으로 새로고침되고 로그아웃돼 로그인·회원가입 화면(주소의 대소문자·끝의 `/`·%인코딩과 관계없이, 예: `/login/`·`/LOGIN`·`/%6Cogin`)에 있던 탭은 새 계정의 대시보드로 이동합니다(같은 계정으로 다시 로그인하면 다른 탭은 화면을 그대로 두고 새 토큰만 씁니다).
- **Google OAuth 2.0 소셜 로그인 (PKCE S256 + state 검증):** 로그인·회원가입 화면에서 "Google로 계속하기"를 누르면 PKCE `code_verifier`/`code_challenge(S256)`를 생성하고 `GET /api/v1/users/oauth/google/authorize`로 서명된 `state`와 인가 URL을 받아 `sessionStorage`에 저장한 뒤 Google 인증 화면으로 이동합니다. 콜백(`/auth/google/callback`)에서는 저장된 `state`와 일치하는지 먼저 검증(로그인 CSRF 방지)하고 주소창에서 `code`/`state` 쿼리를 즉시 지운 뒤 `POST /api/v1/users/oauth/google/callback`으로 코드를 교환합니다.
- **직무 비교 화면은 미리보기 세션에서 동작하지 않습니다.** 실제 자격증명 재검증과 AI 서버가 필요합니다.

- **협력사 자료 취급:** 템플릿 ID·API 주소 등 협력사에서 받은 값은 FE 코드에 넣지 않습니다. 발급 화면의 Template ID는 비워두면 BE 서버 설정의 기본 템플릿을 씁니다.
- **verify 응답 `data`:** BE 공통 형식(`achievement_name`, `issuer_name`, `issuer_trusted`, `status`, `record_id`, `checks` 등)입니다. 외부 검증기 응답 원문이 아닙니다.
- **실행 시점 재검증 (v0.6.0):** 지갑 상세의 "지금 다시 검증하기"는 저장된 서명 원본을 다시 검증하고 DB 상태도 갱신합니다.
  서명 원본이 없는 자격증명(시드 데이터 등)은 `unverifiable` 로 표시됩니다.
- **포트폴리오 (VC_ONLY, v0.6.0):** 자유 텍스트 본문은 보내지 않고 `title`·`target_role`·`record_ids`·`source: "VC_ONLY"` 만 보냅니다.
  BE가 저장 직전에 재검증해 통과한 것만 본문에 넣고 나머지는 `excluded`(사유 포함)로 돌려줍니다. 통과한 것이 없으면 422.
  저장 후에는 본문을 직접 수정할 수 없고, 상세 모달에서 제목·직무 수정과 게시/초안 전환만 됩니다(PATCH).
  제목·직무를 수정하면 BE가 본문 머리(`# 제목`, `- 희망 직무:` 줄)도 함께 갱신하며, 근거 섹션은 그대로 둡니다.
- **선택 공개 (v0.6.0):** 고른 자격증명만 보이는 링크를 만듭니다. 링크 주소(토큰)는 **생성 직후 한 번만** 보이고 서버에는 해시만 저장됩니다.
  기업은 `/share/{token}` 으로 로그인 없이 열며, 없는·철회·만료 링크는 모두 같은 404입니다. 공개 조회에는 로그인 토큰을 붙이지 않습니다.

- **인증 (v0.4.0)**: 회원가입/로그인 응답의 `access_token`(JWT)을 `store/authStore.ts` 에 저장하고,
  `lib/apiClient.ts` 요청 인터셉터가 모든 요청에 `Authorization: Bearer <token>` 을 붙입니다.
  토큰을 보냈는데 401(만료·위조)이면 응답 인터셉터가 자동 로그아웃 → 로그인 화면에 "로그인 정보가 만료되었습니다" 안내.
  토큰 없이 저장된 이전 로그인 정보(v0.3.0 시절)는 앱 시작 시 버려지므로 한 번 다시 로그인해야 합니다.
- **권한**: 로그인 사용자는 본인 DID만 조회·발급·검증할 수 있습니다(남의 DID는 403).
- **비밀번호**: 회원가입 시 8자 이상, UTF-8 기준 72바이트 이하(bcrypt 제약). 로그인 실패는 이메일/비밀번호 구분 없이 `401 Invalid email or password`.
- **시드 테스트 계정**: `user001@example.com` ~ `user045@example.com`, 비밀번호 `test1234`.
- **에러 형식**: `{ detail: string }`, 요청 검증 실패(422)는 `{ detail: [...] }` 배열 → `lib/apiClient.ts` 의 `getApiErrorMessage` 가 한국어 메시지로 변환.
  DXWorks 호출 실패는 502, DXWorks 설정 누락은 503, 그 외 내부 오류는 500 `Internal server error`(내부 메시지 미노출).
- **careers API**: 상태와 무관하게 모든 자격증명을 반환하므로 FE에서 `status === 'verified'` 로 거른다.
- **verify API**: 검증 실패도 HTTP 200 + `success: false` 로 응답한다. `data.status`·`data.record_id` 로
  폐기·만료 배지는 `success: false` 여도 해당 status로 지갑에 저장됐는지 알 수 있습니다(`data.record_id` 존재).

### 에러/성공 화면 대응표

에러 종류는 `lib/apiClient.ts` 의 `getApiErrorKind` 로 분류하고, 공통 `components/ui/Alert.tsx` 로 표시합니다.

| 화면 | 상황 | BE 응답 | FE 표시 |
| --- | --- | --- | --- |
| 로그인 | 비밀번호 오류 / 미가입 이메일 | 401 (BE가 둘을 구분하지 않음) | 빨간 알림 + 비밀번호 칸 비우고 포커스 + "이 이메일로 회원가입"(이메일 자동 입력) |
| 로그인 | 성공 | 200 | 대시보드 "○○ 님, 환영합니다" 배너 |
| 로그인 | 같은 이메일로 너무 많이 틀림 (또는 운영 WAF의 IP별 속도 제한) | 429 | "로그인 중 오류가 발생했습니다" + "시도가 너무 많습니다. 잠시 후 다시 시도해주세요." (응답 본문과 관계없이. 다른 오류에 붙는 "잠시 후 다시 시도" 안내는 덧붙이지 않음) |
| 로그인 | 로그아웃(같은 브라우저의 다른 탭에서 한 로그아웃 포함) / DID 만료 후 진입 | - | "로그아웃되었습니다" / "로그인 정보가 만료되었습니다" 배너 |
| 회원가입 | 이미 가입된 이메일 | 400 | 이메일 칸 에러 + "이 이메일로 로그인"(이메일 자동 입력) |
| 회원가입 | 가입 요청이 너무 많음 (운영 WAF의 IP별 속도 제한) | 429 | "회원가입 중 오류가 발생했습니다" + "시도가 너무 많습니다. 잠시 후 다시 시도해주세요." (응답 본문과 관계없이. 다른 오류에 붙는 "잠시 후 다시 시도" 안내는 덧붙이지 않음) |
| 회원가입 | 8자 미만 / 72바이트 초과 / 확인 불일치 | (FE 사전 검증, 서버는 422) | 각 입력칸 아래 메시지 |
| 회원가입 | 성공 | 200 | 바로 로그인 → 대시보드 "회원가입이 완료되었습니다" 배너 |
| 대시보드·포트폴리오 | 저장된 DID가 서버에 없음 (404를 주는 이전 BE. 지금 BE는 계정이 없어진 토큰을 401로 거절하므로 아래 행처럼 자동 로그아웃) | 404 | "계정 정보를 서버에서 찾을 수 없습니다" + 다시 로그인 |
| 대시보드·포트폴리오 | 저장된 DID ≠ 토큰의 DID | 403 | "로그인 정보가 일치하지 않습니다" + 다시 로그인 |
| 전 화면(로그인 필요) | 토큰 만료·무효·다른 기기에서 로그아웃·계정 없음(DB 초기화 등) / 토큰 없는 이전 세션 (만료 시각이 지나면 요청 없이도) | 401 | 자동 로그아웃 → 로그인 화면 "로그인 시간이 만료되었습니다" 배너 → 다시 로그인하면 대시보드 |
| 대시보드 | 자격증명 0개 | 200 | 빈 상태 + "첫 자격증명 발급받기" |
| 발급 | 허용되지 않은 템플릿 / 24시간 발급 한도 초과 / 모의 발급 꺼짐 | 403 / 429 / 404 | "배지 발급에 실패했습니다" + "허용되지 않은 배지 템플릿입니다" / "모의 발급 한도(24시간)를 넘었습니다…" / "이 서버에서는 모의 발급을 사용할 수 없습니다" |
| 발급 | 발급 요청이 너무 많음 (운영 WAF의 IP별 속도 제한, BE의 한도 문구가 없는 429) | 429 | "배지 발급에 실패했습니다" + "시도가 너무 많습니다. 잠시 후 다시 시도해주세요." ("잠시 후 다시 시도" 안내는 덧붙이지 않음) |
| 발급 | 템플릿 오류 / 발급 인프라 오류 · 설정 누락 | 502 / 503 | "배지 발급에 실패했습니다" + "배지 발급·검증 서비스에서 오류가 발생했습니다" / "…지금 사용할 수 없습니다" (원인은 BE 로그 `Provider error`, 개발 서버는 설정 확인 안내) |
| 발급 | 가입되지 않은 DID / 남의 DID / 다른 사용자에 이미 등록 | 404 / 403 / 409 | "배지를 발급(저장)할 수 없습니다" + 사유 |
| 발급 | 검증 실패 (저장 안 됨) | 200 + `success:false`, `data.record_id:null` | "배지 검증에 실패했습니다" + 처음부터 다시 발급 |
| 발급 | 폐기·만료 배지 (상태와 함께 저장됨) | 200 + `success:false`, `data.record_id` 있음 | 상태 배지 + "지갑에 기록되었습니다" + 지갑에서 확인하기 |
| 발급 | 성공 | 200 | 완료 단계(배지·발급기관 표시) → 대시보드 "지갑에 저장되었습니다" 배너 |
| 지갑 상세 | 재검증 통과 / 실패 / 서명 원본 없음 | 200 + `success:true/false` | "지금도 유효한 자격증명이에요" / 갱신된 상태 + 점검 항목 / "서명 원본이 없어 다시 검증할 수 없어요" |
| 포트폴리오 | 검증 후 저장 성공 (일부 제외) | 201 + `excluded` | 성공 배너 + 빠진 자격증명과 사유 + 서버가 만든 본문 |
| 포트폴리오 | 고른 것 중 통과한 것 없음 | 422 `No verified credentials selected` | "선택한 자격증명 중 지금 검증을 통과한 것이 없습니다" |
| 선택 공개 | 링크 생성 | 201 | 링크 주소(한 번만 표시) + 복사 버튼, 목록에 열람 수·만료일·철회 버튼 |
| 기업용 조회 | 없는·철회·만료 링크 | 404 | "열 수 없는 링크입니다" |
| 전 화면 | BE 서버 미실행 | 연결 실패 (vite 프록시 502) | "서버에 연결할 수 없습니다" (+ 조회 화면은 다시 시도) |

> 서버 주소(`localhost:3000`)·서버 설정 이름(`DXWORKS_*`, `AI_SERVICE_*`, `GOOGLE_*`, `ai_contract`)·"BE 서버 로그 확인" 같은 운영자용 안내는
> 개발 서버(`npm run dev`)에서만 보입니다(`import.meta.env.DEV` 분기). 운영 빌드(`npm run build`)에서는 번들에서 빠지고
> "잠시 후 다시 시도해주세요. 문제가 계속되면 관리자에게 문의해주세요."로 바뀌며, 번역되지 않은 5xx 본문은
> "서버에서 일시적인 오류가 발생했습니다."로 보여 줍니다. 번역표에 없는 429(운영 WAF·로드밸런서·API 게이트웨이가 보낸 HTML·
> `{"message": …}`·빈 본문 등)는 개발·운영 빌드 모두 "시도가 너무 많습니다. 잠시 후 다시 시도해주세요."로 보여 주고(`getApiErrorMessage`),
> BE가 보낸 알려진 429 문구(모의 발급 한도 등)는 그 번역을 그대로 씁니다. 새 안내 문구는 `lib/apiClient.ts`의 `getConnectionHint()`·`RETRY_LATER_MESSAGE`와
> `KNOWN_ERROR_MESSAGES`의 개발 전용 항목에 둡니다. CI FE 작업은 빌드 후 `dist`에 `localhost:3000`·`AI_SERVICE_TOKEN`·`AI_SERVICE_URL`·`DXWORKS_`·`ai_contract`가 있으면 실패합니다.
>
> 로그인·가입 응답의 사용자 정보(`did` 포함)와 `access_token`, 만료 시각(`expiresAt`)을 Zustand persist(localStorage 키 `vc-ai-career-agent-auth`)로
> 저장해 로그인 상태를 유지하고, 같은 브라우저의 탭들은 이 값을 함께 따릅니다(`storage` 이벤트). 토큰이 유효한지는 BE가 판단하고(로그아웃한 토큰은 401),
> FE는 만료 시각이 지난 세션을 지웁니다. 유효 시간은 BE `JWT_EXPIRES_MINUTES`(기본 1440분)이고,
> BE `JWT_SECRET` 을 비워 두면 BE가 재시작될 때마다(코드 수정 후 자동 재시작 포함) 기존 토큰이 모두 무효가 되어
> 다음 요청에서 로그아웃됩니다. 로컬에서도 `JWT_SECRET` 을 설정하세요.
>
> 이전 버전에서 만든 DB 볼륨을 계속 쓰면 새 컬럼·테이블이 필요합니다. `cd BE && docker compose up -d --build` 로 띄우면
> `migrate` 서비스가 아직 적용되지 않은 `DB/migrations/` 파일을 자동으로 적용하고, BE를 로컬에서 실행한다면
> `cd BE && python -m app.migrate` 를 실행합니다. 자세한 내용은 BE README 참고.

## 스크립트

| 명령 | 설명 |
| --- | --- |
| `npm run dev` | 개발 서버 실행 |
| `npm run build` | 타입체크(`tsc -b`) + 프로덕션 빌드 |
| `npm run lint` | oxlint 실행 |
| `npm run preview` | 빌드 결과 미리보기 |

## 다음 단계 (TODO)

- [x] BE v0.6.0 연동 — 실행 시점 재검증, 포트폴리오(VC_ONLY) 저장·목록·삭제, 선택 공개 링크·기업용 조회 화면
- [x] 포트폴리오 제목·직무 수정, 게시/초안 전환 (`PATCH /portfolios/{id}`, 저장 목록 → 상세 모달)
- [x] BE JWT 인증(v0.4.0) 및 토큰 만료·다중 탭 로그아웃 연동
- [x] Google OAuth 2.0 소셜 로그인 연동 (PKCE S256 + state 검증 + 계정 연동 상태 표시)
- [ ] Figma 디자인 확정 후 MCP/플러그인 기반 컴포넌트 교체
- [ ] Pretendard 웹폰트 자체 호스팅: 버전을 고정한 npm 패키지(`pretendard@1.3.9`)의 CSS를 `main.tsx`에서 import 하고 `index.html`의 CDN `<link>`를 지운 뒤, 운영 CSP에서 `https://cdn.jsdelivr.net`을 뺍니다 (`npm install` 필요)

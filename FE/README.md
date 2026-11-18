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
- **Motion · Anime.js · d3-shape** — 화면 전환·모달 애니메이션(Motion), 통계 카드의 숫자 카운트·막대 애니메이션(Anime.js), 지갑 상태 링 차트의 호 경로(d3-shape). 링 가운데 숫자는 애니메이션 없는 글자로 그립니다(인라인 `<style>`을 쓰는 컴포넌트는 운영 CSP `style-src`에 걸리므로 쓰지 않음)

## 폴더 구조

```
src/
├── api/            # BE 엔드포인트별 호출 함수 (auth.ts, credentials.ts, career.ts, market.ts, portfolios.ts, shares.ts)
├── components/     # 공통 UI (Layout, ProtectedRoute, RouteErrorBoundary, StatusBadge, VerificationChecks, MarketTrendsCard, SocialLoginButton, OAuthProviderBadges, charts/*, dashboard/*, portfolio/*, ui/*)
├── lib/            # axios 인스턴스, 에러 파싱, 소셜 로그인(oauth.ts: PKCE·state 검증, oauthProviders.ts: 제공자별 이름·콜백 경로·허용 인가 호스트), 클립보드, 개발용 미리보기 데이터 등 공용 유틸
├── pages/          # 라우트 단위 페이지 (Login, Register, OAuthCallback, Dashboard, IssueCredential, CareerPlan, PortfolioBuilder, SharedView)
├── store/          # Zustand 전역 상태 (authStore)
└── types/          # BE 응답 타입 정의 (api.ts, career.ts, market.ts)
```

## 로컬 실행 방법

BE 서버(`../BE`)가 먼저 `http://localhost:3000` 에서 실행되고 있어야 합니다.
BE 실행법은 `../BE/README.md` 참고. 아래 둘 중 **하나만** 씁니다 (둘 다 3000번 포트를 쓰며, 먼저 [서비스 실행 방법](../docs/GETTING_STARTED.md)의 "2. 환경 변수 설정"대로 `BE/.env` 를 준비합니다).

- 전체 컨테이너(DB·BE·AI): `cd BE && docker compose up --build -d` — DB 마이그레이션은 `migrate` 서비스가 자동으로 적용하고, BE가 컨테이너에서 3000번으로 뜨므로 uvicorn을 따로 실행하지 않습니다.
- BE만 로컬: `cd BE && docker compose up -d postgres` 로 DB만 띄운 뒤 `python -m app.migrate` 로 마이그레이션을 적용하고 `python -m uvicorn main:app --reload --port 3000` 을 실행합니다. `/career` 는 AI 서비스도 필요합니다 ([서비스 실행 방법](../docs/GETTING_STARTED.md)의 "6. Docker 없이 실행").

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
- 기본값 `/api` 는 FE와 BE를 같은 도메인에서 서비스하고 `/api/*` 요청을 BE로 넘기는 구성에서 씁니다. 운영 배포([../deploy/README.md](../deploy/README.md), NCP 서버 한 대의 Docker Compose)는 nginx 이미지([../deploy/nginx/Dockerfile](../deploy/nginx/Dockerfile))가 이 폴더를 `VITE_API_BASE_URL` 없이 빌드하고, `location ^~ /api/ { proxy_pass $vc_backend; }`(`http://backend:3000`)로 넘깁니다. 환경마다 다시 빌드할 필요가 없고 CORS 설정도 필요 없습니다.
- 프록시는 `/api/*` 응답을 캐시하지 않고 `Authorization` 헤더를 BE로 그대로 넘겨야 합니다. 헤더가 빠지면 BE가 401을 주고, FE는 이를 로그인 만료로 보고 바로 로그아웃합니다. 자세한 항목은 [배포 체크리스트](../deploy/CHECKLIST.md)의 "FE".
- nginx `proxy_pass` 주소 끝에 `/` 를 붙이면 `/api` 가 잘려 모든 요청이 404가 됩니다.
- BE가 다른 도메인이면 `/api` 까지 넣고(예: `https://api.example.com/api`) BE `CORS_ORIGINS` 에 FE 주소를 추가합니다.
- 운영은 HTTPS로 서비스합니다 (nginx + Let's Encrypt, [../deploy/README.md](../deploy/README.md) ⑦). HTTPS 페이지에서 `http://` API 호출은 브라우저가 막으므로(mixed content) BE 주소도 `https://` 여야 합니다.
- **다른 출처의 스크립트를 넣지 않습니다.** 다른 출처 코드는 이 사이트 권한으로 실행되어 localStorage의 로그인 토큰과 비밀번호 입력을 읽을 수 있습니다. Figma html-to-design 캡처 스크립트는 `vite.config.ts`의 개발 서버 전용 플러그인(`apply: 'serve'`)이 `FIGMA_CAPTURE=1 npm run dev`로 켰을 때만 개발 서버 화면에 넣고(기본은 꺼짐), `npm run build`·`npm run preview` 결과(`dist`)에는 들어가지 않습니다. CI FE 작업은 빌드 후 `dist`에 `mcp.figma.com`이나 외부 `<script src>`가 있으면 실패합니다.
- **웹폰트(Pretendard):** 지금은 jsDelivr CDN(`https://cdn.jsdelivr.net`)에서 받습니다. `<link>`는 `index.html`이 아니라 `main.tsx`가 스크립트로 붙여, CDN이 느리거나 막혀도 첫 화면이 기본 글꼴로 바로 보입니다. 운영 CSP의 `style-src`·`font-src`에 `https://cdn.jsdelivr.net`이 없으면 시스템 글꼴로 바뀝니다. 자체 호스팅은 아래 TODO 참고.
- **보안 헤더:** HTTP→HTTPS 리디렉션과 HSTS·CSP·`X-Frame-Options` 등은 정적 호스팅(nginx, [../deploy/nginx/vc/security-headers.conf](../deploy/nginx/vc/security-headers.conf))이 붙이며, 값은 [배포 체크리스트](../deploy/CHECKLIST.md)의 "FE"에 있습니다. CSP는 `script-src 'self'`(eval 불허)로 둘 수 있게, `src/lib/zodConfig.ts`가 `main.tsx`의 첫 import로 zod의 eval 시험(`new Function`)을 끕니다(검증 동작·메시지는 같음). 스키마보다 먼저 실행되어야 하므로 이 import 순서를 바꾸지 않습니다.
- SPA라서 `/dashboard` 같은 경로를 새로고침하면 정적 호스팅이 `index.html` 을 돌려줘야 합니다. 이때 `/api/*` 의 404·403 응답까지 `index.html` 로 바꾸지 않게 합니다(오류 페이지를 서버 전체에 거는 방식은 BE의 JSON 오류까지 바꿈). 운영 nginx([../deploy/nginx/vc/app.conf](../deploy/nginx/vc/app.conf))는 `/api/`를 BE로 넘기고, 마지막 경로 조각에 점이 없는 경로(`/dashboard`, `/auth/<제공자>/callback`, `/share/<token>`)만 `index.html`(200)로, 점이 있는데 없는 파일은 404로 답합니다. `/dashboard` → `/dashboard/index.html`처럼 바꾸는 방식은 이 앱에서 404가 됩니다.

## 구현된 화면과 BE 엔드포인트 매핑

| 화면 | 설명 | 연동 BE 엔드포인트 |
| --- | --- | --- |
| `/register` | 회원가입 (이름·이메일·비밀번호, DID 자동 발급) | `POST /api/v1/users/register` |
| `/login` | 이메일 + 비밀번호 로그인 | `POST /api/v1/users/login` |
| `/dashboard` | 자격증명 지갑 (status별 구분 표시, 상세에서 **지금 다시 검증하기**, 관리형 지갑 표시) | `GET /api/v1/subjects/{did}/careers`, `POST /api/v1/credentials/{record_id}/reverify`, `GET /api/v1/users/me` |
| `/issue` | 모의 배지 발급 → 검증 → 저장 (2단계 플로우) | `POST /api/mock-issuer/issue`, `POST /api/v1/verify` |
| `/career` | 직무 비교·커리큘럼: 공고 선택 → 실행 시점 재검증 후 AI 비교 → 학습 계획 (로그인 토큰 필요, BE·AI 서버 필요). 위쪽 "직무별 요구 역량 통계" 카드는 BE DB에 통계가 있을 때만 보임 | `GET /api/v1/job-postings`, `GET /api/v1/skills`, `POST /api/v1/career-analyses`, `POST /api/v1/curricula`, `GET /api/v1/market/snapshots`, `GET /api/v1/market/snapshots/{role_id}` |
| `/portfolio` | AI 포트폴리오 빌더 — 대화(AI 연동 전 모의 응답) + **검증 후 저장(VC_ONLY)** + 저장 목록 + **선택 공개 링크** | `GET /api/v1/subjects/{did}/careers`, `/api/v1/portfolios`, `/api/v1/shares` |
| `/share/:token` | 기업용 조회 (로그인 불필요, 열 때마다 재검증, 서명 원본 제공) | `GET /api/v1/public/shares/{token}` |
| `/auth/google/callback`, `/auth/github/callback`, `/auth/naver/callback` | 소셜 로그인(Google·GitHub·네이버 OAuth 2.0) 콜백 처리 (state·PKCE S256 검증 후 코드 교환. 세 경로 모두 `pages/OAuthCallbackPage.tsx`. 목록에 없는 `/auth/<다른 이름>/callback`은 다른 모르는 경로처럼 `/dashboard`(로그인 전이면 `/login`)로 보내고 BE를 부르지 않음) | `GET /api/v1/users/oauth/{provider}/authorize`, `POST /api/v1/users/oauth/{provider}/callback` (`provider` = `google`·`github`·`naver`) |

API 계약은 `BE/openapi.yaml`(v0.8.0)을 기준으로 합니다.

- **로그인 토큰:** 로그인·가입·소셜 로그인 응답의 `access_token`을 저장하고 공개 API를 제외한 모든 요청에 `Authorization: Bearer` 헤더로 붙입니다 (`lib/apiClient.ts`). 공개 엔드포인트 이외의 요청에 BE가 401(토큰 만료·무효, 토큰 없는 이전 세션)을 반환하면 응답 인터셉터가 자동으로 로그아웃하고 로그인 화면에 "로그인 시간이 만료되었습니다" 배너를 띄웁니다. 응답의 `expires_in`으로 만료 시각을 저장해 두고, 그 시각이 지나면 요청이 없어도 같은 배너와 함께 로그아웃합니다(만료된 세션으로 앱을 열면 첫 화면 전에 로그아웃).
- **로그아웃:** 화면에서 로그아웃하면 BE `POST /api/v1/users/logout`(응답을 기다리지 않음. 이 요청이 실패하면 서버의 토큰은 만료 시각까지 유효)으로 이 계정의 토큰을 서버에서도 무효로 하고, 같은 브라우저의 다른 탭도 바로 로그아웃됩니다("로그아웃되었습니다" 배너). 다른 브라우저·기기의 같은 계정은 다음 요청에서 401을 받아 로그아웃됩니다. 한 탭에서 로그인하면, 다른 계정으로 로그인돼 있던 탭은 새 계정으로 새로고침되고 로그아웃돼 로그인·회원가입 화면(주소의 대소문자·끝의 `/`·%인코딩과 관계없이, 예: `/login/`·`/LOGIN`·`/%6Cogin`)에 있던 탭은 새 계정의 대시보드로 이동합니다(같은 계정으로 다시 로그인하면 다른 탭은 화면을 그대로 두고 새 토큰만 씁니다). 로그인한 사용자가 바뀌면(로그아웃·만료·다른 계정 로그인) 그 탭의 조회 캐시(TanStack Query)를 모두 비워, 같은 탭에서 이어서 로그인한 사람에게 이전 사용자의 포트폴리오·공유 링크가 보이지 않게 합니다(`main.tsx`).
- **소셜 로그인 (Google·GitHub·네이버 OAuth 2.0, PKCE S256 + state 검증):** 로그인·회원가입 화면의 "또는" 구분선 아래에 버튼 세 개가 Google, GitHub, 네이버 순으로 있습니다(`components/SocialLoginButton.tsx`의 `SocialLoginButtons`. 세 버튼 모두 높이 56px(`h-14`)·모서리 16px(`rounded-2xl`)). 아래 표의 심볼은 모두 인라인 SVG이고 외부 이미지·폰트를 쓰지 않습니다. 한 버튼이 시작하는 동안 그 버튼은 "처리 중...", 나머지는 잠깁니다.

  | 제공자 | 버튼 문구 | 모양 | 콜백 경로 | 인가 주소 호스트 |
  |---|---|---|---|---|
  | Google | "Google로 계속하기" (회원가입 화면은 "Google로 가입·시작하기") | 예전 버튼 그대로 (흰 배경, 테두리, 컬러 G 로고) | `/auth/google/callback` | `accounts.google.com` |
  | GitHub | "GitHub로 로그인" | 배경 `#24292F`(hover `#32383F`), 흰 글자, 흰색 GitHub 마크(Primer Octicons `mark-github` 16px path, 20px로 그림), 키보드 포커스 때 2px 띄운 포인트 색 2px 테두리 | `/auth/github/callback` | `github.com` |
  | 네이버 | "네이버 로그인" | 배경 `#03A94D`, 흰색 N 로고·글자 | `/auth/naver/callback` | `nid.naver.com` |

  버튼을 누르면 PKCE `code_verifier`/`code_challenge(S256)`를 생성하고 `GET /api/v1/users/oauth/{provider}/authorize`로 서명된 `state`와 인가 URL을 받아 `sessionStorage`에 제공자·`state`·`code_verifier`를 저장한 뒤 제공자 인증 화면으로 이동합니다(`lib/oauth.ts`의 `startOAuth(provider)`). 인가 URL은 `https://`이고 호스트가 그 제공자의 것(위 표, `lib/oauthProviders.ts`. GitHub은 `github.com`만이고 `api.github.com`·`github.com.<다른 도메인>`은 거절)일 때만 따라갑니다(다른 제공자의 호스트도 거절, "허용되지 않은 소셜 로그인 이동 주소입니다."). PKCE 값은 세 제공자 모두에 보내고, 네이버는 BE가 제공자에게 싣지 않습니다. 콜백(`/auth/{provider}/callback`)에서는 저장된 제공자·`state`와 일치하는지 먼저 검증(로그인 CSRF 방지)하고 주소창에서 `code`/`state` 쿼리를 즉시 지운 뒤 `POST /api/v1/users/oauth/{provider}/callback`으로 코드를 교환합니다(`finishOAuthCallback(provider, …)`, StrictMode에서도 요청은 한 번). FE가 BE에 보내는 콜백 주소는 브라우저의 출처 + `/auth/{provider}/callback`이므로, BE `GOOGLE_REDIRECT_URI`·`GITHUB_REDIRECT_URI`·`NAVER_REDIRECT_URI`(운영은 `https://<FE 도메인>/auth/{provider}/callback`)와 각 제공자 콘솔(GitHub은 OAuth App의 Authorization callback URL)에 등록한 주소가 이 주소와 같아야 합니다(다르면 400 `Invalid redirect_uri` → "허용되지 않은 로그인 경로입니다…"). 같은 이메일로 이미 이메일·비밀번호 가입을 한 사용자는 소셜 로그인으로 로그인할 수 없고(409) 이메일과 비밀번호로 로그인합니다. BE는 Google·GitHub이 인증한 이메일(GitHub은 인증된 기본 이메일)로만 같은 이메일의 기존 계정에 연동하므로, 네이버 로그인이나 네이버로 만든 계정에는 다른 방법이 붙지 않습니다(409, 아래 표). 로그인 뒤 프로필 메뉴와 대시보드 프로필 카드에는 `GET /api/v1/users/me`의 `oauth_providers`마다 배지("Google 연동됨"·"GitHub 연동됨"·"네이버 연동됨", 이 순서. `components/OAuthProviderBadges.tsx`. 모르는 제공자 이름은 표시하지 않음)가 보이고, 기존 계정에 연동된 로그인이면 대시보드에 "기존 계정에 <제공자> 계정이 연동되어 로그인되었습니다." 배너가 나옵니다.
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
- **에러 형식**: `{ detail: string }`, 요청 검증 실패(422)는 `{ detail: [...] }` 배열(항목마다 `type`·`loc`·`msg`만 있고 보낸 값은 없음, 20개까지) → `lib/apiClient.ts` 의 `getApiErrorMessage` 가 한국어 메시지로 변환. 기계용 `code` 가 함께 오는 응답 중 `AI_SERVICE_BUSY` 는 `code` 로 번역합니다(`KNOWN_ERROR_CODES`).
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
| 로그인 | 같은 이메일로 너무 많이 틀림 (또는 운영 nginx의 IP별 요청 제한) | 429 | "로그인 시도가 너무 많습니다." + "보안을 위해 잠시 동안 로그인이 제한되었습니다. 잠시 후 다시 시도해주세요." (노란 알림. 응답 본문과 관계없이 같은 문구) |
| 로그인 | 로그아웃(같은 브라우저의 다른 탭에서 한 로그아웃 포함) / DID 만료 후 진입 | - | "로그아웃되었습니다" / "로그인 정보가 만료되었습니다" 배너 |
| 회원가입 | 이미 가입된 이메일 | 400 | 이메일 칸 에러 + "이 이메일로 로그인"(이메일 자동 입력) |
| 회원가입 | 가입 요청이 너무 많음 (운영 nginx의 IP별 요청 제한) | 429 | "회원가입 시도가 너무 많습니다." + "보안을 위해 잠시 동안 요청이 제한되었습니다. 잠시 후 다시 시도해주세요." (노란 알림. 응답 본문과 관계없이 같은 문구) |
| 회원가입 | 8자 미만 / 72바이트 초과 / 확인 불일치 | (FE 사전 검증, 서버는 422) | 각 입력칸 아래 메시지 |
| 회원가입 | 성공 | 200 | 바로 로그인 → 대시보드 "회원가입이 완료되었습니다" 배너 |
| 대시보드·포트폴리오 | 저장된 DID가 서버에 없음 (404를 주는 이전 BE. 지금 BE는 계정이 없어진 토큰을 401로 거절하므로 아래 행처럼 자동 로그아웃) | 404 | "계정 정보를 서버에서 찾을 수 없습니다" + 다시 로그인 |
| 대시보드·포트폴리오 | 저장된 DID ≠ 토큰의 DID | 403 | "로그인 정보가 일치하지 않습니다" + 다시 로그인 |
| 전 화면(로그인 필요) | 토큰 만료·무효·다른 기기에서 로그아웃·계정 없음(DB 초기화 등) / 토큰 없는 이전 세션 (만료 시각이 지나면 요청 없이도) | 401 | 자동 로그아웃 → 로그인 화면 "로그인 시간이 만료되었습니다" 배너 → 다시 로그인하면 대시보드 |
| 대시보드 | 자격증명 0개 | 200 | 빈 상태 + "첫 자격증명 발급받기" |
| 발급 | 허용되지 않은 템플릿 / 사용자별 24시간 발급 한도 초과 / 서버 전체 24시간 발급 한도 도달 (또는 실패한 것 없이 한도만큼의 발급 요청이 한꺼번에 진행 중) / 모의 발급 꺼짐 | 403 / 429 `Mock issuance limit reached; …` / 429 `Mock issuance service limit reached; …` / 404 | "배지 발급에 실패했습니다" + "허용되지 않은 배지 템플릿입니다" / "모의 발급 한도(24시간)를 넘었습니다…" / "전체 모의 발급 한도(24시간)에 도달했습니다. 잠시 후 다시 시도하거나 관리자에게 문의해주세요." / "이 서버에서는 모의 발급을 사용할 수 없습니다" |
| 발급 | 발급 서버(협력사) 오류가 이어져 BE가 모의 발급을 잠시 멈춤 (BE가 1분에 한 건씩 보내 보고, 성공하면 바로 풀림) | 429 `Mock issuance is paused after repeated failures. …` + `Retry-After` | "배지 발급에 실패했습니다" + "발급 서버 오류가 이어져 모의 발급을 잠시 멈췄습니다. 1분쯤 뒤에 다시 시도해주세요." (`Retry-After`의 초는 화면에 보여 주지 않음) |
| 발급 | 발급 요청이 너무 많음 (운영 nginx의 IP별 요청 제한 `Too many attempts. Try again later.`, 또는 BE의 한도 문구가 없는 429) | 429 | "배지 발급에 실패했습니다" + "시도가 너무 많습니다. 잠시 후 다시 시도해주세요." ("잠시 후 다시 시도" 안내는 덧붙이지 않음) |
| 발급 | 템플릿 오류 / 발급 인프라 오류 · 설정 누락 | 502 / 503 | "배지 발급에 실패했습니다" + "배지 발급·검증 서비스에서 오류가 발생했습니다" / "…지금 사용할 수 없습니다" (원인은 BE 로그 `Provider error`, 개발 서버는 설정 확인 안내) |
| 발급 | 가입되지 않은 DID / 남의 DID / 다른 사용자에 이미 등록 | 404 / 403 / 409 | "배지를 발급(저장)할 수 없습니다" + 사유 |
| 발급 | 검증 실패 (저장 안 됨) | 200 + `success:false`, `data.record_id:null` | "배지 검증에 실패했습니다" + 처음부터 다시 발급 |
| 발급 | 폐기·만료 배지 (상태와 함께 저장됨) | 200 + `success:false`, `data.record_id` 있음 | 상태 배지 + "지갑에 기록되었습니다" + 지갑에서 확인하기 |
| 발급 | 성공 | 200 | 완료 단계(배지·발급기관 표시) → 대시보드 "지갑에 저장되었습니다" 배너 |
| 지갑 상세 | 재검증 통과 / 실패 / 서명 원본 없음 | 200 + `success:true/false` | "지금도 유효한 자격증명이에요" / 갱신된 상태 + 점검 항목 / "서명 원본이 없어 다시 검증할 수 없어요" |
| 지갑 상세 | 검증 서버가 이 자격증명만 확인하지 못함 (저장된 상태는 그대로. 검증 서버 장애는 502/503) | 200 + `success:false`, `message: Re-verification failed: verifier_error` | "재검증을 통과하지 못했어요" + "검증 서버가 이 자격증명을 확인하지 못했어요. 지갑에 저장된 상태는 바뀌지 않았습니다." (상태 배지는 그대로) |
| 지갑 상세 | 200이지만 재검증 응답이 아닌 본문 (`/api/*` 가 `index.html` 로 넘어가는 배포 설정, 빈 객체 등. `data.status`·`data.verified` 를 확인) | 200 | "재검증 요청에 실패했습니다." + "서버 응답이 올바르지 않습니다." (일괄 재검증과 같은 검사. 점검 항목 `checks` 가 없는 응답은 결과만 보여 줌) |
| 포트폴리오 | 검증 후 저장 성공 (일부 제외) | 201 + `excluded` | 성공 배너 + 빠진 자격증명과 사유 + 서버가 만든 본문 |
| 포트폴리오 | 고른 것 중 통과한 것 없음 | 422 `No verified credentials selected` | "선택한 자격증명 중 지금 검증을 통과한 것이 없습니다" |
| 선택 공개 | 링크 생성 | 201 | 링크 주소(한 번만 표시) + 복사 버튼, 목록에 열람 수·만료일·철회 버튼 |
| 기업용 조회 | 없는·철회·만료 링크 | 404 | "열 수 없는 링크입니다" |
| 전 화면 | BE 서버 미실행 | 연결 실패 (vite 프록시 502) | "서버에 연결할 수 없습니다" (+ 조회 화면은 다시 시도) |
| 로그인·회원가입·소셜 로그인 콜백 | 200이지만 인증 응답이 아닌 본문 (`/api/*` 가 `index.html` 로 넘어가는 배포 설정, 이름·DID·토큰이 없는 응답 등) | 200 | "서버 응답이 올바르지 않습니다." (세션을 저장하지 않음) |
| 전 화면 | 저장된 세션이 로그인 상태인데 프로필이 올바르지 않음 (`id`·`did`·`name` 이 빈 문자열이 아닌 문자열, `email` 이 문자열이어야 함) | - | 저장된 세션을 쓰지 않고 로그인 화면으로 (빈 화면 대신. `store/authStore.ts` 의 `isUserProfile`) |
| 소셜 로그인 콜백 | 같은 이메일이 이미 이메일·비밀번호 계정으로 가입돼 있음 (BE는 그 계정에 소셜 계정을 붙이지 않음) | 409 `This email is registered with a password. …` | "이 이메일은 <제공자>로 로그인할 수 없습니다." + "이미 이메일·비밀번호로 가입된 계정입니다. 이메일과 비밀번호로 로그인해주세요." |
| 소셜 로그인 콜백 | 같은 이메일이 이미 다른 방법으로 가입돼 있는데 이메일이 양쪽에서 확인되지 않음 (네이버 로그인, 또는 네이버로 만든 계정) | 409 `This email is already used by another sign-in method. …` | "이 이메일은 <제공자>로 로그인할 수 없습니다." + "이미 다른 방법(이메일 또는 다른 소셜 로그인)으로 가입한 이메일입니다. 처음 가입한 방법으로 로그인해주세요." |
| 소셜 로그인 콜백 | 이 이메일 계정에 같은 제공자의 다른 계정이 이미 연동돼 있음 | 409 `User is already linked to another account for this provider` | "다른 <제공자> 계정이 이미 연동되어 있습니다." + "이 이메일 계정에는 이미 다른 <제공자> 계정이 연동되어 있습니다. 연동된 <제공자> 계정으로 로그인해주세요." |
| 소셜 로그인 콜백 | 제공자가 이메일을 주지 않음 (이메일 제공 동의 거절, 네이버 제공 정보에 이메일 없음 등) / 제공자가 이메일을 인증하지 않음 (Google) | 400 `OAuth email is required` / 400 `OAuth email is not verified` | "이메일 제공 동의가 필요합니다." + "<제공자> 계정의 이메일 제공에 동의해야 로그인할 수 있어요. 동의 항목을 확인한 뒤 다시 시도해주세요." / "<제공자> 로그인을 완료할 수 없습니다." + "<제공자> 계정의 이메일이 인증되지 않아 로그인할 수 없습니다." |
| 소셜 로그인 콜백 (GitHub) | 승인 화면에서 이메일 권한(`user:email`)을 뺐거나 이메일 목록에 기본(primary) 이메일이 없음 / 기본 이메일이 인증되지 않음 (토큰 단계의 `unverified_user_email` 포함) | 400 `OAuth email is required` / 400 `OAuth email is not verified` | "이메일 제공 동의가 필요합니다." + "GitHub 계정의 이메일을 확인할 수 없습니다. 이메일 권한(user:email)을 허용했는지 확인한 뒤 다시 시도해주세요." / "GitHub 로그인을 완료할 수 없습니다." + "GitHub 계정의 기본 이메일이 인증되지 않았습니다. GitHub에서 이메일 인증을 마친 뒤 다시 시도해주세요." (`OAuth email is not verified`는 Google과 같이 뒤에 다시 시도 안내 한 줄이 붙음) |
| 소셜 로그인 콜백 | 사용자가 제공자 화면에서 취소 / 제공자가 그 밖의 `error`로 돌려보냄 (GitHub `redirect_uri_mismatch`·`application_suspended` 등) / 다른 탭·제공자의 `state` / 제공자 쪽 응답 실패 | `?error=access_denied` / `?error=<그 밖>` / (BE 호출 없음) / 502 `Google OAuth error`·`GitHub OAuth error`·`Naver OAuth error` | "<제공자> 로그인이 취소되었습니다." / 같은 제목 + "<제공자> 인증을 완료하지 못했습니다. 다시 시도해주세요." / "<제공자> 로그인을 완료할 수 없습니다." + 사유 / "<제공자> 로그인을 완료할 수 없습니다" + "<제공자> 로그인 서비스에 일시적인 문제가 있습니다." (모든 실패 화면에 "<제공자>로 다시 시도" 버튼) |
| 로그인·회원가입 (소셜 로그인 버튼) | BE에 그 제공자 설정 없음 / BE가 허용하지 않은 콜백 주소 / 허용하지 않은 인가 주소 | 503 `Google OAuth is not configured`·`GitHub OAuth is not configured`·`Naver OAuth is not configured`(또는 `<Label> OAuth redirect_uri is not configured`) / 400 `Invalid redirect_uri` / (BE 응답의 `authorization_url`이 그 제공자의 `https://` 호스트가 아님) | "<제공자> 로그인을 시작할 수 없습니다." + "<제공자> 로그인을 지금 사용할 수 없습니다." / "허용되지 않은 로그인 경로입니다. 관리자에게 문의해주세요." / "허용되지 않은 소셜 로그인 이동 주소입니다." (개발 서버는 `GOOGLE_*`·`GITHUB_*`·`NAVER_*` 설정 안내) |
| 소셜 로그인 버튼 · 콜백 | 그 제공자의 토큰 교환 한도(BE 프로세스마다 제공자별 분당 300번) 또는 운영 nginx의 IP별 요청 제한 | 429 | "요청이 너무 많습니다." / "로그인 시도가 너무 많습니다." + "보안을 위해 잠시 동안 요청이 제한되었습니다. 잠시 후 다시 시도해주세요." |
| 전 화면 | 서버 혼잡: DB 연결 한도 / 검증 호출 한도 / AI 동시 호출 한도 | 503 `Service is busy. …` / 429 `Too many verification requests. …` / 503 `code: AI_SERVICE_BUSY` | "요청이 많아 처리가 지연되고 있습니다. …" / "검증 요청이 너무 많습니다. …" / "분석 요청이 많습니다. …" (모두 "잠시 후 다시 시도해주세요."로 끝나며 같은 안내를 덧붙이지 않음) |
| 직무 비교 (공고 목록·비교·학습 계획) | 사용자별 한도: AI 묶음 요청(동시 2개·분당 30번) / 24시간 직무 비교·학습 계획 각 200건 / LLM 학습 계획 `CURRICULUM_LLM_DAILY_LIMIT`건(모든 계정 합계 `CURRICULUM_LLM_GLOBAL_DAILY_LIMIT`건) / 계정당 동시 LLM 계획 1건 / AI의 동시 LLM 생성 한도 | 429 `Too many analysis requests. …` / 429 `Daily career analysis limit reached. …`·`Daily curriculum limit reached. …` / 429 `Daily LLM curriculum limit reached. …`·`Daily LLM curriculum service limit reached. …` / 429 `An LLM curriculum is already being generated …` / 503 `code: LLM_BUSY` | "분석 요청이 너무 많습니다. 잠시 후 다시 시도해주세요." / "오늘 만들 수 있는 직무 비교(학습 계획) 수를 모두 썼습니다. 내일 다시 시도해주세요." / "오늘 만들 수 있는 LLM 학습 계획 수를 모두 썼습니다. 템플릿으로 만들거나 내일 다시 시도해주세요."·"오늘 서비스 전체의 LLM 학습 계획 한도에 도달했습니다. …" / "이 계정의 LLM 학습 계획을 이미 만들고 있습니다. 끝난 뒤 다시 시도해주세요." / "LLM 학습 계획 요청이 많습니다. 잠시 후 다시 시도해주세요." |
| 학습 계획 (LLM 모드) | Claude API 연결 실패·시간 초과 / Claude API 429·5xx / LLM 설정 없음 / 배포 중 설정 변경 | 503 `code: LLM_PROVIDER_UNAVAILABLE` / 503 `code: LLM_PROVIDER_REJECTED` / 503 `code: GENERATOR_CONFIGURATION_REQUIRED` / 503 `code: GENERATOR_CONFIGURATION_CHANGED` | "LLM 서비스에 연결하지 못했습니다. 템플릿 모드를 쓰거나 잠시 후 다시 시도해주세요." / "LLM 서비스가 지금 요청을 받지 못했습니다. 템플릿 모드를 쓰거나 잠시 후 다시 시도해주세요." (422면 "LLM이 계획을 만들지 못했습니다. 템플릿 모드를 사용해주세요.") / "지금은 LLM 모드를 사용할 수 없습니다. 템플릿 모드를 사용해주세요." / "LLM 설정이 바뀌는 중입니다. 잠시 후 다시 시도해주세요." |
| 지갑 (일괄 재검증) | 한 항목이 연결 실패·5xx·429로 실패 | 응답 없음 / 5xx / 429 | 그 항목은 "오류", 남은 항목은 보내지 않고 "미실행"으로 표시 + 제목 "자격증명 일괄 재검증 중단" + "재검증을 중단했습니다. 나머지 N건은 실행하지 않았어요." (마지막·하나뿐인 항목에서 실패하면 남은 항목 없이 "재검증을 중단했습니다."만. 404 등 그 항목만의 오류는 계속 진행) |
| 지갑 (일괄 재검증) | 검증 서버가 한 자격증명만 확인하지 못함 / 200이지만 재검증 응답이 아닌 본문 | 200 + `success:false`, `message: Re-verification failed: verifier_error` / 200 | 그 항목은 "실패" + "검증 서버가 확인하지 못함 (지갑 상태는 그대로)" / "오류" + "서버 응답이 올바르지 않습니다." 둘 다 다음 항목을 계속 진행. "N건의 자격증명이 '확인 필요' 탭으로 이동했습니다." 안내는 검증 결과로 상태가 정해진 실패만 셈 (요청 오류·검증 서버가 확인하지 못한 항목은 세지 않음) |
| 포트폴리오·선택 공개 | 사용자별 개수 한도 | 409 `Portfolio limit reached.` / `Share link limit reached. …` | "포트폴리오를 더 만들 수 없습니다." / "공유 링크를 더 만들 수 없습니다. 사용하지 않는 링크를 먼저 폐기해주세요." |
| 조회 화면 전체 (지갑·포트폴리오·공유 링크·직무 비교·통계·기업용 조회) | BE 응답 없음 (연결 실패) / BE가 답한 오류(혼잡 503·429·404, 본문 없는 5xx 등) | - / 4xx·5xx | 1초 뒤 한 번 자동으로 다시 시도 / 자동 재시도 없이 바로 안내 + 오류 안내의 "다시 시도" 버튼(그 조회만 다시 부름. 그 조회를 다시 부르는 동안에는 버튼이 잠겨 여러 번 눌러도 요청은 한 번). 오류를 화면에 보여 주는 조회에는 모두 이 버튼이 있음: 자격증명 목록(지갑·포트폴리오 만들기), 포트폴리오 목록·상세, 공유 링크 목록, 공고 목록, 직무별 통계 목록·상세, 기업용 조회. 버튼 대신 다른 안내가 나오는 것은 자격증명 목록 403·404("다시 로그인")와 기업용 조회 404("열 수 없는 링크입니다")뿐이고, 다시 불러도 같은 오류(직무별 통계 404·409, 포트폴리오 상세 404 등)에도 버튼이 나옴. 401은 자동 로그아웃되어 로그인 화면으로 감. 오류를 보여 주지 않는 조회(기술 이름 `GET /skills`, 프로필 `GET /users/me`)는 그 화면·메뉴를 다시 열 때 다시 부름. `main.tsx` 의 조회 기본값이고, `retry: false` 로 둔 조회(`GET /users/me`)는 자동으로 다시 시도하지 않음 |
| 전 화면 | 화면을 그리는 중 예외 (FE가 모르는 응답 값 등) | - | 그 화면 자리에 "화면을 표시하는 중 오류가 발생했습니다" + 새로고침 버튼 (머리글·메뉴·로그아웃은 유지, 다른 화면으로 옮기면 풀림. `components/RouteErrorBoundary.tsx`) |

> 서버 주소(`localhost:3000`)·서버 설정 이름(`DXWORKS_*`, `AI_SERVICE_*`, `GOOGLE_*`, `GITHUB_*`, `NAVER_*`, `ai_contract`)·"BE 서버 로그 확인" 같은 운영자용 안내는
> 개발 서버(`npm run dev`)에서만 보입니다(`import.meta.env.DEV` 분기). 운영 빌드(`npm run build`)에서는 번들에서 빠지고
> "잠시 후 다시 시도해주세요. 문제가 계속되면 관리자에게 문의해주세요."로 바뀌며, 번역되지 않은 5xx 본문은
> "서버에서 일시적인 오류가 발생했습니다."로 보여 줍니다. 번역표에 없는 429(프록시·로드밸런서·API 게이트웨이가 보낸 HTML·
> `{"message": …}`·빈 본문 등)는 개발·운영 빌드 모두 "시도가 너무 많습니다. 잠시 후 다시 시도해주세요."로 보여 주고(`getApiErrorMessage`),
> BE가 보낸 알려진 429 문구(모의 발급 한도 등)는 그 번역을 그대로 씁니다. 새 안내 문구는 `lib/apiClient.ts`의 `getConnectionHint()`·`RETRY_LATER_MESSAGE`와
> `KNOWN_ERROR_MESSAGES`의 개발 전용 항목에 둡니다. CI FE 작업은 빌드 후 `dist`에 `localhost:3000`·`AI_SERVICE_TOKEN`·`AI_SERVICE_URL`·`DXWORKS_`·`GOOGLE_`·`GITHUB_`·`NAVER_`·`ai_contract`가 있으면 실패합니다.
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
- [x] GitHub·네이버 소셜 로그인 (Google 버튼을 바탕으로 한 공통 버튼 컨테이너, 제공자별 콜백 화면·연동 배지)
- [ ] Figma 디자인 확정 후 MCP/플러그인 기반 컴포넌트 교체
- [ ] 자동 테스트 도입 (지금은 테스트 러너가 없어 CI가 lint·타입 검사·빌드·`dist` 검사만 함)
- [ ] 포트폴리오 화면의 대화를 실제 AI와 연동 (지금은 모의 응답)
- [ ] Pretendard 웹폰트 자체 호스팅: 버전을 고정한 npm 패키지(`pretendard@1.3.9`)의 CSS를 `main.tsx`에서 import 하고 `main.tsx`가 붙이는 CDN `<link>`를 지운 뒤, 운영 CSP에서 `https://cdn.jsdelivr.net`을 뺍니다 (`npm install` 필요)

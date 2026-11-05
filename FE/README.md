# FE 파트 - VC AI Career Agent 프론트엔드

디지털 배지·자격증명(VC) 기반 초개인화 AI 커리어 에이전트 지갑의 프론트엔드입니다.
BE(FastAPI, `../BE`)가 제공하는 REST API를 소비하는 **SPA(Single Page Application)** 입니다.

## 기술 스택

- **React 19 + TypeScript + Vite** — 빠른 개발 서버/빌드, SPA에 적합 (SSR 불필요한 개인 지갑 서비스)
- **Tailwind CSS v4** — 유틸리티 기반 스타일링 (Figma 디자인→코드 플러그인과의 호환성이 좋음)
- **React Router** — 클라이언트 라우팅
- **TanStack Query (React Query)** — 서버 상태(캐싱/로딩/에러) 관리
- **Zustand (persist)** — 로그인한 유저 정보(DID 포함)를 로컬에 보관하는 가벼운 전역 상태
- **React Hook Form + Zod** — 폼 상태 및 유효성 검증
- **Axios** — HTTP 클라이언트

## 폴더 구조

```
src/
├── api/            # BE 엔드포인트별 호출 함수 (auth.ts, credentials.ts, portfolios.ts, shares.ts)
├── components/     # 공통 UI (Layout, ProtectedRoute, StatusBadge, VerificationChecks, dashboard/*, portfolio/*, ui/*)
├── lib/            # axios 인스턴스, 에러 파싱, 개발용 미리보기 데이터 등 공용 유틸
├── pages/          # 라우트 단위 페이지 (Login, Register, Dashboard, IssueCredential, PortfolioBuilder, SharedView)
├── store/          # Zustand 전역 상태 (authStore)
└── types/          # BE 응답 타입 정의 (api.ts)
```

## 로컬 실행 방법

BE 서버(`../BE`)가 먼저 `http://localhost:3000` 에서 실행되고 있어야 합니다.
(BE 실행법은 `../BE/README.md` 참고: `docker compose up -d` 로 DB 기동 후 `python -m uvicorn main:app --reload --port 3000`)

```bash
cd FE
npm install
cp .env.example .env   # 필요 시 VITE_API_BASE_URL 수정
npm run dev
```

- 개발 서버: `http://localhost:5173`
- `/api` 로 시작하는 요청은 `vite.config.ts` 의 프록시 설정을 통해 BE(`http://localhost:3000`)로 전달됩니다.

## 구현된 화면과 BE 엔드포인트 매핑

| 화면 | 설명 | 연동 BE 엔드포인트 |
| --- | --- | --- |
| `/register` | 회원가입 (이름·이메일·비밀번호, DID 자동 발급) | `POST /api/v1/users/register` |
| `/login` | 이메일 + 비밀번호 로그인 | `POST /api/v1/users/login` |
| `/dashboard` | 자격증명 지갑 (status별 구분 표시, 상세에서 **지금 다시 검증하기**, 관리형 지갑 표시) | `GET /api/v1/subjects/{did}/careers`, `POST /api/v1/credentials/{record_id}/reverify`, `GET /api/v1/users/me` |
| `/issue` | 모의 배지 발급 → 검증 → 저장 (2단계 플로우) | `POST /api/mock-issuer/issue`, `POST /api/v1/verify` |
| `/portfolio` | AI 포트폴리오 빌더 — 대화(AI 연동 전 모의 응답) + **검증 후 저장(VC_ONLY)** + 저장 목록 + **선택 공개 링크** | `GET /api/v1/subjects/{did}/careers`, `/api/v1/portfolios`, `/api/v1/shares` |
| `/share/:token` | 기업용 조회 (로그인 불필요, 열 때마다 재검증, 서명 원본 제공) | `GET /api/v1/public/shares/{token}` |

API 계약은 `BE/openapi.yaml`(v0.6.0)을 기준으로 합니다.

- **협력사 자료 취급:** 템플릿 ID·API 주소 등 협력사에서 받은 값은 FE 코드에 넣지 않습니다. 발급 화면의 Template ID는 비워두면 BE 서버 설정의 기본 템플릿을 씁니다.
- **verify 응답 `data`:** BE 공통 형식(`achievement_name`, `issuer_name`, `issuer_trusted`, `status`, `record_id`, `checks` 등)입니다. 외부 검증기 응답 원문이 아닙니다.
- **실행 시점 재검증 (v0.6.0):** 지갑 상세의 "지금 다시 검증하기"는 저장된 서명 원본을 다시 검증하고 DB 상태도 갱신합니다.
  서명 원본이 없는 자격증명(시드 데이터 등)은 `unverifiable` 로 표시됩니다.
- **포트폴리오 (VC_ONLY, v0.6.0):** 자유 텍스트 본문은 보내지 않고 `title`·`target_role`·`record_ids`·`source: "VC_ONLY"` 만 보냅니다.
  BE가 저장 직전에 재검증해 통과한 것만 본문에 넣고 나머지는 `excluded`(사유 포함)로 돌려줍니다. 통과한 것이 없으면 422.
  저장 후에는 본문을 수정할 수 없습니다(제목·직무·상태만 PATCH 가능).
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
| 로그인 | 로그아웃 / DID 만료 후 진입 | - | "로그아웃되었습니다" / "로그인 정보가 만료되었습니다" 배너 |
| 회원가입 | 이미 가입된 이메일 | 400 | 이메일 칸 에러 + "이 이메일로 로그인"(이메일 자동 입력) |
| 회원가입 | 8자 미만 / 72바이트 초과 / 확인 불일치 | (FE 사전 검증, 서버는 422) | 각 입력칸 아래 메시지 |
| 회원가입 | 성공 | 200 | 바로 로그인 → 대시보드 "회원가입이 완료되었습니다" 배너 |
| 대시보드·포트폴리오 | 저장된 DID가 서버에 없음 | 404 | "계정 정보를 찾을 수 없습니다" + 다시 로그인 |
| 대시보드·포트폴리오 | 저장된 DID ≠ 토큰의 DID | 403 | "로그인 정보가 일치하지 않습니다" + 다시 로그인 |
| 전 화면 | 토큰 만료 / 위조 | 401 | 자동 로그아웃 → 로그인 화면 "로그인 정보가 만료되었습니다" |
| 대시보드 | 자격증명 0개 | 200 | 빈 상태 + "첫 자격증명 발급받기" |
| 발급 | 템플릿 오류 / 발급 인프라 오류 · 설정 누락 | 502 / 503 | "배지 발급에 실패했습니다" + 원인 안내 |
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

> FE는 로그인 응답의 사용자 정보(`did` 포함)와 `access_token` 을 Zustand persist 로 로컬에 저장해 로그인 상태를 유지합니다.
> BE는 전환 기간 동안 `AUTH_REQUIRED=false`(기본값)라 토큰 없는 요청도 받지만, FE는 이미 토큰을 보내므로 `true` 로 바꿔도 동작합니다.
>
> BE v0.6.0 업데이트 후 기존 BE 환경을 쓰고 있다면 BE README의 안내대로 `docker compose up -d --build` 와
> 마이그레이션(`05_credentials_raw_token.sql`, `06_holder_keys.sql`, `07_shares.sql`)을 실행하거나 `docker compose down -v && docker compose up -d --build` 로
> DB를 다시 만들어야 합니다. BE `.env` 의 `JWT_SECRET` 을 비워두면 BE 재시작 때마다 토큰이 무효가 되어 다시 로그인해야 합니다.
> BE `CORS_ORIGINS` 기본값은 `http://localhost:5173` 이며, 개발 중에는 vite 프록시를 쓰므로 CORS 영향이 없습니다.

## 스크립트

| 명령 | 설명 |
| --- | --- |
| `npm run dev` | 개발 서버 실행 |
| `npm run build` | 타입체크(`tsc -b`) + 프로덕션 빌드 |
| `npm run lint` | oxlint 실행 |
| `npm run preview` | 빌드 결과 미리보기 |

## 다음 단계 (TODO)

- [x] BE v0.6.0 연동 — 실행 시점 재검증, 포트폴리오(VC_ONLY) 저장·목록·삭제, 선택 공개 링크·기업용 조회 화면
- [ ] 포트폴리오 제목·직무·게시 상태 수정 UI (`PATCH /portfolios/{id}`, API 함수는 `api/portfolios.ts` 에 있음)
- [ ] AI 브랜치(RAG 커리어 가이드) 연동 — 대화 탭의 모의 응답 교체, BE `GET /subjects/{did}/evidence` 근거 사용
- [ ] Figma 디자인 확정 후 MCP/플러그인 기반 컴포넌트 교체
- [x] BE JWT 인증(v0.4.0) 연동 — `apiClient` Bearer 인터셉터 + 401 자동 로그아웃

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
├── api/            # BE 엔드포인트별 호출 함수 (auth.ts, credentials.ts)
├── components/     # 공통 UI (Layout, ProtectedRoute, StatusBadge, ui/*)
├── lib/            # axios 인스턴스, 에러 파싱 등 공용 유틸
├── pages/          # 라우트 단위 페이지 (Login, Register, Dashboard, IssueCredential)
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
| `/dashboard` | 자격증명 지갑 (status별 구분 표시) | `GET /api/v1/subjects/{did}/careers` |
| `/issue` | 모의 배지 발급 → 검증 → 저장 (2단계 플로우) | `POST /api/mock-issuer/issue`, `POST /api/v1/verify` |
| `/portfolio` | AI 포트폴리오 빌더 (verified 자격증명만 사용, AI 연동 전 모의 응답) | `GET /api/v1/subjects/{did}/careers` |

API 계약은 BE 브랜치의 `BE/openapi.yaml`(v0.4.0)을 기준으로 합니다.

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
- **verify API**: 검증 실패도 HTTP 200 + `success: false` 로 응답한다. v0.4.0부터 `status`·`record_id` 가 추가되어,
  폐기·만료 배지는 `success: false` 여도 해당 status로 지갑에 저장됩니다(`record_id` 존재).

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
| 발급 | 템플릿 오류 / DXWorks 오류·설정 누락 | 502 / 503 | "배지 발급에 실패했습니다" + 원인 안내 |
| 발급 | 가입되지 않은 DID / 남의 DID / 다른 사용자에 이미 등록 | 404 / 403 / 409 | "배지를 발급(저장)할 수 없습니다" + 사유 |
| 발급 | 검증 실패 (저장 안 됨) | 200 + `success:false`, `record_id:null` | "배지 검증에 실패했습니다" + 처음부터 다시 발급 |
| 발급 | 폐기·만료 배지 (상태와 함께 저장됨) | 200 + `success:false`, `record_id` 있음 | 상태 배지 + "지갑에 기록되었습니다" + 지갑에서 확인하기 |
| 발급 | 성공 | 200 | 완료 단계(배지·발급기관 표시) → 대시보드 "지갑에 저장되었습니다" 배너 |
| 전 화면 | BE 서버 미실행 | 연결 실패 (vite 프록시 502) | "서버에 연결할 수 없습니다" (+ 조회 화면은 다시 시도) |

> FE는 로그인 응답의 사용자 정보(`did` 포함)와 `access_token` 을 Zustand persist 로 로컬에 저장해 로그인 상태를 유지합니다.
> BE는 전환 기간 동안 `AUTH_REQUIRED=false`(기본값)라 토큰 없는 요청도 받지만, FE는 이미 토큰을 보내므로 `true` 로 바꿔도 동작합니다.
>
> BE v0.4.0 업데이트 후 기존 BE 환경을 쓰고 있다면 BE README의 안내대로 `docker compose up -d --build` 와
> 마이그레이션(`05_credentials_raw_token.sql`)을 실행하거나 `docker compose down -v && docker compose up -d --build` 로
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

- [ ] `portfolios` 테이블은 BE DB 스키마에 존재하지만 API가 아직 없음 — BE팀과 엔드포인트 협의 필요
- [ ] AI 브랜치(RAG 커리어 가이드) 연동 화면 설계
- [ ] Figma 디자인 확정 후 MCP/플러그인 기반 컴포넌트 교체
- [x] BE JWT 인증(v0.4.0) 연동 — `apiClient` Bearer 인터셉터 + 401 자동 로그아웃

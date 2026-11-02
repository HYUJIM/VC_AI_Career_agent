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
| `/register` | 회원가입 (DID 자동 발급) | `POST /api/v1/users/register` |
| `/login` | 로그인 | `POST /api/v1/users/login` |
| `/dashboard` | 검증된 자격증명 지갑 목록 | `GET /api/v1/subjects/{did}/careers` |
| `/issue` | 모의 배지 발급 → 검증 → 저장 (2단계 플로우) | `POST /api/mock-issuer/issue`, `POST /api/v1/verify` |

> BE에는 별도의 인증 토큰/세션이 없고 DID 기반 간이 로그인만 존재합니다. FE는 로그인 성공 시 받은
> `user_profile` 을 Zustand persist 로 로컬에 저장해 로그인 상태를 유지합니다. 운영 전환 시 BE에
> JWT/세션이 추가되면 `lib/apiClient.ts` 에 인터셉터를 추가해 토큰을 주입하세요.

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
- [ ] BE에 실제 인증(JWT 등) 도입 시 `apiClient` 에 인터셉터 추가

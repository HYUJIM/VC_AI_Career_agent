# BE 파트 — API 게이트웨이 (BFF)

FE/AI 팀에게 **완성된 단일 응답**을 제공하는 Backend For Frontend(BFF) 역할의 백엔드 파트입니다.
VC 지갑 서버(`localhost:4000`)를 내부에서 직접 호출하고, 자체 DB 데이터와 조립하여 FE/AI에게 내려줍니다.

**지금은 논문 우선 개발 기간**이라 NestJS 실 비즈니스 로직 구현 전, **Prism Mock 서버**로
AI/FE 팀이 즉시 개발할 수 있는 환경을 먼저 제공합니다.
실 로직은 논문 이후 NestJS + Axios + PostgreSQL(Prisma) 조합으로 구현 예정입니다.

## 1. 요구 사항

- Node.js 20 이상
- npm

## 2. 처음 받았을 때 (clone 직후)

```bash
cd BE
npm install
```

별도 환경변수(.env) 설정 없이 바로 `npm run mock`으로 Mock 서버를 실행할 수 있습니다.

## 3. 실행

### Mock 서버 실행 (논문 기간)

```bash
npm run mock
```

기동 로그 예시:

```
[CLI] i  info   GET  http://127.0.0.1:4010/health
[CLI] i  info   GET  http://127.0.0.1:4010/v1/subjects/...
[CLI] ►  start  Prism is listening on http://127.0.0.1:4010
```

헬스체크:

```bash
curl http://127.0.0.1:4010/health
```

### 실서버 실행 (논문 이후 — NestJS 구현 후)

```bash
npm run start:dev
# → http://localhost:3000 에서 NestJS 개발 서버 기동
```

## 4. API

Base URL: `http://localhost:4010` (Mock 서버) / `http://localhost:3000` (논문 이후 실서버)

명세 파일은 [`openapi.yaml`](./openapi.yaml)에 정리되어 있습니다.

| Method | Path | 설명 |
| --- | --- | --- |
| GET | `/health` | 서버 상태, 모드, 로드된 레코드 수 |
| GET | `/v1/subjects/:did/credentials` | 자격증명 목록. **기본값은 `status=verified`만** 반환 |
| GET | `/v1/subjects/:did/credentials?status=all` | 비정상 케이스 포함 전체 반환 (테스트/디버깅용) |
| GET | `/v1/records/:id` | 단일 `CredentialRecord` 전체 (스킬·근거·검증 결과 포함) |
| GET | `/v1/records/:id/verification` | 해당 레코드의 `VerificationResult` (7개 체크 배열 항상 전부) |
| GET | `/v1/subjects/:did/skills` | `verified` 레코드에서 집계한 `SkillClaim[]` |
| POST | `/v1/verify` `{ "external_id": "..." }` | 그 자리에서 파이프라인을 재실행 (AI MCP용) |

데모용 subject: `did:fixture:student-001`

```bash
curl "http://127.0.0.1:4010/v1/subjects/did:fixture:student-001/credentials"
curl "http://127.0.0.1:4010/v1/subjects/did:fixture:student-001/skills"
curl "http://127.0.0.1:4010/v1/records/3f444a85-191b-51a4-83a6-73fad567c272/verification"
```

응답 스키마는 VC 파트의 `src/schemas/credential-record.schema.json`(JSON Schema)과
`src/types/credential.ts`(TypeScript 타입)를 그대로 참조합니다.

> FE/AI 팀이 처음 연동한다면 VC 파트의 [`docs/API_GUIDE.md`](../VC/docs/API_GUIDE.md)를 먼저 읽는 것을 추천합니다.
> `status` 기본값이 왜 verified인지, `record_id`를 왜 외래키로 써야 하는지까지 구체적으로 적혀 있습니다.

## 5. 왜 "기본값이 verified"인가

기업/AI가 근거 없는 스펙을 사실처럼 받는 일을 API 레벨에서 막기 위해서입니다.
`status` 쿼리를 생략하면 무조건 `verified`만 내려가고, 위조·폐기·만료·미상 발급자 데이터를 받으려면
`?status=all` 또는 `?status=invalid,revoked`처럼 **명시적으로** 요청해야 합니다.

## 6. 아키텍처: BFF (Backend For Frontend)

BE는 VC 지갑 서버를 단순히 통과시키는 프록시(Proxy)가 아닙니다.
VC 서버와 자체 DB를 **서버에서 직접 조립**하여 FE/AI에게 완성된 단일 응답을 제공합니다.

```
[FE / AI]
    │  요청 1번
    ▼
[BE :4010(Mock) / :3000(실서버)]
    ├─── 내부 HTTP 호출 ───▶ [VC 지갑 서버 :4000]  ← 검증된 자격증명/스킬
    └─── DB 조회 (논문 이후) ▶ [PostgreSQL]         ← 이력서, 사용자 정보
    │
    └─── 조립·가공된 완성 데이터 ──▶ [FE / AI]
```

| 구분 | 프록시 방식 | BFF 방식 (채택) |
| --- | --- | --- |
| FE/AI 요청 횟수 | 여러 번 (VC 따로, DB 따로) | **딱 1번** |
| 데이터 조립 주체 | 프론트엔드(브라우저) | **백엔드(서버)** |
| 민감 정보 필터링 | 어려움 | **서버에서 차단 가능** |
| AI 컨텍스트 품질 | 조각 데이터를 AI가 직접 조합 | **완성된 데이터 세트 1개 제공** |

## 7. 디렉토리 구조

```
BE/
├── openapi.yaml        # OpenAPI 3.1 명세 (VC 스키마 참조, Prism Mock 설정 기준)
├── package.json        # npm 스크립트 (mock / start:dev)
└── README.md           # 이 문서
```

논문 이후 NestJS 실서버 구축 시 추가 예정:

```
BE/
├── src/
│   ├── config/         # 환경변수 로더
│   ├── modules/
│   │   ├── credentials/  # 자격증명 조회 (VC 서버 호출)
│   │   ├── skills/       # 역량 집계 (VC 서버 호출)
│   │   ├── resume/       # 이력서 생성 (VC + DB 조립)
│   │   └── auth/         # JWT 인증/인가
│   ├── common/           # 공통 DTO, 필터, 인터셉터
│   └── main.ts
├── prisma/               # PostgreSQL 스키마 (Prisma)
└── test/
```

## 8. 다른 파트와의 연동

- **VC**: `http://localhost:4000` API를 Axios로 직접 호출. 계약은 VC의 `docs/openapi.yaml` 및 `src/schemas/*.schema.json`.
- **AI**: BE의 `GET /v1/subjects/:did/skills`, `GET /v1/subjects/:did/credentials`를 MCP 툴에서 감싸서 사용.
- **DB**: `CredentialRecord` / `SkillClaim` 필드 그대로 Prisma 스키마 설계에 반영. `record_id`(UUIDv5)를 외래키로 사용.
- **FE**: BE API 한 번만 호출하면 완성된 데이터 수신. `status=all`은 관리/디버그 전용.

스키마 변경이 필요하면 팀 채널에 먼저 공지 후 VC 파트의 `schema_version`을 올려주세요.

# VC_AI_Career_agent
[종합설계프로젝트1] 디지털 배지 및 자격증명 기반 초개인화 AI 커리어 에이전트 지갑 개발 과제

---

# VC 파트 — 검증 이력 지갑 코어

W3C VC 2.0 / Open Badges 2.0·3.0 자격증명을 파싱·정규화하고, DID 조회·서명 검증·발급자 신뢰·폐기 확인을 거쳐
`verified | invalid | revoked | expired | unverifiable` 상태로 판정하는 검증 엔진입니다.

**지금은 논문 우선 개발 기간**이라 DX Ledger 실제 API 대신 **로컬 fixture(가짜 발급기관 + 실제 서명)**로
전체 파이프라인이 돌아갑니다. DX Ledger 문서가 오면 `src/adapters/ports.ts`의 포트를 구현하는 어댑터만
추가하면 되고, 파서/검증/서버 코드는 손댈 필요가 없습니다.

## 1. 요구 사항

- Node.js 20 이상 (권장: 22+)
- npm

## 2. 처음 받았을 때 (clone 직후)

```bash
git clone <repo-url>
cd VC
npm install
cp .env.example .env
```

fixture(가짜 발급기관 키·자격증명·신뢰 레지스트리)는 이미 레포에 커밋되어 있으므로
**바로 `npm run dev`를 실행해도 됩니다.** 별도로 키나 fixture를 만들 필요가 없습니다.

만약 fixture를 처음부터 다시 만들고 싶다면(키를 새로 생성하고 싶을 때만):

```bash
npm run setup:fixtures   # 키 생성 -> 서명된 자격증명 9건 재생성
```

## 3. 실행

```bash
npm run dev
```

기동 로그 예시:

```
[vc-mock-server] fixture 자격증명 검증 중...
[vc-mock-server] 9건 로드 완료 (mode=fixture)
[vc-mock-server] http://localhost:4000 에서 대기 중
```

헬스체크:

```bash
curl http://localhost:4000/health
```

## 4. API (다른 파트가 쓰는 계약)

Base URL: `http://localhost:4000` (포트는 `.env`의 `PORT`)

서버를 띄운 상태에서 바로 탐색하려면 **`http://localhost:4000/docs`**(Swagger UI)를 열거나,
원시 스펙은 **`http://localhost:4000/openapi.json`**으로 받으면 됩니다. 형식 명세 파일은
[`docs/openapi.yaml`](./docs/openapi.yaml)이고, 사용 예시·에러 코드·BE·AI 연동 방법까지 자세히 설명한
서술형 가이드는 [`docs/API_GUIDE.md`](./docs/API_GUIDE.md)에 따로 정리해 두었습니다.

| Method | Path | 설명 |
| --- | --- | --- |
| GET | `/health` | 서버 상태, 로드된 레코드 수 |
| GET | `/docs` | Swagger UI (대화형 탐색) |
| GET | `/openapi.json` | OpenAPI 3.1 원시 스펙 (코드 생성기용) |
| GET | `/v1/subjects/:did/credentials` | 자격증명 목록. **기본값은 `status=verified`만** 반환 |
| GET | `/v1/subjects/:did/credentials?status=all` | 비정상 케이스 포함 전체 반환 (테스트/디버깅용) |
| GET | `/v1/records/:id` | 단일 `CredentialRecord` 전체 |
| GET | `/v1/records/:id/verification` | 해당 레코드의 `VerificationResult` (7개 체크 배열) |
| GET | `/v1/subjects/:did/skills` | `verified` 레코드에서 집계한 `SkillClaim[]` |
| POST | `/v1/verify` `{ "external_id": "..." }` | 그 자리에서 파이프라인을 재실행 |

데모용 subject: `did:fixture:student-001` (fixture 9건 중 8건이 이 학생 소유, 상태가 섞여 있음)

```bash
curl "http://localhost:4000/v1/subjects/did:fixture:student-001/credentials"
curl "http://localhost:4000/v1/subjects/did:fixture:student-001/credentials?status=all"
curl "http://localhost:4000/v1/subjects/did:fixture:student-001/skills"
```

응답 스키마는 `src/schemas/*.schema.json` (JSON Schema)과 `src/types/credential.ts` (TypeScript 타입)에
**항상 동일하게** 정의되어 있습니다. 타입만 보고 싶다면 `src/types/credential.ts`를 확인하세요.

> BE/AI 팀이 처음 연동한다면 README보다 [`docs/API_GUIDE.md`](./docs/API_GUIDE.md)를 먼저 읽는 것을 추천합니다:
> 왜 `status` 기본값이 verified인지, `record_id`를 왜 외래키로 써야 하는지, AI 팀이 MCP 툴을 어떻게 래핑하면 되는지까지 구체적으로 적혀 있습니다.

## 5. 왜 "기본값이 verified"인가

기업/AI가 근거 없는 스펙을 사실처럼 받는 일을 API 레벨에서 막기 위해서입니다.
`status` 쿼리를 생략하면 무조건 `verified`만 내려가고, 위조·폐기·만료·미상 발급자 데이터를 받으려면
`?status=all` 또는 `?status=invalid,revoked`처럼 **명시적으로** 요청해야 합니다.

## 6. fixture 구성

`fixtures/` 아래에 있으며 전부 실제 Ed25519 서명이 들어간 진짜(테스트용) 자격증명입니다.

- `fixtures/keys/*.json` — 발급기관 3곳(acme-university, globex-academy, shady-academy)의 키쌍. **테스트 전용 키**이며 실제 비밀키가 아니므로 커밋되어 있습니다.
- `fixtures/dids/did-documents.json` — 각 발급기관의 DID Document (공개키 포함)
- `fixtures/registry/trust-registry.json` — 신뢰 발급기관 목록 (`shady-academy`는 의도적으로 미등록 → `unverifiable` 케이스)
- `fixtures/registry/revocation-list.json` — 폐기된 자격증명 externalId 목록
- `fixtures/registry/skill-alias-dict.json` — alignment 코드 → 공통 역량(SkillClaim) 매핑 사전
- `fixtures/credentials/*.json` — 자격증명 9건 (정상 4 + 비정상 5), 각 파일에 `expectedStatus`가 같이 적혀 있어 테스트가 이 값을 기준으로 검증합니다.

| externalId | 형식 | 기대 상태 | 설명 |
| --- | --- | --- | --- |
| cred-ob2-hosted-001 | OB2.0 hosted | verified | 서명 없이 호스팅 신뢰 |
| cred-ob2-signed-002 | OB2.0 signed (JWS) | verified | 정상 서명 |
| cred-ob3-vcjwt-003 | OB3.0 VC-JWT | verified | 발급자 trust=known |
| cred-ob3-di-004 | OB3.0 Data Integrity | verified | 정상 서명 |
| cred-ob2-signed-forged-005 | OB2.0 signed | invalid | 서명 값 변조 (위조 탐지) |
| cred-ob3-di-revoked-006 | OB3.0 Data Integrity | revoked | 폐기 목록에 등록됨 |
| cred-ob3-vcjwt-expired-007 | OB3.0 VC-JWT | expired | 만료일 경과 |
| cred-ob2-hosted-untrusted-008 | OB2.0 hosted | unverifiable | 신뢰 레지스트리에 없는 발급자 |
| cred-ob3-di-schema-invalid-009 | OB3.0 Data Integrity | invalid | 필수 필드 누락 (스키마 위반) |

### fixture를 더 추가하고 싶다면

1. `scripts/generate-fixtures.ts`에 케이스를 하나 추가하고 `expectedStatus`를 정확히 적습니다.
2. `npm run generate:fixtures` 실행 (키는 그대로 재사용됨).
3. `npm test`로 새 케이스가 기대한 상태로 나오는지 확인합니다.

## 7. 테스트

```bash
npm test          # 전체 실행 (파이프라인 단위 테스트 + API 통합 테스트)
npm run test:watch
```

`test/verifyPipeline.test.ts`는 fixture 9건을 전부 돌려서 `expectedStatus`와 실제 판정이 일치하는지
자동으로 검증합니다. fixture를 추가/수정했는데 이 테스트가 깨진다면 검증 로직이나 fixture 중 하나가 잘못된 것입니다.

## 8. 아키텍처 한 줄 요약

```
RawCredentialRef(document|compactJws)
  → parseCredential (OB2.0/OB3.0 자동 감지)
  → normalizeCredential (CredentialRecord 초안 + SkillClaim 매핑)
  → 7개 체크(schema/did_resolution/signature/issuer_trust/revocation/anchor_match/expiration)
  → VerificationResult.status 도출
  → CredentialRecord 완성
```

벤더 의존성은 전부 `src/adapters/ports.ts`의 인터페이스 뒤에 격리되어 있고, 지금은
`src/adapters/fixture/*`만 구현되어 있습니다. DX Ledger 연동은 같은 인터페이스를 구현하는
`src/adapters/dxledger/*`를 추가하고 `src/container.ts`의 분기만 바꾸면 됩니다(다른 코드 수정 불필요).

## 9. 디렉토리 구조

```
VC/
├── src/
│   ├── config/env.ts            # 환경변수 로더
│   ├── types/credential.ts      # 공유 도메인 타입 (계약의 실체)
│   ├── schemas/*.schema.json    # 같은 계약의 JSON Schema 버전
│   ├── crypto/                  # Ed25519, compact JWS, Data Integrity, JCS 해시
│   ├── resolver/                # did-resolver 조립 + fixture DID 드라이버
│   ├── adapters/
│   │   ├── ports.ts             # 벤더 격리 인터페이스
│   │   └── fixture/             # 지금 유일한 구현체
│   ├── parsers/                 # OB2.0 / OB3.0 파서
│   ├── normalize/               # CredentialRecord 정규화 + 스킬 매핑
│   ├── verify/                  # 7종 체크 + 파이프라인 오케스트레이션
│   ├── container.ts             # 어댑터 조립 지점 (fixture ↔ dxledger 전환점)
│   └── server/                  # Express Mock 서버
├── scripts/                      # fixture 키/데이터 생성 스크립트
├── fixtures/                      # 커밋된 테스트 데이터 (키 포함, 전부 가짜)
└── test/                          # vitest 테스트
```

## 10. 다른 파트와의 연동

- **BE**: 이 서버를 그대로 프록시하거나, `src/**`를 패키지로 import해서 씁니다. 계약은 `src/schemas/*.schema.json`.
- **AI**: `GET /v1/subjects/:did/skills`, `GET /v1/subjects/:did/credentials?status=verified`, `POST /v1/verify`를 MCP 툴에서 감싸서 씁니다.
- **DB**: `CredentialRecord`/`SkillClaim` 필드 그대로 테이블/벡터 스키마 설계에 반영하면 됩니다.

질문이나 스키마 변경이 필요하면 팀 채널에 먼저 공지 후 `schema_version`을 올려주세요.

# VC 지갑 코어 — 데이터 인터페이스 가이드 (BE·AI 팀용)

형식 명세(기계가 읽는 문서)는 [`docs/openapi.yaml`](./openapi.yaml)이고, 서버를 띄운 상태에서
`http://localhost:4000/docs`(Swagger UI) 또는 `http://localhost:4000/openapi.json`(원시 스펙)으로도
바로 확인할 수 있습니다. 이 문서는 그 스펙을 실제로 어떻게 쓰면 되는지, 왜 이렇게 설계했는지를 설명하는
서술형 가이드입니다.

## 0. 한 문장 요약

**VC 파트는 "검증된 것만 verified로 나가는" 자격증명 조회 API를 제공한다.** BE는 이 API를 그대로
프록시하거나 감싸서 쓰고, AI는 이 API를 MCP 툴로 감싸서 LLM에 노출합니다. 두 팀 모두 자체적으로
서명·폐기·발급자 신뢰를 다시 검증할 필요가 없습니다 — 그건 이미 VC 파트가 끝냈습니다.

## 1. 접속 정보

- Base URL (로컬 개발): `http://localhost:4000`
- 인증: 없음 (논문 기간 fixture 모드. 실서비스 전환 시 API Key/JWT 추가 예정, 별도 공지)
- 데이터 포맷: 모든 요청/응답은 `application/json`
- CORS: `.env`의 `CORS_ORIGIN`에 등록된 오리진만 허용 (BE/AI 로컬 개발 서버 주소를 여기 추가하세요)

## 2. 데이터 모델 한눈에 보기

```
CredentialRecord                 <- 정규화된 자격증명 하나
├── record_id                    <- 안정적인 내부 식별자 (아래 3번 참고)
├── subject_did                  <- 이 자격증명의 소유자
├── source                       <- 원본이 어디서 왔는지 (DX Ledger Wallet/Badge/hosted_url/upload)
├── format                       <- OB2.0 | OB3.0 | VC1.1 | VC2.0
├── issuer                       <- 발급자 정보 + trust_level(accredited/known/unknown)
├── achievement                  <- 배지/성취 내용 (이름, 설명, 기준, alignment)
├── skills[]                     <- 정규화된 공통 역량 (SkillClaim, 아래 참고)
└── verification                 <- 검증 결과 (VerificationResult, 아래 참고)

VerificationResult
├── status                       <- verified | invalid | revoked | expired | unverifiable | pending
├── checks[7]                    <- schema, did_resolution, signature, issuer_trust,
│                                    revocation, anchor_match, expiration (항상 7개 전부)
├── proof                        <- 서명 방식 정보 (VC-JWT / DataIntegrityProof / HostedVerification)
└── anchor                       <- 원장 앵커 대조 정보 (지금은 항상 checked=false, skip)

SkillClaim
├── skill_id                     <- 내부 공통 역량 ID (예: SK.DEV.BACKEND)
├── taxonomy                     <- NCS/ESCO/internal 중 어떤 체계의 어떤 코드인지
├── confidence, mapping_method   <- 이 매핑을 얼마나 믿을 수 있는지 + 어떤 방법으로 매핑했는지
└── source_record_ids[]          <- "어떤 자격증명이 이 역량의 근거인지" (근거 제시용 핵심 필드)
```

전체 필드 타입은 `src/types/credential.ts`(TypeScript) 또는 `src/schemas/credential-record.schema.json`
(JSON Schema)에 정의되어 있고 **둘은 항상 동일**합니다. 코드 생성기를 쓸 거면 JSON Schema나
`docs/openapi.yaml`을 입력으로 쓰세요.

## 3. 왜 `record_id`가 중요한가

`record_id`는 원본 자격증명의 `external_id`로부터 만든 **결정적(deterministic) UUID**입니다
(`uuidv5` 사용). 즉:

- 서버를 재시작해도, 다른 팀원의 로컬 환경에서도 **같은 자격증명은 항상 같은 record_id**를 가집니다.
- BE/DB가 자체 테이블에서 자격증명을 참조할 때는 반드시 이 `record_id`를 외래키로 쓰세요.
  DX Ledger의 원본 ID(`source.external_id`)를 직접 참조하면 나중에 벤더가 바뀔 때 깨집니다.
- AI가 "이 근거로 이 스킬을 판단했다"고 답할 때 인용해야 하는 값도 `record_id`입니다
  (`SkillClaim.source_record_ids`, 논문 실험의 "근거 정확도" 지표가 바로 이 값의 일치 여부를 봅니다).

## 4. 왜 기본값이 `verified`인가 (가장 중요한 계약)

`GET /v1/subjects/{did}/credentials`는 **`status` 쿼리를 생략하면 무조건 `verified`만** 반환합니다.

```bash
# 기본: verified만 (BE 이력서 생성, AI 근거 제시 등 실사용 경로는 항상 이걸 씁니다)
curl "http://localhost:4000/v1/subjects/did:fixture:student-001/credentials"

# 명시적으로 전체를 봐야 할 때만 (관리자 화면, 디버깅, 테스트)
curl "http://localhost:4000/v1/subjects/did:fixture:student-001/credentials?status=all"
curl "http://localhost:4000/v1/subjects/did:fixture:student-001/credentials?status=invalid,revoked"
```

`GET /v1/subjects/{did}/skills`도 동일한 원칙으로, **`verified` 레코드에서 나온 스킬만** 집계합니다.
반대로 `GET /v1/records/{id}`는 `record_id`를 이미 알고 있다는 전제이므로 상태와 무관하게 전체를
반환합니다 (기업용 "이 자격증명이 왜 무효인지 확인" 같은 근거 화면에 씁니다).

**BE/AI 팀에게 부탁**: 사용자에게 보여주는 이력서·포트폴리오·채용 매칭 결과는 반드시 기본값(verified)
경로만 쓰세요. `status=all`은 관리/디버그 화면 전용입니다. 이게 이 과제의 "허위 스펙 기재 원천 차단"
원칙의 실제 구현입니다.

## 5. 엔드포인트별 사용 예시

### 5.1 `GET /health`

```bash
curl http://localhost:4000/health
# {"status":"ok","mode":"fixture","recordCount":9}
```

서버가 아직 fixture를 검증 중이면 `status: "starting"`이 나옵니다. BE 배포 파이프라인의 readiness
체크로 그대로 써도 됩니다.

### 5.2 `GET /v1/subjects/{did}/credentials`

```bash
curl "http://localhost:4000/v1/subjects/did:fixture:student-001/credentials"
```

```json
{
  "subject_did": "did:fixture:student-001",
  "status_filter": ["verified"],
  "count": 4,
  "records": [
    {
      "record_id": "3f444a85-191b-51a4-83a6-73fad567c272",
      "format": "OB2.0",
      "achievement_name": "백엔드 개발 기초",
      "issuer_name": "ACME University",
      "status": "verified",
      "issued_at": "2026-03-01T00:00:00Z"
    }
  ]
}
```

목록 응답은 **요약 필드만** 담습니다 (전체 `CredentialRecord`를 다 내려주면 이력서 목록 화면 하나
띄우는 데 필요 이상으로 무거워집니다). 상세가 필요하면 `record_id`로 2번 엔드포인트를 다시 호출하세요.

### 5.3 `GET /v1/records/{id}`

```bash
curl "http://localhost:4000/v1/records/3f444a85-191b-51a4-83a6-73fad567c272"
```

`CredentialRecord` 전체(스킬, 근거, 검증 결과 포함)를 반환합니다. 없는 ID면 `404`.

### 5.4 `GET /v1/records/{id}/verification`

`CredentialRecord.verification`만 따로 뽑아줍니다. "왜 이게 invalid인지" 보여주는 UI(기업용 검증 뷰,
AI의 `wallet.verify_credential` 결과 설명)에 적합합니다.

```json
{
  "status": "revoked",
  "checks": [
    { "check": "schema", "result": "pass", "checked_at": "..." },
    { "check": "did_resolution", "result": "pass", "checked_at": "..." },
    { "check": "signature", "result": "pass", "checked_at": "..." },
    { "check": "issuer_trust", "result": "pass", "checked_at": "..." },
    { "check": "revocation", "result": "fail", "detail": "폐기 목록에 등록된 자격증명", "checked_at": "..." },
    { "check": "anchor_match", "result": "skip", "detail": "DX Ledger Builder API 미연동...", "checked_at": "..." },
    { "check": "expiration", "result": "pass", "checked_at": "..." }
  ],
  "verified_at": "...",
  "verifier_version": "vc-core-mock/0.1.0"
}
```

`checks`는 **항상 7개 전부** 옵니다. 어떤 체크가 왜 실패했는지, 또는 왜 `skip`인지(`anchor_match`는
논문 기간 내내 skip 고정)를 `detail` 필드로 사람이 읽을 수 있게 설명합니다. UI에서 이 배열을 그대로
렌더링하면 "근거 있는 무효 판정 설명"이 완성됩니다.

### 5.5 `GET /v1/subjects/{did}/skills`

```bash
curl "http://localhost:4000/v1/subjects/did:fixture:student-001/skills"
```

AI 커리어 플래너 팀이 갭 분석의 "보유 역량" 입력으로 바로 쓰는 엔드포인트입니다. 같은 `skill_id`가
여러 자격증명에서 나오면 하나로 합쳐지고 `source_record_ids`에 근거가 전부 남습니다.

### 5.6 `POST /v1/verify`

```bash
curl -X POST http://localhost:4000/v1/verify \
  -H "Content-Type: application/json" \
  -d '{"external_id": "cred-ob2-hosted-001"}'
```

캐시를 쓰지 않고 그 자리에서 파이프라인을 다시 돌립니다. 데모/디버깅용이며, AI 팀의
`wallet.verify_credential` MCP 툴이 이 엔드포인트를 감싸면 됩니다.

## 6. 에러 코드

| HTTP | error 값 | 발생 상황 |
| --- | --- | --- |
| 400 | `bad_request` | `POST /v1/verify`에 `external_id`를 안 보냈을 때 |
| 404 | `not_found` | 존재하지 않는 `record_id` / `external_id` / `did` (did는 목록이 빈 배열로 오히려 200) |
| 422 | `verification_failed` | 파이프라인 실행 중 처리 불가능한 오류 (알 수 없는 포맷 등) |

에러 응답은 항상 `{ "error": "...", "message": "..." }` 형태입니다 (`docs/openapi.yaml`의
`ErrorResponse` 참고).

## 7. AI 팀: MCP 툴 래핑 가이드

계획대로 MCP 툴은 AI 파트가 직접 정의하고, 이 HTTP API를 감싸는 얇은 어댑터로 구현하면 됩니다
(로직을 MCP 서버 쪽에 복제하지 마세요). 권장 매핑:

| MCP 툴 | 감쌀 엔드포인트 | 비고 |
| --- | --- | --- |
| `wallet.list_credentials` | `GET /v1/subjects/{did}/credentials` | `status` 인자를 그대로 전달, 기본은 verified |
| `wallet.get_verification` | `GET /v1/records/{id}/verification` | 근거 설명용 |
| `wallet.get_skills` | `GET /v1/subjects/{did}/skills` | 갭 분석 입력 |
| `wallet.verify_credential` | `POST /v1/verify` | 즉석 재검증 |
| `wallet.gap_analysis` | (AI 파트 자체 구현) | `wallet.get_skills` 결과를 내부에서 소비 |

LLM에게 노출하는 툴의 입력 스키마는 `docs/openapi.yaml`의 `parameters`/`requestBody`를 그대로
JSON Schema로 변환해서 쓰면 이중 정의를 피할 수 있습니다.

## 8. BE 팀: 통합 방식

두 가지 중 편한 쪽을 고르세요.

1. **프록시**: BE(NestJS) 게이트웨이가 이 서버를 그대로 리버스 프록시하고, 인증/세션만 앞단에서 처리.
2. **직접 호출**: BE 서비스 코드가 이 서버에 HTTP로 요청 (Axios/fetch), 응답 타입은 `docs/openapi.yaml`을
   `openapi-generator` 또는 `orval` 같은 도구에 넣어 TypeScript 클라이언트를 자동 생성.

어느 쪽이든 **BE의 지갑 DB 직접 접근은 하지 않습니다** — 이 서버가 유일한 진실 공급원(source of truth)
입니다. 이력서 생성 API가 "검증된 자격증명만 근거로 사용"하도록 강제하려면, BE 쪽 요청 스키마에서도
`source: "VC_ONLY"`처럼 사용자가 직접 텍스트를 입력할 수 없게 막고 이 API가 준 `record_id`만
참조하도록 설계하세요 (계획 문서의 "데이터 무결성 강제" 원칙과 동일).

## 9. 변경 관리 정책

- 필드 **추가**는 무통보 허용 (기존 소비자는 모르는 필드를 무시하면 됨).
- 필드 **삭제/타입 변경**은 금지. 꼭 필요하면 새 필드를 추가하고 기존 필드를 `deprecated`로 표시.
- 계약이 바뀌면 팀 채널에 공지하고 `CredentialRecord.schema_version`을 올립니다.
- `docs/openapi.yaml`이 실제 구현과 항상 일치하는지는 `npm test`의 `/openapi.json` 테스트가
  최소한의 안전망 역할을 합니다 (엔드포인트 존재 여부만 확인, 스키마 drift까지는 못 잡으므로
  응답 필드를 바꿨다면 이 문서와 스펙도 같이 고쳐주세요).

## 10. 참고 문서

- [`README.md`](../README.md) — 로컬 실행/설치 가이드
- [`docs/openapi.yaml`](./openapi.yaml) — 형식 명세 (OpenAPI 3.1)
- `http://localhost:4000/docs` — 서버 기동 후 Swagger UI로 바로 탐색
- `src/types/credential.ts` — 타입의 실체 (TypeScript)
- `src/schemas/*.schema.json` — 타입의 실체 (JSON Schema)

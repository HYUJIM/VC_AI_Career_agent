# BE (Back-End) 파트

디지털 배지 및 자격증명 기반 초개인화 AI 커리어 에이전트 지갑 개발의 백엔드 파트 폴더입니다.

---

## 아키텍처 결정: BFF (Backend For Frontend) 방식

BE는 VC 지갑 서버와 연동할 때 **단순 프록시(Proxy)가 아닌, 직접 호출(BFF) 방식**을 채택합니다.

```
[FE / AI] ──요청 1번──▶ [BE (NestJS)] ──내부 호출──▶ [VC 지갑 서버 :4000]
                              │                              │
                              │◀────── VC 데이터 ────────────│
                              │◀────── BE 자체 DB 조회 ───────│
                              │
                              └──── 가공·조립된 완성 데이터 ──▶ [FE / AI]
```

### 왜 프록시가 아닌 BFF인가?

| 구분 | 프록시 방식 | BFF 방식 (채택) |
|---|---|---|
| FE/AI 요청 횟수 | 여러 번 (VC 따로, DB 따로) | **딱 1번** |
| 데이터 조립 주체 | 프론트엔드(브라우저) | **백엔드(서버)** |
| 민감 정보 필터링 | 어려움 | **가능 (서버에서 차단)** |
| AI 컨텍스트 품질 | 조각 데이터를 AI가 직접 조합 | **완성된 데이터 세트 1개 제공** |

> **논문 기간(~10/23)**: 실제 NestJS 로직 구현은 미루고, 현재는 Prism Mock 서버를 통해 FE/AI 팀이 즉시 개발할 수 있는 환경을 제공합니다.
> **논문 이후**: NestJS에서 VC 서버를 Axios/Fetch로 직접 호출하고, 자체 DB(PostgreSQL) 조회 결과와 합쳐 응답하는 실 로직을 구현합니다.

---

## 1. 현재 구현된 내용 (Phase 0~1 논문 기간용)

- **OpenAPI 3.1.0 명세서 (`openapi.yaml`) 작성 완료**: BE가 FE/AI에게 제공할 6개 핵심 엔드포인트 명세화
- **VC 스키마 100% 통합**: VC 파트의 `CredentialRecord`, `VerificationResult`, `SkillClaim` 등 스키마를 그대로 참조하여 이중 정의 차단
- **기본값 강제 정책**: `status` 파라미터 생략 시 무조건 `verified` 자격증명만 반환 → "근거 없는 스펙 기재 원천 차단"
- **Prism Mock 서버 세팅**: `openapi.yaml` 기반으로 즉석에서 Mock 데이터를 응답하는 서버 환경 구성

---

## 2. 엔드포인트 및 Mock 서버 응답 값

Prism Mock 서버(`localhost:4010`)는 `openapi.yaml`의 example 데이터를 반환합니다.
VC 서버(`localhost:4000`)의 실제 fixture 데이터를 기준으로 작성되어 있어, 연동 시 응답 구조가 동일합니다.

### ① `GET /health` — 서버 상태 확인

```json
{
  "status": "ok",
  "mode": "fixture",
  "recordCount": 9
}
```

### ② `GET /v1/subjects/{did}/credentials` — 자격증명 목록 요약 조회

특정 학생의 **검증된(verified) 자격증명 리스트**를 요약 반환합니다.
FE 이력서 뷰와 AI 컨텍스트 구성에 사용합니다.

```bash
# 예시 요청
GET /v1/subjects/did:fixture:student-001/credentials
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
    },
    {
      "record_id": "a1b2c3d4-e5f6-5a7b-8c9d-0e1f2a3b4c5d",
      "format": "OB2.0",
      "achievement_name": "데이터베이스 설계 인증",
      "issuer_name": "ACME University",
      "status": "verified",
      "issued_at": "2026-05-15T00:00:00Z"
    },
    {
      "record_id": "b2c3d4e5-f6a7-5b8c-9d0e-1f2a3b4c5d6e",
      "format": "OB3.0",
      "achievement_name": "머신러닝 프로젝트 수료",
      "issuer_name": "Globex Academy",
      "status": "verified",
      "issued_at": "2026-06-01T00:00:00Z"
    },
    {
      "record_id": "c3d4e5f6-a7b8-5c9d-0e1f-2a3b4c5d6e7f",
      "format": "OB3.0",
      "achievement_name": "블록체인 응용 캡스톤",
      "issuer_name": "ACME University",
      "status": "verified",
      "issued_at": "2026-04-20T00:00:00Z"
    }
  ]
}
```

### ③ `GET /v1/subjects/{did}/skills` — 보유 역량(Skill) 추출

**검증된 자격증명에서만** NCS/ESCO 규격으로 정규화된 스킬 목록을 반환합니다.
AI 갭(Gap) 분석의 "보유 역량" 입력으로 사용합니다.

```json
{
  "subject_did": "did:fixture:student-001",
  "count": 4,
  "skills": [
    {
      "skill_id": "SK.DEV.BACKEND",
      "label_ko": "백엔드 개발",
      "label_en": "Backend Development",
      "taxonomy": { "source": "NCS", "code": "2001010401", "version": "2024" },
      "level": null,
      "level_basis": "completion",
      "confidence": 1,
      "mapping_method": "alias_dict",
      "source_record_ids": ["3f444a85-191b-51a4-83a6-73fad567c272"]
    },
    {
      "skill_id": "SK.DEV.DATABASE",
      "label_ko": "데이터베이스 설계",
      "label_en": "Database Design",
      "taxonomy": { "source": "NCS", "code": "2001010305", "version": "2024" },
      "level": null,
      "level_basis": "completion",
      "confidence": 1,
      "mapping_method": "alias_dict",
      "source_record_ids": ["a1b2c3d4-e5f6-5a7b-8c9d-0e1f2a3b4c5d"]
    },
    {
      "skill_id": "SK.AI.MACHINE_LEARNING",
      "label_ko": "머신러닝",
      "label_en": "Machine Learning",
      "taxonomy": { "source": "ESCO", "code": "S1.2.1", "version": "1.2.0" },
      "level": null,
      "level_basis": "completion",
      "confidence": 1,
      "mapping_method": "alias_dict",
      "source_record_ids": ["b2c3d4e5-f6a7-5b8c-9d0e-1f2a3b4c5d6e"]
    },
    {
      "skill_id": "SK.SEC.BLOCKCHAIN",
      "label_ko": "블록체인 응용",
      "label_en": "Blockchain Application",
      "taxonomy": { "source": "NCS", "code": "2003020104", "version": "2024" },
      "level": null,
      "level_basis": "completion",
      "confidence": 1,
      "mapping_method": "alias_dict",
      "source_record_ids": ["c3d4e5f6-a7b8-5c9d-0e1f-2a3b4c5d6e7f"]
    }
  ]
}
```

### ④ `GET /v1/records/{id}/verification` — 7대 검증 내역 조회

이 자격증명이 왜 유효한지 **7가지 검사 근거**를 반환합니다.
AI의 "근거 있는 답변"과 기업용 원클릭 검증 뷰에 사용합니다.

```bash
# 예시 요청
GET /v1/records/3f444a85-191b-51a4-83a6-73fad567c272/verification
```

```json
{
  "status": "verified",
  "checks": [
    { "check": "schema",       "result": "pass", "checked_at": "2026-09-28T11:00:00.000Z" },
    { "check": "did_resolution","result": "pass", "checked_at": "2026-09-28T11:00:00.000Z" },
    { "check": "signature",    "result": "skip", "detail": "OB2.0 Hosted 방식은 서명 없음", "checked_at": "2026-09-28T11:00:00.000Z" },
    { "check": "issuer_trust", "result": "pass", "checked_at": "2026-09-28T11:00:00.000Z" },
    { "check": "revocation",   "result": "pass", "checked_at": "2026-09-28T11:00:00.000Z" },
    { "check": "anchor_match", "result": "skip", "detail": "DX Ledger 미연동 (논문 기간 skip)", "checked_at": "2026-09-28T11:00:00.000Z" },
    { "check": "expiration",   "result": "pass", "checked_at": "2026-09-28T11:00:00.000Z" }
  ],
  "proof": { "type": "HostedVerification" },
  "verified_at": "2026-09-28T11:00:00.000Z",
  "verifier_version": "vc-core-mock/0.1.0"
}
```

### ⑤ `GET /v1/records/{id}` — 단일 자격증명 전체 조회

record_id로 단일 CredentialRecord 전체(스킬, 근거, 검증 결과 포함)를 반환합니다.

### ⑥ `POST /v1/verify` — 즉시 재검증

```bash
POST /v1/verify
Content-Type: application/json

{ "external_id": "cred-ob2-hosted-001" }
```

파이프라인을 즉시 재실행하여 재검증된 `CredentialRecord` 전체를 반환합니다.
AI MCP `wallet.verify_credential` 툴이 호출하는 엔드포인트입니다.

---

## 3. 실행 명령어 (터미널)

### 패키지 설치 (최초 1회)
```bash
cd BE
npm install
```

### Mock 서버 실행 (논문 기간)
```bash
npm run mock
# → http://localhost:4010 에서 Prism Mock 서버 기동
# 터미널에 "Prism is listening on http://127.0.0.1:4010" 출력되면 정상
```

### 실서버 실행 (논문 이후 — NestJS 구현 후)
```bash
npm run start:dev
# → http://localhost:3000 에서 NestJS 개발 서버 기동
```

---

## 4. 논문 이후 구현 예정 (Phase 2~)

- NestJS + TypeScript 기반 실 서버 구축
- VC 지갑 서버(`localhost:4000`) HTTP 직접 호출 (Axios)
- PostgreSQL + Prisma 연동 (이력서, 사용자 정보 등 BE 자체 데이터)
- **BFF 패턴**: VC 지갑 데이터 + BE DB 데이터를 서버에서 조립하여 FE/AI에게 단일 응답으로 제공
- JWT 기반 인증/인가

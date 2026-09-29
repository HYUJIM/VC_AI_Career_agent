# BE (Back-End) 파트

디지털 배지 및 자격증명 기반 초개인화 AI 커리어 에이전트 지갑 개발의 백엔드 파트 폴더입니다.

---

## 1. 현재 구현된 내용 (Phase 0~1 논문 기간용)

현재 실제 비즈니스 로직(NestJS, DB 연결 등) 구현 전, AI 및 FE 팀이 개발을 즉시 진행할 수 있도록 **Mock API 환경**을 최우선으로 구축했습니다.

- **OpenAPI 3.1.0 기반 명세서 (`openapi.yaml`) 작성 완료**: 백엔드에서 제공할 6개의 핵심 엔드포인트를 명세화했습니다.
- **VC 스키마 100% 통합**: 데이터 이중 정의 및 불일치를 막기 위해, VC(지갑 코어) 파트의 JSON Schema (`CredentialRecord`, `VerificationResult`, `SkillClaim` 등)를 그대로 반영했습니다.
- **기본값 강제 정책**: "근거 없는 스펙 기재 원천 차단"을 위해 API 호출 시 파라미터가 없으면 무조건 `verified`(검증 통과)된 자격증명과 역량만 반환되도록 엔드포인트를 설계했습니다.
- **Prism Mock 서버 세팅**: `openapi.yaml`을 읽고 즉석에서 가짜(Mock) 데이터를 응답해 주는 서버 환경이 구성되어 있습니다.

---

## 2. Mock 서버 주요 응답 값 (Postman 시나리오)

Prism Mock 서버는 `openapi.yaml`에 정의된 예시(example) 데이터를 반환합니다.

### ① 자격증명 목록 요약 조회
`GET /v1/subjects/did:fixture:student-001/credentials`
특정 학생의 **검증된 자격증명 리스트**를 요약해서 반환합니다. (FE 이력서 뷰, AI 컨텍스트 용도)

```json
{
  "subject_did": "did:fixture:student-001",
  "status_filter": [
    "verified"
  ],
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
    // ... 총 4개 기록
  ]
}
```

### ② 보유 역량(Skill) 추출
`GET /v1/subjects/did:fixture:student-001/skills`
해당 학생의 자격증명을 바탕으로 **NCS/ESCO 규격으로 정규화된 스킬 목록**을 반환합니다. (AI 갭 분석 용도)

```json
{
  "subject_did": "did:fixture:student-001",
  "count": 4,
  "skills": [
    {
      "skill_id": "SK.DEV.BACKEND",
      "label_ko": "백엔드 개발",
      "label_en": "Backend Development",
      "taxonomy": {
        "source": "NCS",
        "code": "2001010401",
        "version": "2024"
      },
      "level": null,
      "level_basis": "completion",
      "confidence": 1,
      "mapping_method": "alias_dict",
      "source_record_ids": [
        "3f444a85-191b-51a4-83a6-73fad567c272"
      ]
    }
  ]
}
```

### ③ 단일 자격증명 검증 내역 조회
`GET /v1/records/3f444a85-191b-51a4-83a6-73fad567c272/verification`
이 자격증명이 왜 진짜인지, 어떤 검사를 거쳤는지 **7가지 검사 내역**을 상세히 반환합니다.

```json
{
  "status": "verified",
  "checks": [
    {
      "check": "schema",
      "result": "pass",
      "checked_at": "2026-09-28T11:00:00.000Z"
    },
    {
      "check": "signature",
      "result": "skip",
      "detail": "OB2.0 Hosted 방식은 서명 없음",
      "checked_at": "2026-09-28T11:00:00.000Z"
    }
    // ... did_resolution, issuer_trust, revocation, anchor_match, expiration 등 총 7개
  ],
  "proof": {
    "type": "HostedVerification"
  },
  "verified_at": "2026-09-28T11:00:00.000Z",
  "verifier_version": "vc-core-mock/0.1.0"
}
```

---

## 3. 실행 명령어 (터미널)

개발을 시작하거나 API 테스트를 하려면 아래 명령어를 통해 Mock 서버를 실행하세요.

### 패키지 설치 (최초 1회)
```bash
cd BE
npm install
```

### Mock 서버 실행
```bash
npm run mock
```
> 터미널에 `Prism is listening on http://127.0.0.1:4010` 문구가 출력되면 정상입니다. Postman이나 브라우저를 통해 `http://localhost:4010/health`로 요청을 보내 테스트해 볼 수 있습니다.

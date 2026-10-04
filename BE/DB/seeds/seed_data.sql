-- ====================================================================
-- KNU_BaaS_Starter 기반 실전 데모 시드 데이터
-- 경북대학교(knu) BaaS 연동 라이브 데이터 및 템플릿 기반
-- ====================================================================

-- 1. 데모 학생 계정 (KNU-DEMO-001)
INSERT INTO users (id, email, name, subject_did)
VALUES (
    '11111111-1111-1111-1111-111111111111',
    'knu-demo-001@knu.ac.kr',
    '가상학생',
    'did:key:z6Mki5XXtvVMjCnPSmq9TWuLLJpfNp1JrSYVKSCr9minyApQ'
) ON CONFLICT (id) DO NOTHING;

-- 2. 자격증명 (KNU BaaS 실발급 템플릿 및 검증 결과 기반)
-- 2-1. 학생 신원 자격증명 (Identity SD-JWT-VC: verified)
INSERT INTO credentials (
    record_id, user_id, source_provider, source_external_id, format,
    achievement_name, issuer_name, issuer_trust_level, status,
    issued_at, awarded_date, raw_data
) VALUES (
    '6067383f-5c28-4f8e-a629-ea4593853f0d',
    '11111111-1111-1111-1111-111111111111',
    'dx_ledger_wallet',
    'knu-id-6067383f',
    'VC2.0',
    'KNU 학생 신원 인증',
    '경북대학교',
    'accredited',
    'verified',
    '2026-10-02T03:34:21Z',
    '2026-10-02',
    '{
        "schema_version": "0.1.0",
        "format": "VC2.0",
        "issuer": { "did": "https://api.dxworks.kr/issuers/knu", "name": "경북대학교", "trust_level": "accredited" },
        "achievement": {
            "id": "1b62af8a-b2d1-47ba-852f-4ed41419755e",
            "name": "KNU 학생 신원 인증",
            "description": "경북대학교 학사정보시스템 연동 가상학생 신원 자격증명"
        },
        "claims": {
            "name": "가상학생",
            "memberId": "KNU-DEMO-001",
            "role": "Student"
        }
    }'::jsonb
) ON CONFLICT (record_id) DO NOTHING;

-- 2-2. KNU 프로젝트 테스트 수료 배지 (OB3.0: verified)
INSERT INTO credentials (
    record_id, user_id, source_provider, source_external_id, format,
    achievement_name, issuer_name, issuer_trust_level, status,
    issued_at, awarded_date, raw_data
) VALUES (
    'e78075f8-67c1-47ef-bd2f-c52ed58a816d',
    '11111111-1111-1111-1111-111111111111',
    'dx_ledger_badge',
    'knu-badge-e78075f8',
    'OB3.0',
    'KNU 프로젝트 테스트 수료 배지',
    '경북대학교',
    'accredited',
    'verified',
    '2026-10-02T03:34:22Z',
    '2026-10-02',
    '{
        "schema_version": "0.1.0",
        "format": "OB3.0",
        "issuer": { "did": "https://api.dxworks.kr/issuers/knu", "name": "경북대학교", "trust_level": "accredited" },
        "achievement": {
            "id": "f9499c5f-6611-45c6-b5a8-683a99040aab",
            "name": "KNU 프로젝트 테스트 수료 배지",
            "description": "산학협력 프로젝트용 가상 교육 수료 배지입니다. OID4VP 프로토콜 구현 실습 완료.",
            "criteria_narrative": "테스트 과정의 실습을 완료한 가상 사용자에게 발급합니다. 키 바인딩 서명 검증 테스트 통과."
        },
        "skills": [
            { "skill_id": "SK.DEV.BACKEND", "label_ko": "백엔드 개발", "confidence": 1.0 },
            { "skill_id": "SK.SEC.BLOCKCHAIN", "label_ko": "블록체인 응용", "confidence": 1.0 }
        ]
    }'::jsonb
) ON CONFLICT (record_id) DO NOTHING;

-- 2-3. AI 커리어 에이전트 종합설계프로젝트 (OB3.0: verified)
INSERT INTO credentials (
    record_id, user_id, source_provider, source_external_id, format,
    achievement_name, issuer_name, issuer_trust_level, status,
    issued_at, awarded_date, raw_data
) VALUES (
    'c3d4e5f6-a7b8-5c9d-0e1f-2a3b4c5d6e7f',
    '11111111-1111-1111-1111-111111111111',
    'dx_ledger_badge',
    'knu-badge-c3d4e5f6',
    'OB3.0',
    'AI 커리어 에이전트 종합설계프로젝트',
    '경북대학교 IT대학',
    'accredited',
    'verified',
    '2026-10-01T09:00:00Z',
    '2026-10-01',
    '{
        "schema_version": "0.1.0",
        "format": "OB3.0",
        "issuer": { "did": "https://api.dxworks.kr/issuers/knu", "name": "경북대학교 IT대학", "trust_level": "accredited" },
        "achievement": {
            "id": "knu-capstone-ai-2026",
            "name": "AI 커리어 에이전트 종합설계프로젝트",
            "description": "디지털 배지 및 자격증명 기반 초개인화 AI 커리어 에이전트 지갑 개발 캡스톤 프로젝트 수행",
            "criteria_narrative": "PostgreSQL, pgvector 기반 RAG 파이프라인 구축 및 LLM 포트폴리오 생성 에이전트 개발 완료"
        },
        "skills": [
            { "skill_id": "SK.DEV.BACKEND", "label_ko": "백엔드 개발", "confidence": 1.0 },
            { "skill_id": "SK.AI.MACHINE_LEARNING", "label_ko": "머신러닝", "confidence": 1.0 }
        ]
    }'::jsonb
) ON CONFLICT (record_id) DO NOTHING;

-- 2-4. 폐기(REVOKED)된 테스트 배지 (허위 스펙 차단 테스트용)
INSERT INTO credentials (
    record_id, user_id, source_provider, source_external_id, format,
    achievement_name, issuer_name, issuer_trust_level, status,
    issued_at, awarded_date, raw_data
) VALUES (
    'acdb326e-6b1f-4e75-bec9-e2348ff8f2ff',
    '11111111-1111-1111-1111-111111111111',
    'dx_ledger_badge',
    'knu-badge-acdb326e',
    'OB3.0',
    '폐기된 테스트 배지',
    '경북대학교',
    'known',
    'revoked', -- ★ 폐기됨
    '2026-09-01T00:00:00Z',
    '2026-09-01',
    '{
        "achievement": { "name": "폐기된 테스트 배지", "description": "발급기관에 의해 폐기 처리된 자격증명" },
        "verification": { "status": "revoked" }
    }'::jsonb
) ON CONFLICT (record_id) DO NOTHING;

-- 3. 7대 검증 체크 결과 (KNU BaaS 실검증 트레이스 기반)
INSERT INTO verification_checks (record_id, check_name, result, detail, checked_at) VALUES
-- 수료 배지(e78075f8) 7대 체크: 전부 pass/skip
('e78075f8-67c1-47ef-bd2f-c52ed58a816d', 'schema', 'pass', 'W3C VC / OB3.0 스키마 검증 통과', NOW()),
('e78075f8-67c1-47ef-bd2f-c52ed58a816d', 'did_resolution', 'pass', '경북대학교 DID 해석 성공 (https://api.dxworks.kr/issuers/knu)', NOW()),
('e78075f8-67c1-47ef-bd2f-c52ed58a816d', 'signature', 'pass', 'Ed25519 전자서명 유효', NOW()),
('e78075f8-67c1-47ef-bd2f-c52ed58a816d', 'issuer_trust', 'pass', '테넌트 knu 공인 발급자 확인 (trusted=true)', NOW()),
('e78075f8-67c1-47ef-bd2f-c52ed58a816d', 'revocation', 'pass', 'StatusList 폐기 목록 미등재 (VALID)', NOW()),
('e78075f8-67c1-47ef-bd2f-c52ed58a816d', 'anchor_match', 'skip', 'BaaS 1-shot 모드 (anchor skip)', NOW()),
('e78075f8-67c1-47ef-bd2f-c52ed58a816d', 'expiration', 'pass', '유효기간 이내 정상 배지', NOW()),

-- 폐기 배지(acdb326e) 7대 체크: revocation = fail
('acdb326e-6b1f-4e75-bec9-e2348ff8f2ff', 'schema', 'pass', '스키마 통과', NOW()),
('acdb326e-6b1f-4e75-bec9-e2348ff8f2ff', 'did_resolution', 'pass', 'DID 해석 통과', NOW()),
('acdb326e-6b1f-4e75-bec9-e2348ff8f2ff', 'signature', 'pass', '서명 유효', NOW()),
('acdb326e-6b1f-4e75-bec9-e2348ff8f2ff', 'issuer_trust', 'pass', '발급자 확인', NOW()),
('acdb326e-6b1f-4e75-bec9-e2348ff8f2ff', 'revocation', 'fail', '발급기관 폐기 목록(StatusList)에 등록됨 (REVOKED)', NOW()),
('acdb326e-6b1f-4e75-bec9-e2348ff8f2ff', 'anchor_match', 'skip', 'skip', NOW()),
('acdb326e-6b1f-4e75-bec9-e2348ff8f2ff', 'expiration', 'pass', '만료일 정상', NOW())
ON CONFLICT (record_id, check_name) DO UPDATE SET result = EXCLUDED.result, detail = EXCLUDED.detail;

-- 4. 학생 보유 역량 캐시 (user_skills)
INSERT INTO user_skills (user_id, skill_id, label_ko, label_en, taxonomy_source, taxonomy_code, level, confidence, source_record_ids)
VALUES
(
    '11111111-1111-1111-1111-111111111111',
    'SK.DEV.BACKEND',
    '백엔드 개발',
    'Backend Development',
    'NCS',
    '2001010401',
    4,
    1.0,
    ARRAY['e78075f8-67c1-47ef-bd2f-c52ed58a816d'::uuid, 'c3d4e5f6-a7b8-5c9d-0e1f-2a3b4c5d6e7f'::uuid]
),
(
    '11111111-1111-1111-1111-111111111111',
    'SK.SEC.BLOCKCHAIN',
    '블록체인 응용',
    'Blockchain Application',
    'NCS',
    '2003020104',
    3,
    1.0,
    ARRAY['e78075f8-67c1-47ef-bd2f-c52ed58a816d'::uuid]
),
(
    '11111111-1111-1111-1111-111111111111',
    'SK.AI.MACHINE_LEARNING',
    '머신러닝',
    'Machine Learning',
    'ESCO',
    'S1.2.1',
    3,
    1.0,
    ARRAY['c3d4e5f6-a7b8-5c9d-0e1f-2a3b4c5d6e7f'::uuid]
)
ON CONFLICT (user_id, skill_id) DO NOTHING;

-- 5. pgvector 임베딩 샘플 (1536차원 단위 벡터 생성)
INSERT INTO student_credential_embeddings (record_id, user_id, content_text, source_text_hash, embedding)
SELECT 
    'e78075f8-67c1-47ef-bd2f-c52ed58a816d'::uuid,
    '11111111-1111-1111-1111-111111111111'::uuid,
    '[배지명] KNU 프로젝트 테스트 수료 배지 [발급기관] 경북대학교 [설명] 산학협력 프로젝트용 가상 교육 수료 배지입니다. OID4VP 프로토콜 구현 실습 완료. [평가기준] 키 바인딩 서명 검증 테스트 통과. [역량] 백엔드 개발, 블록체인 응용',
    md5('KNU 프로젝트 테스트 수료 배지'),
    (SELECT array_agg(0.02)::vector(1536) FROM generate_series(1, 1536))
ON CONFLICT (record_id) DO NOTHING;

INSERT INTO student_credential_embeddings (record_id, user_id, content_text, source_text_hash, embedding)
SELECT 
    'c3d4e5f6-a7b8-5c9d-0e1f-2a3b4c5d6e7f'::uuid,
    '11111111-1111-1111-1111-111111111111'::uuid,
    '[배지명] AI 커리어 에이전트 종합설계프로젝트 [발급기관] 경북대학교 IT대학 [설명] 디지털 배지 기반 초개인화 AI 커리어 에이전트 지갑 개발. [평가기준] PostgreSQL, pgvector RAG 파이프라인 구축 및 LLM 포트폴리오 생성 완료. [역량] 백엔드 개발, 머신러닝',
    md5('AI 커리어 에이전트 종합설계프로젝트'),
    (SELECT array_agg(0.03)::vector(1536) FROM generate_series(1, 1536))
ON CONFLICT (record_id) DO NOTHING;

-- 6. AI 생성 포트폴리오 샘플
INSERT INTO portfolios (
    id, user_id, title, target_role, bio_summary,
    content_markdown, referenced_record_ids, status
) VALUES (
    'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
    '11111111-1111-1111-1111-111111111111',
    '경북대학교 2026 하반기 백엔드 & 분산시스템 엔지니어 포트폴리오',
    '백엔드 엔지니어',
    '경북대학교에서 W3C DID/VC 기반 분산지갑 인프라와 AI RAG 파이프라인을 구축한 백엔드 개발자입니다.',
    '# 가상학생 포트폴리오

## 1. 소개
경북대학교 학부생으로, 검증된 분산 자격증명(VC) 인프라와 AI RAG 파이프라인을 설계하고 구축했습니다.

## 2. 검증된 프로젝트 경험
### [AI 커리어 에이전트 지갑 개발] (경북대학교 IT대학 공식 인증)
- **근거 자격증명**: `c3d4e5f6-a7b8-5c9d-0e1f-2a3b4c5d6e7f`
- **사용 기술**: PostgreSQL, pgvector, NestJS, OID4VP
- **성과**: 7대 검증 체크 파이프라인 구축 및 LLM RAG 기반 포트폴리오 자동화 달성

### [KNU BaaS OID4VP 분산 지갑 실습] (경북대학교 공식 인증)
- **근거 자격증명**: `e78075f8-67c1-47ef-bd2f-c52ed58a816d`
- **사용 기술**: Ed25519, did:key, Key-Binding JWT
- **성과**: 1-shot 프로토콜을 통한 위조 및 폐기 배지 거절 테스트 100% 통과
',
    ARRAY['c3d4e5f6-a7b8-5c9d-0e1f-2a3b4c5d6e7f'::uuid, 'e78075f8-67c1-47ef-bd2f-c52ed58a816d'::uuid],
    'published'
) ON CONFLICT (id) DO NOTHING;

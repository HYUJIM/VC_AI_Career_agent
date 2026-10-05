-- PostgreSQL 스키마

CREATE EXTENSION IF NOT EXISTS "pgcrypto"; 
-- ====================================================================
-- 0. ENUM 타입 정의
-- ====================================================================
CREATE TYPE credential_format   AS ENUM ('OB2.0', 'OB3.0', 'VC1.1', 'VC2.0');
CREATE TYPE verification_status AS ENUM ('verified', 'invalid', 'revoked', 'expired', 'unverifiable', 'pending');
CREATE TYPE check_result        AS ENUM ('pass', 'fail', 'warn', 'skip');
CREATE TYPE check_name_type     AS ENUM ('schema', 'did_resolution', 'signature', 'issuer_trust', 'revocation', 'anchor_match', 'expiration');
-- ====================================================================
-- 1. 사용자 (학생/구직자)
-- ====================================================================
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    name            VARCHAR(100) NOT NULL,
    subject_did     VARCHAR(255) UNIQUE NOT NULL, -- 지갑 소유자 식별자 (did:fixture:student-001)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_users_did ON users(subject_did);
-- ====================================================================
-- 2. 자격증명
-- ====================================================================
CREATE TABLE credentials (
    record_id           UUID PRIMARY KEY,               -- VC 코어 결정적 UUID v5
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_provider     VARCHAR(50) NOT NULL,           -- dx_ledger_badge, hosted_url 등
    source_external_id  VARCHAR(255) NOT NULL,          -- BaaS 원본 ID (재검증 엔드포인트 호출용)
    format              credential_format NOT NULL,
    achievement_name    VARCHAR(255) NOT NULL,          -- 배지/자격명
    issuer_name         VARCHAR(255) NOT NULL,          -- 발급 기관명
    issuer_trust_level  VARCHAR(50) DEFAULT 'unknown',  -- accredited, known, unknown
    status              verification_status NOT NULL,   -- verified, revoked, expired 등
    issued_at           TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ,
    awarded_date        DATE,
    raw_data            JSONB NOT NULL,                 -- 원본 CredentialRecord JSON 전문
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, source_provider, source_external_id) -- 동일 배지 중복 적재 차단
);
CREATE INDEX idx_credentials_user ON credentials(user_id);
CREATE INDEX idx_credentials_status ON credentials(status);
-- ====================================================================
-- 3. 7대 검증 체크 상세 감사로그 (복합 PK로 정합성 보장)
-- ====================================================================
CREATE TABLE verification_checks (
    record_id   UUID NOT NULL REFERENCES credentials(record_id) ON DELETE CASCADE,
    check_name  check_name_type NOT NULL, -- schema, signature, revocation 등 7개
    result      check_result NOT NULL,    -- pass, fail, warn, skip
    detail      TEXT,                     -- 실패/스킵 사유 (예: "폐기 목록에 등록된 배지")
    checked_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, check_name)   -- ★ 1개 자격증명당 체크항목별 1건만 존재 강제
);
-- ====================================================================
-- 4. 역량 (VC 코어가 집계한 스킬 목록)
-- ====================================================================
CREATE TABLE user_skills (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    skill_id            VARCHAR(100) NOT NULL,          -- "SK.DEV.BACKEND"
    label_ko            VARCHAR(100) NOT NULL,          -- "백엔드 개발"
    label_en            VARCHAR(100),
    taxonomy_source     VARCHAR(50),                    -- NCS, ESCO 등
    taxonomy_code       VARCHAR(50),
    level               NUMERIC,
    confidence          NUMERIC DEFAULT 1.0,
    source_record_ids   UUID[] NOT NULL DEFAULT '{}',   -- 이 역량의 근거가 된 record_id 목록
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, skill_id)
);
CREATE INDEX idx_user_skills_user ON user_skills(user_id);
CREATE INDEX idx_user_skills_sources ON user_skills USING gin(source_record_ids); -- GIN 인덱스
-- ====================================================================
-- 5. 포트폴리오 (AI 작성본 학생 편집본)
-- ====================================================================
CREATE TABLE portfolios (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title                   VARCHAR(255) NOT NULL,          -- 포트폴리오 제목
    target_role             VARCHAR(100),                   -- 희망 직무
    bio_summary             TEXT,                           -- AI 작성 한 줄 소개/요약
    content_markdown        TEXT NOT NULL,                  -- 프론트 렌더링/수정용 마크다운 본문
    sections                JSONB,                          -- 프로젝트별 구조화된 데이터
    referenced_record_ids   UUID[] DEFAULT '{}',            -- 포트폴리오에 인용된 검증 배지 ID 목록
    status                  VARCHAR(20) DEFAULT 'draft',    -- draft(작성중), published(완성)
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_portfolios_user ON portfolios(user_id);

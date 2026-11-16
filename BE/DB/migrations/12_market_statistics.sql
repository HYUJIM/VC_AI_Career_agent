-- 직무별 채용 시장 통계 (공고 수집 → AI 집계 → 스냅샷)
--
-- market_postings : 수집한 공고의 구간별 텍스트 (기술스택·주요업무·자격요건·우대사항). 통계 재계산용 내부 자료라
--                   API 응답으로는 내보내지 않는다. 한 공고가 여러 직무 분류에 속할 수 있어 role_ids는 배열이다.
-- market_snapshots: AI 서비스가 계산한 직무별 통계. 덮어쓰지 않고 쌓으며, 조회는 직무별 최신 한 건을 쓴다.
--
-- 새로 띄우는 DB: docker-compose가 initdb 단계에서 자동 실행한다.
-- 이미 볼륨이 있는 DB: python -m app.migrate (여러 번 실행해도 안전).

CREATE TABLE IF NOT EXISTS market_postings (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source                VARCHAR(50) NOT NULL,            -- 예: 'jumpit'
    external_id           VARCHAR(255) NOT NULL,           -- 출처의 공고 번호
    role_ids              TEXT[] NOT NULL,                 -- 예: {backend_developer,infra_engineer}
    source_url            TEXT NOT NULL,
    company               VARCHAR(200) NOT NULL,
    title                 VARCHAR(255) NOT NULL,
    experience_min_years  SMALLINT,
    experience_max_years  SMALLINT,
    closes_at             TIMESTAMPTZ,
    sections              JSONB NOT NULL,                  -- {tech_stack, responsibilities, qualifications, preferred}
    content_hash          CHAR(64) NOT NULL,
    rights_basis          VARCHAR(40) NOT NULL,            -- permission / public_api_license / research_authorization
    captured_at           TIMESTAMPTZ NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS idx_market_postings_roles ON market_postings USING GIN (role_ids);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id       VARCHAR(40) NOT NULL,
    as_of         TIMESTAMPTZ NOT NULL,
    sample_size   INTEGER NOT NULL,
    snapshot      JSONB NOT NULL,                          -- MarketSnapshot 전문 (원문 없음)
    content_hash  CHAR(64) NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (role_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_latest ON market_snapshots(role_id, as_of DESC, created_at DESC);

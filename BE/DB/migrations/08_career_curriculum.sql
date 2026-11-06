-- 직무 비교·커리큘럼 (AI 서비스 연동)
--
-- 공고·학습자료는 연구자가 원문 인용과 함께 반입한 버전을 그대로 보존한다(덮어쓰지 않음).
-- 분석·커리큘럼은 AI 결과와 그 근거(재검증 스냅샷, 공고 버전)를 함께 저장해 나중에 재현할 수 있게 한다.
-- content_hash는 RFC 8785 정규화 JSON의 SHA-256이며 AI 서비스도 같은 값을 다시 계산해 대조한다.
--
-- 새로 띄우는 DB: docker-compose가 initdb 단계에서 자동 실행한다.
-- 이미 볼륨이 있는 DB: 아래 명령으로 한 번만 직접 실행한다 (여러 번 실행해도 안전).
--   docker compose exec -T postgres psql -U postgres -d vc_career_agent < DB/migrations/08_career_curriculum.sql

CREATE TABLE IF NOT EXISTS job_postings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source              VARCHAR(50) NOT NULL,
    external_id         VARCHAR(255) NOT NULL,
    latest_version_id   UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS job_posting_versions (
    id              UUID PRIMARY KEY,
    posting_id      UUID NOT NULL REFERENCES job_postings(id) ON DELETE CASCADE,
    payload         JSONB NOT NULL,                 -- JobVersion 전문 (원문·주석 포함)
    content_hash    CHAR(64) NOT NULL,
    imported_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_job_posting_versions_posting ON job_posting_versions(posting_id);

CREATE TABLE IF NOT EXISTS learning_resources (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source              VARCHAR(50) NOT NULL,
    external_id         VARCHAR(255) NOT NULL,
    latest_version_id   UUID,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, external_id)
);

CREATE TABLE IF NOT EXISTS learning_resource_versions (
    id              UUID PRIMARY KEY,
    resource_id     UUID NOT NULL REFERENCES learning_resources(id) ON DELETE CASCADE,
    payload         JSONB NOT NULL,                 -- LearningResourceVersion 전문
    content_hash    CHAR(64) NOT NULL,
    imported_by     UUID REFERENCES users(id) ON DELETE SET NULL,
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_learning_resource_versions_resource ON learning_resource_versions(resource_id);

CREATE TABLE IF NOT EXISTS career_analyses (
    id          UUID PRIMARY KEY,                   -- = analysis.analysis_id
    user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    analysis    JSONB NOT NULL,                     -- AI가 만든 CareerAnalysis (BE 재검사 통과본)
    snapshot    JSONB NOT NULL,                     -- 분석에 쓴 실행 시점 재검증 스냅샷
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_career_analyses_user ON career_analyses(user_id);

CREATE TABLE IF NOT EXISTS curricula (
    id                  UUID PRIMARY KEY,           -- = result.job_id
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    analysis_id         UUID NOT NULL REFERENCES career_analyses(id) ON DELETE CASCADE,
    idempotency_key     UUID NOT NULL,
    request             JSONB NOT NULL,
    result              JSONB NOT NULL,             -- CurriculumResult
    audit               JSONB NOT NULL DEFAULT '[]',-- 모델 호출 요약 (원문 출력은 저장하지 않음)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_curricula_user ON curricula(user_id);

-- 선택 공개 링크 (학생이 고른 자격증명만 기업에 보여 주는 링크)
--
-- 링크 토큰 원문은 저장하지 않고 SHA-256 해시만 저장한다 (DB가 새어도 링크를 만들 수 없게).
--
-- 새로 띄우는 DB: docker-compose가 initdb 단계에서 자동 실행한다.
-- 이미 볼륨이 있는 DB·RDS: docker compose의 migrate 서비스나 `python -m app.migrate`가 원장(schema_migrations)에
-- 없을 때 한 번 적용한다 (여러 번 실행해도 안전). 적용한 뒤에는 SQL을 고치지 말고 다음 번호 파일을 추가한다.

CREATE TABLE IF NOT EXISTS shares (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      CHAR(64) UNIQUE NOT NULL,          -- sha256(token) hex
    record_ids      UUID[] NOT NULL,                   -- 공개할 자격증명 (credentials.record_id)
    label           VARCHAR(100),                      -- 학생용 이름 (기업 화면에는 노출하지 않음)
    expires_at      TIMESTAMPTZ NOT NULL,
    revoked_at      TIMESTAMPTZ,
    view_count      INTEGER NOT NULL DEFAULT 0,
    last_viewed_at  TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_shares_user ON shares(user_id);

-- OAuth 소셜 로그인 연동 계정 (예: Google OAuth 2.0 / OIDC)
--
-- OAuth 사용자는 password_hash 없이도 가입·로그인할 수 있으며,
-- 제공자에서 인증된 이메일(email_verified=true)이 기존 사용자와 같으면 같은 계정에 연동된다.
--
-- 새로 띄우는 DB: docker-compose가 initdb 단계에서 자동 실행한다.
-- 이미 볼륨이 있는 DB: 아래 명령으로 한 번만 직접 실행한다 (여러 번 실행해도 안전).
--   docker compose exec -T postgres psql -U postgres -d vc_career_agent < DB/migrations/08_oauth_accounts.sql

CREATE TABLE IF NOT EXISTS oauth_accounts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider          VARCHAR(50) NOT NULL,            -- 예: 'google'
    provider_user_id  VARCHAR(255) NOT NULL,           -- 제공자 고유 식별자 (OIDC sub)
    email             VARCHAR(255) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider, provider_user_id),
    UNIQUE (user_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_oauth_accounts_user ON oauth_accounts(user_id);

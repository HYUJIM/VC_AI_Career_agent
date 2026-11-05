-- users.password_hash 추가 + 시드 테스트 계정 비밀번호 설정
--
-- 새로 띄우는 DB: docker-compose가 initdb 단계에서 자동 실행한다.
-- 이미 볼륨이 있는 DB: 아래 명령으로 한 번만 직접 실행한다 (여러 번 실행해도 안전).
--   docker compose exec -T postgres psql -U postgres -d vc_career_agent < DB/migrations/04_users_password_hash.sql

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);

-- 시드 테스트 계정(user001@example.com ~ user045@example.com)의 비밀번호를 'test1234'로 설정한다.
-- 이미 비밀번호가 있는 계정은 건드리지 않는다.
UPDATE users
SET password_hash = '$2b$12$NE/ahavIaowONbReQO8HeerLjtjlupCA.UjiFQM7Aw6YvIveWlYSS'
WHERE password_hash IS NULL
  AND email ~ '^user[0-9]{3}@example\.com$';

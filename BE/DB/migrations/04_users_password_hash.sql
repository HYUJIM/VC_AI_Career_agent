-- users.password_hash 추가 + 시드 테스트 계정 비밀번호 설정
--
-- 새로 띄우는 DB: docker-compose가 initdb 단계에서 자동 실행한다.
-- 이미 볼륨이 있는 DB·RDS: docker compose의 migrate 서비스나 `python -m app.migrate`가 원장(schema_migrations)에
-- 없을 때 한 번 적용한다 (여러 번 실행해도 안전). 적용한 뒤에는 SQL을 고치지 말고 다음 번호 파일을 추가한다.

ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255);

-- 시드 테스트 계정(user001@example.com ~ user045@example.com)의 비밀번호를 'test1234'로 설정한다.
-- 이미 비밀번호가 있는 계정은 건드리지 않는다.
UPDATE users
SET password_hash = '$2b$12$NE/ahavIaowONbReQO8HeerLjtjlupCA.UjiFQM7Aw6YvIveWlYSS'
WHERE password_hash IS NULL
  AND email ~ '^user[0-9]{3}@example\.com$';

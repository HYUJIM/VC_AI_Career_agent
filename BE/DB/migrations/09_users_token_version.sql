-- 로그인 세션 무효화 (서버 쪽 로그아웃)
--
-- 액세스 토큰(JWT)에 발급 당시의 token_version을 넣고, 요청마다 users.token_version과 같은지 확인한다.
-- 로그아웃하면 값을 1 올려 그 계정의 기존 토큰을 모든 탭·기기에서 한 번에 무효로 만든다.
-- 이 컬럼이 생기기 전에 받은 토큰(ver 없음)은 0으로 본다 (배포할 때 사용자가 로그아웃되지 않는다).
--
-- 새로 띄우는 DB: docker-compose가 initdb 단계에서 자동 실행한다.
-- 이미 볼륨이 있는 DB·RDS: docker compose의 migrate 서비스나 `python -m app.migrate`가 원장(schema_migrations)에
-- 없을 때 한 번 적용한다 (여러 번 실행해도 안전). 적용한 뒤에는 SQL을 고치지 말고 다음 번호 파일을 추가한다.

ALTER TABLE users ADD COLUMN IF NOT EXISTS token_version INTEGER NOT NULL DEFAULT 0;

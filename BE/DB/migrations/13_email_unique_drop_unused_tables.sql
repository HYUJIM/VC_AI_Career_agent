-- 이메일 유일성(대소문자 무시) + 쓰지 않는 인덱스·표 정리
--
-- 1) users.email의 UNIQUE는 대소문자를 구분하는데, 로그인과 Google 연동은 lower(email)로 계정을 찾는다.
--    "이메일 하나에 계정 하나"가 가입·OAuth 경로가 소문자로 바꿔 넣는 것에만 기대고 있었으므로 DB가 직접 보장하게 하고
--    (대소문자만 다른 이메일로는 두 번째 계정을 만들 수 없다), 그 조회가 순차 탐색 대신 이 인덱스를 타게 한다.
--    대소문자만 다른 이메일의 계정이 이미 있으면 인덱스를 만들 수 없으므로 먼저 확인해 멈춘다.
--    오류 문구에는 겹치는 수만 넣는다 (이메일 주소를 마이그레이션 로그에 남기지 않는다).
-- 2) idx_users_did는 users_subject_did_key(UNIQUE 제약의 인덱스)와 같은 열의 중복 인덱스라 지운다.
-- 3) student_credential_embeddings·user_skills는 만들기만 하고 어떤 코드도 읽거나 쓰지 않는 표다. 지운다
--    (안에 직접 넣어 둔 행이 있으면 함께 없어진다). 기본 스키마(DB/schema)도 이 두 표와 vector·pgcrypto 확장을 더 이상 만들지
--    않는다. 이미 설치된 확장은 그대로 둔다 (DROP EXTENSION은 하지 않는다).
--
-- 새로 띄우는 DB: docker-compose가 initdb 단계에서 자동 실행한다.
-- 이미 볼륨이 있는 DB·RDS: docker compose의 migrate 서비스나 `python -m app.migrate`가 원장(schema_migrations)에
-- 없을 때 한 번 적용한다 (여러 번 실행해도 안전). 적용한 뒤에는 SQL을 고치지 말고 다음 번호 파일을 추가한다.

DO $$
DECLARE
    duplicated BIGINT;
BEGIN
    SELECT count(*) INTO duplicated
    FROM (SELECT 1 FROM users GROUP BY lower(email) HAVING count(*) > 1) AS same_email;
    IF duplicated > 0 THEN
        RAISE EXCEPTION '대소문자만 다른 이메일을 쓰는 계정이 있어 users_email_lower_key 인덱스를 만들 수 없습니다 (겹치는 이메일 %개)', duplicated
            USING HINT = 'SELECT lower(email), count(*) FROM users GROUP BY 1 HAVING count(*) > 1; 로 찾아 이메일마다 계정을 하나만 남긴 뒤 다시 실행하세요.';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS users_email_lower_key ON users (lower(email));
DROP INDEX IF EXISTS idx_users_did;

DROP TABLE IF EXISTS student_credential_embeddings, user_skills;

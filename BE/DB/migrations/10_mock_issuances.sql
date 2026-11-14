-- 모의 발급 기록 (사용자별 하루 발급 한도)
--
-- 모의 발급은 회사 DXWorks 발급 키로 실제 서명된 배지를 만들므로 사용자마다 24시간 동안의 성공 건수를 제한한다.
-- 한도 검사는 사용자별 advisory 트랜잭션 잠금 안에서 세므로 여러 ECS 태스크·동시 요청에서도 넘치지 않는다.
-- token_sha256은 발급된 VC-JWT의 SHA-256(hex)이다 (원문은 저장하지 않는다).
--
-- 새로 띄우는 DB: docker-compose가 initdb 단계에서 자동 실행한다.
-- 이미 볼륨이 있는 DB·RDS: docker compose의 migrate 서비스나 `python -m app.migrate`가 원장(schema_migrations)에
-- 없을 때 한 번 적용한다 (여러 번 실행해도 안전). 적용한 뒤에는 SQL을 고치지 말고 다음 번호 파일을 추가한다.

CREATE TABLE IF NOT EXISTS mock_issuances (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    template_id     VARCHAR(255) NOT NULL,
    token_sha256    CHAR(64),                          -- 발급 성공 뒤에 채운다 (sha256(vc_jwt) hex)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mock_issuances_user_created ON mock_issuances(user_id, created_at);

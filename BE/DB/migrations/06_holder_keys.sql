-- 관리형(custodial) 홀더 키 보관 테이블
--
-- 새 가입자는 BE가 만든 Ed25519 키로 진짜 did:key를 받는다. 개인키는 AES-256-GCM 암호문으로만 저장한다.
-- 기존 사용자(가짜 DID)는 이 테이블에 행이 없다 (홀더 키 없음).
--
-- 새로 띄우는 DB: docker-compose가 initdb 단계에서 자동 실행한다.
-- 이미 볼륨이 있는 DB·RDS: docker compose의 migrate 서비스나 `python -m app.migrate`가 원장(schema_migrations)에
-- 없을 때 한 번 적용한다 (여러 번 실행해도 안전). 적용한 뒤에는 SQL을 고치지 말고 다음 번호 파일을 추가한다.

CREATE TABLE IF NOT EXISTS holder_keys (
    user_id                 UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    did                     VARCHAR(255) UNIQUE NOT NULL,      -- users.subject_did와 같은 값
    key_type                VARCHAR(20) NOT NULL DEFAULT 'Ed25519',
    public_key_multibase    VARCHAR(100) NOT NULL,
    encrypted_private_key   BYTEA NOT NULL,                    -- AES-256-GCM 암호문 (+16바이트 태그)
    nonce                   BYTEA NOT NULL,                    -- 12바이트
    kek_id                  VARCHAR(32) NOT NULL,              -- 암호화에 쓴 마스터 키 지문 (키 교체 대비)
    created_at              TIMESTAMPTZ DEFAULT NOW()
);

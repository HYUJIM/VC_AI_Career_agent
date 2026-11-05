-- 서명된 원본 자격증명(vcJwt) 보관 + 검증 점검 항목에 recipient 추가
--
-- 목적: 논문(RAG-VC)의 "실행 시점 재검증"을 하려면 DB 사본이 아니라 서명된 원본이 남아 있어야 한다.
--       raw_data(디코딩한 VC 본문)만으로는 서명을 다시 검증할 수 없다.
--
-- 새로 띄우는 DB: docker-compose가 initdb 단계에서 자동 실행한다.
-- 이미 볼륨이 있는 DB: 아래 명령으로 한 번만 직접 실행한다 (여러 번 실행해도 안전).
--   docker compose exec -T postgres psql -U postgres -d vc_career_agent < DB/migrations/05_credentials_raw_token.sql

ALTER TABLE credentials ADD COLUMN IF NOT EXISTS raw_token TEXT;              -- 서명된 원본 (compact JWS / VC-JWT). 시드 데이터는 NULL (재검증 불가)
ALTER TABLE credentials ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ; -- 마지막으로 검증기에 확인한 시각

ALTER TYPE check_name_type ADD VALUE IF NOT EXISTS 'recipient';

-- pgvector 스키마 
CREATE EXTENSION IF NOT EXISTS vector;
-- 학생 자격증명/성취 내용 통합 임베딩 테이블 (성능을 위해 단일 컬렉션으로 설)
CREATE TABLE student_credential_embeddings (
    record_id           UUID PRIMARY KEY REFERENCES credentials(record_id) ON DELETE CASCADE,
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content_text        TEXT NOT NULL,                -- 임베딩에 사용된 실제 텍스트 원문
    source_text_hash    VARCHAR(64) NOT NULL,         -- 내용 변경 감지용 해시
    embedding           vector(1536) NOT NULL,        -- 정확도와 성능을 적절히 가진 1536차원(text-embedding-3-small)
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
-- HNSW 인덱스 (초고속 코사인 유사도 검색)
CREATE INDEX idx_cred_embeddings_hnsw 
ON student_credential_embeddings 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

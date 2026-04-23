-- 001_init.sql
-- Jet-Rag 초기 스키마: documents / chunks / ingest_jobs / ingest_logs
-- 기획서 §10.7 저장 스키마 + Day 3 Tier 1 dedup(§10.8) 지원
--
-- 실행 방법: Supabase SQL Editor에서 전체 붙여넣고 RUN.

BEGIN;

-- ============================================================
-- Extensions
-- ============================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- 1) documents : 업로드된 원본 문서 메타
-- ============================================================
CREATE TABLE documents (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id        UUID NOT NULL,
    title          TEXT NOT NULL,
    doc_type       TEXT NOT NULL CHECK (doc_type IN
                     ('pdf','hwp','hwpx','docx','pptx','image','url','txt','md')),
    source_channel TEXT NOT NULL CHECK (source_channel IN
                     ('drag-drop','os-share','clipboard','url','camera','api')),
    storage_path   TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    size_bytes     BIGINT NOT NULL,
    content_type   TEXT NOT NULL,
    tags           TEXT[] NOT NULL DEFAULT '{}',
    summary        TEXT,
    implications   TEXT,
    doc_embedding  vector(1024),
    flags          JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at     TIMESTAMPTZ,
    UNIQUE (user_id, sha256)
);

CREATE INDEX idx_documents_user_created
    ON documents (user_id, created_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX idx_documents_tags  ON documents USING GIN (tags);
CREATE INDEX idx_documents_flags ON documents USING GIN (flags);
CREATE INDEX idx_documents_embed ON documents USING ivfflat
    (doc_embedding vector_cosine_ops) WITH (lists = 100);

-- ============================================================
-- 2) chunks : 청킹 단위
-- ============================================================
CREATE TABLE chunks (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_id        UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_idx     INTEGER NOT NULL,
    text          TEXT NOT NULL,
    page          INTEGER,
    section_title TEXT,
    char_range    INT4RANGE,
    bbox          NUMERIC[],
    dense_vec     vector(1024),
    sparse_json   JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (doc_id, chunk_idx)
);

CREATE INDEX idx_chunks_doc    ON chunks (doc_id);
CREATE INDEX idx_chunks_dense  ON chunks USING ivfflat
    (dense_vec vector_cosine_ops) WITH (lists = 100);
CREATE INDEX idx_chunks_sparse ON chunks USING GIN (sparse_json);

-- ============================================================
-- 3) ingest_jobs : 업로드→인덱싱 작업 라이프사이클
-- ============================================================
CREATE TABLE ingest_jobs (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    doc_id        UUID REFERENCES documents(id) ON DELETE CASCADE,
    status        TEXT NOT NULL CHECK (status IN
                    ('queued','running','completed','failed','cancelled')),
    current_stage TEXT,
    attempts      INTEGER NOT NULL DEFAULT 0,
    error_msg     TEXT,
    queued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ
);

CREATE INDEX idx_ingest_jobs_status
    ON ingest_jobs (status, queued_at)
    WHERE status IN ('queued','running');
CREATE INDEX idx_ingest_jobs_doc ON ingest_jobs (doc_id);

-- ============================================================
-- 4) ingest_logs : 스테이지 단위 기록
-- ============================================================
CREATE TABLE ingest_logs (
    id          BIGSERIAL PRIMARY KEY,
    job_id      UUID NOT NULL REFERENCES ingest_jobs(id) ON DELETE CASCADE,
    stage       TEXT NOT NULL,
    status      TEXT NOT NULL CHECK (status IN
                  ('started','succeeded','failed','skipped')),
    error_msg   TEXT,
    duration_ms INTEGER,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE INDEX idx_ingest_logs_job ON ingest_logs (job_id, started_at);

-- ============================================================
-- RLS : 전부 enable, 정책 없음 → anon/authenticated 차단, service_role bypass
-- W5 auth 도입 시 per-user SELECT/INSERT/UPDATE 정책 추가 예정
-- ============================================================
ALTER TABLE documents   ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks      ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_logs ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- updated_at 자동 갱신 트리거 (documents만)
-- ============================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_documents_updated_at
    BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

COMMIT;

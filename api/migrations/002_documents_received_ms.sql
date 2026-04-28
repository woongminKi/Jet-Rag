-- 002_documents_received_ms.sql
-- W2 Day 2 (2026-04-28) — SLO 회복 작업
-- 목적: POST /documents 수신 단계 latency (ms) 를 documents 에 기록해
--       /stats.slo_buckets 에서 doc_type 별 p95 / sample_count / pass_rate 집계 가능하게 함.
--
-- 기획서 §10.11 "수신 응답 < 2초" SLO 측정 근거.
-- W2 명세 v0.3 §3.A AC ("`/stats` 응답에 `slo_buckets: {pdf_50p, image, pdf_scan, hwp, url}` 추가") 충족.

ALTER TABLE documents
  ADD COLUMN IF NOT EXISTS received_ms INT;

COMMENT ON COLUMN documents.received_ms IS
  'POST /documents 수신 단계 latency (ms). 수신 시작 → 202 응답 직전까지의 경과 시간. NULL = 측정 이전 (W2 Day 2 이전 업로드분).';

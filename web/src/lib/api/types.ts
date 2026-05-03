export type DocType =
  | 'pdf'
  | 'hwp'
  | 'hwpx'
  | 'docx'
  | 'pptx'
  | 'image'
  | 'url'
  | 'txt'
  | 'md';

export type SourceChannel =
  | 'drag-drop'
  | 'os-share'
  | 'clipboard'
  | 'url'
  | 'camera'
  | 'api';

export type JobStatusValue =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled';

export type StageValue =
  | 'extract'
  | 'chunk'
  | 'content_gate'
  | 'tag_summarize'
  | 'load'
  | 'embed'
  | 'doc_embed'
  | 'dedup'
  | 'done';

export interface Document {
  id: string;
  title: string;
  doc_type: DocType;
  source_channel: SourceChannel;
  size_bytes: number;
  content_type: string;
  tags: string[];
  summary: string | null;
  flags: Record<string, unknown>;
  chunks_count: number;
  latest_job_status: JobStatusValue | null;
  latest_job_stage: StageValue | null;
  created_at: string;
}

export interface DocumentListResponse {
  total: number;
  limit: number;
  offset: number;
  items: Document[];
}

export interface TagCount {
  tag: string;
  count: number;
}

/** W3 Day 2 Phase 3 — `/search` 의 ring buffer 기반 SLO 통계.
 *  sample_count === 0 이면 모든 백분위/평균은 null.
 *  fallback_breakdown 은 항상 3개 키 (transient_5xx, permanent_4xx, none) 노출.
 *  W4-Q-3 — embed_query LRU cache hit 카운트 / 비율 (sample 0 시 null). */
export interface SearchSloStats {
  p50_ms: number | null;
  p95_ms: number | null;
  sample_count: number;
  avg_dense_hits: number | null;
  avg_sparse_hits: number | null;
  avg_fused: number | null;
  fallback_count: number;
  fallback_breakdown: Record<string, number>;
  cache_hit_count: number;
  cache_hit_rate: number | null;
}

/** W7 Day 3 — chunks 단위 가시성 (DE-65 후 1256 환경 + chunk_filter 마킹 추적).
 *  filtered_breakdown 키는 chunks.flags.filtered_reason 의 값
 *  (table_noise · header_footer · empty · extreme_short 등). */
export interface ChunksStats {
  total: number;
  effective: number;
  filtered_breakdown: Record<string, number>;
  filtered_ratio: number;
}

/** W8 Day 4 — Vision API 호출 누적 카운트 (Gemini Flash RPD 20 cap 모니터링).
 *  W11 Day 1 — last_quota_exhausted_at 추가 (한계 #38 lite — fast-fail 시점만 정확 capture).
 *  in-memory counter (vision_metrics 모듈) 의 스냅샷. 프로세스 재시작 시 휘발. */
export interface VisionUsageStats {
  total_calls: number;
  success_calls: number;
  error_calls: number;
  /** UTC ISO 8601, 미호출 시 null */
  last_called_at: string | null;
  /** UTC ISO 8601, fast-fail (RESOURCE_EXHAUSTED / 429 / quota) 미발생 시 null */
  last_quota_exhausted_at?: string | null;
}

export interface Stats {
  documents: {
    total: number;
    by_doc_type: Record<string, number>;
    by_source_channel: Record<string, number>;
    extract_skipped: number;
    total_size_bytes: number;
    added_this_month: number;
    added_last_7d: number;
  };
  chunks_total: number;
  /** W7 Day 3 백엔드 신규 — effective vs filtered breakdown 가시성. */
  chunks: ChunksStats;
  /** W8 Day 4 백엔드 신규 — Gemini Vision RPD 20 cap 모니터링. */
  vision_usage: VisionUsageStats;
  jobs: {
    total: number;
    by_status: Record<string, number>;
    failed_sample: Array<Record<string, unknown>>;
  };
  popular_tags: TagCount[];
  search_slo: SearchSloStats;
  generated_at: string;
}

export interface MatchedChunk {
  chunk_id: string;
  chunk_idx: number;
  text: string;
  page: number | null;
  section_title: string | null;
  highlight: Array<[number, number]>;
  /** W6 Day 5 — RRF score (검색 ranking 근거). null = backward compat */
  rrf_score?: number | null;
  /** W6 Day 5 — chunk metadata (overlap_with_prev_chunk_idx 등) */
  metadata?: Record<string, unknown> | null;
}

export interface SearchHit {
  doc_id: string;
  doc_title: string;
  doc_type: DocType;
  tags: string[];
  summary: string | null;
  created_at: string;
  relevance: number;
  matched_chunk_count: number;
  matched_chunks: MatchedChunk[];
}

export interface QueryParsedInfo {
  has_dense: boolean;
  has_sparse: boolean;
  dense_hits: number;
  sparse_hits: number;
  fused: number;
  /** W3 Day 2 Phase 3 D-1 — HF API 실패 분류.
   *  null: dense path 정상 / "transient_5xx": sparse-only fallback 진입
   *  503 응답에는 본 필드가 노출되지 않음 (응답 자체가 안 감). */
  fallback_reason?: string | null;
}

export interface SearchResponse {
  query: string;
  total: number;
  limit: number;
  offset: number;
  items: SearchHit[];
  took_ms: number;
  query_parsed: QueryParsedInfo;
}

export interface UploadResponse {
  doc_id: string;
  job_id: string | null;
  duplicated: boolean;
}

export interface ReingestResponse {
  doc_id: string;
  job_id: string;
  chunks_deleted: number;
}

export interface JobStatus {
  job_id: string;
  status: JobStatusValue;
  current_stage: StageValue | null;
  attempts: number;
  error_msg: string | null;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface DocumentStatusResponse {
  doc_id: string;
  job: JobStatus | null;
  logs: Array<Record<string, unknown>> | null;
}

/** GET /documents/{id} — 단건 종합 응답 (W2 §3.M, F′-α2). */
export interface DocumentDetailResponse {
  id: string;
  title: string;
  doc_type: DocType;
  source_channel: SourceChannel;
  size_bytes: number;
  content_type: string;
  tags: string[];
  summary: string | null;
  flags: Record<string, unknown>;
  chunks_count: number;
  latest_job: JobStatus | null;
  created_at: string;
  received_ms: number | null;
  source_url: string | null;
}

/** GET /documents/batch-status — 여러 doc_id 의 latest job 일괄 조회. */
export interface BatchStatusItem {
  doc_id: string;
  job: JobStatus | null;
}

export interface BatchStatusResponse {
  items: BatchStatusItem[];
}

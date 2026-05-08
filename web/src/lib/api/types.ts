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

/** S2 D3 — 운영 모드 (UI 토글). 백엔드 Literal 동일 (api/app/services/ingest_mode.py).
 *  - fast: page_cap=10, 메모/짧은 문서용
 *  - default: page_cap=50, 권장 (대다수 자료에 안전)
 *  - precise: page_cap=무한 (비용 한도까지) */
export type IngestMode = 'fast' | 'default' | 'precise';

/** S2 D3 — `flags.ingest_mode` 마킹 (백엔드가 doc 단 저장). */
export interface IngestModeFlag {
  ingest_mode?: IngestMode;
}

/** S2 D2 — `flags.vision_page_cap_exceeded` + `flags.vision_page_cap` 페이로드.
 *  cost cap (`vision_budget_exceeded`) 와 직교 — 같은 doc 안 둘 다 도달 가능. */
export interface VisionPageCapFlag {
  vision_page_cap_exceeded?: boolean;
  vision_page_cap?: {
    called_pages: number;
    page_cap: number;
    reason: string;
  };
}

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
  | 'chunk_filter'
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

/** W14 Day 3 — by_mode entry. `SearchSloStats` 와 동일 schema (recursion 회피 위해 분리). */
export interface SearchSloPerMode {
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

/** W3 Day 2 Phase 3 — `/search` 의 ring buffer 기반 SLO 통계.
 *  sample_count === 0 이면 모든 백분위/평균은 null.
 *  fallback_breakdown 은 항상 3개 키 (transient_5xx, permanent_4xx, none) 노출.
 *  W4-Q-3 — embed_query LRU cache hit 카운트 / 비율 (sample 0 시 null).
 *  W14 Day 3 — by_mode 신규: hybrid/dense/sparse 분리 측정. */
export interface SearchSloStats extends SearchSloPerMode {
  by_mode?: Record<string, SearchSloPerMode>;
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

/** S0 D4 — incremental vision reingest 응답 (POST /documents/{id}/reingest-missing). */
export interface ReingestMissingResponse {
  doc_id: string;
  job_id: string;
  total_pages: number;
  missing_pages_before: number[];
  note: string;
}

export interface StageProgressDetail {
  current: number;
  total: number;
  unit: string; // 'pages' | 'chunks' | ...
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
  /** W25 D14 Sprint B — 대략적 남은 시간(ms). queued/running 시만 추정값,
   *  나머지는 null. ingest_logs.duration_ms median (5분 cache) + fallback. */
  estimated_remaining_ms?: number | null;
  /** W25 D14 — stage 안 sub-step 진행 (예: vision_enrich 페이지 12/41).
   *  null 시 stage 라벨만 표시. */
  stage_progress?: StageProgressDetail | null;
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

/** W25 D14 Sprint 0 — GET /documents/active.
 *  /ingest 새로고침 후 진행 중·실패 doc 자동 표시 (queued/running/failed × 최근 24h). */
export interface ActiveDocItem {
  doc_id: string;
  file_name: string;
  size_bytes: number;
  job: JobStatus;
}

export interface ActiveDocsResponse {
  items: ActiveDocItem[];
}

/** W16 Day 2 — `/stats/trend` 의 단일 시간 bucket.
 *  - metric=search 시: p50_ms / p95_ms / fallback_count 채움
 *  - metric=vision 시: success_count / quota_exhausted_count 채움
 *  - 빈 bucket (sample_count=0) 도 row 유지 → frontend 시계열 zero-fill */
export interface TrendBucket {
  bucket_start: string;
  sample_count: number;
  p50_ms?: number | null;
  p95_ms?: number | null;
  fallback_count?: number | null;
  success_count?: number | null;
  quota_exhausted_count?: number | null;
}

export type TrendRange = '24h' | '7d' | '30d';
export type TrendMode = 'all' | 'hybrid' | 'dense' | 'sparse';
export type TrendMetric = 'search' | 'vision';

/** W16 Day 2 — `/stats/trend` 응답.
 *  error_code='migrations_pending': 005·006·007 미적용 graceful (buckets 빈 배열). */
export interface TrendResponse {
  metric: TrendMetric;
  range: TrendRange;
  mode: TrendMode | null;
  buckets: TrendBucket[];
  error_code: string | null;
  generated_at: string;
}

/** S1 D3 — `/admin/queries/stats` 응답 (실 query 로그 시각화).
 *  - error_code='migrations_pending': 마이그 006 미적용 graceful (모든 집계 빈 값).
 *  - error_code='classify_unavailable': evals 모듈 import 실패 (distribution 빈 dict).
 *  - daily: range 일수 만큼 row, KST 자정 기준 zero-fill (오래된→최신 순).
 *  - query_type_distribution: 9 라벨 모두 노출 (sample 0건이라도). 단 error_code !== null 시 빈 dict 가능.
 *  - failed_samples: 최근 10건. reason ∈ {permanent_4xx, transient_5xx, no_hits}. */
export type AdminRange = '7d' | '14d' | '30d';
export type AdminFailureReason = 'permanent_4xx' | 'transient_5xx' | 'no_hits';

export interface AdminDailyBucket {
  date: string; // YYYY-MM-DD (KST)
  count: number;
  success_count: number;
  fail_count: number;
}

export interface AdminFailedSample {
  query: string;
  ts: string;
  reason: AdminFailureReason;
}

export interface AdminQueriesStatsResponse {
  range: AdminRange;
  daily: AdminDailyBucket[];
  query_type_distribution: Record<string, number>;
  failed_samples: AdminFailedSample[];
  total_queries: number;
  success_rate: number | null;
  avg_latency_ms: number | null;
  error_code: 'migrations_pending' | 'classify_unavailable' | null;
  generated_at: string;
}

/** S1 D4 — `/admin/feedback/stats` 응답 (answer_feedback 통합 분석).
 *  - error_code='migrations_pending': 마이그 011 미적용 graceful (모든 집계 빈 값).
 *  - daily: range 일수 만큼 row, KST 자정 기준 zero-fill (오래된→최신 순).
 *  - rating_distribution: 항상 2 키 (up/down) 노출.
 *  - comment_categories: 항상 4 키 노출 (sample 0건이라도).
 *  - recent_comments: 코멘트 첨부된 최근 10건. 빈 코멘트는 분류·노출 X.
 *  - satisfaction_rate: 전체 sample 0건 시 null. */
export type AdminFeedbackRating = 'up' | 'down';
export type AdminFeedbackCategory =
  | 'search_issue'
  | 'answer_issue'
  | 'source_issue'
  | 'other';

export interface AdminFeedbackDailyBucket {
  date: string; // YYYY-MM-DD (KST)
  up: number;
  down: number;
  total: number;
}

export interface AdminFeedbackComment {
  query: string;
  rating: AdminFeedbackRating;
  comment: string;
  category: AdminFeedbackCategory;
  ts: string;
}

export interface AdminFeedbackStatsResponse {
  range: AdminRange;
  daily: AdminFeedbackDailyBucket[];
  rating_distribution: Record<AdminFeedbackRating, number>;
  satisfaction_rate: number | null;
  comment_categories: Record<AdminFeedbackCategory, number>;
  recent_comments: AdminFeedbackComment[];
  total_feedback: number;
  comment_count: number;
  error_code: 'migrations_pending' | null;
  generated_at: string;
}

/** W25 D12 — `/answer` 라우터 응답 (LLM RAG PoC).
 *  faithfulness: 답변에 인라인 [N] 으로 sources[] 인용 명시. */
export interface AnswerSource {
  chunk_id: string;
  doc_id: string;
  doc_title: string | null;
  chunk_idx: number;
  page: number | null;
  section_title: string | null;
  score: number;
  /** chunk 본문 앞 200자 — UI snippet 용 */
  snippet: string;
}

export interface AnswerResponse {
  query: string;
  answer: string;
  sources: AnswerSource[];
  /** false 면 검색 0건 → answer 는 "찾지 못함" 메시지 */
  has_search_results: boolean;
  model: string;
  took_ms: number;
  query_parsed: QueryParsedInfo;
}

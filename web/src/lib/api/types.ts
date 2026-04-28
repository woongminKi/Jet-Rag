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
  jobs: {
    total: number;
    by_status: Record<string, number>;
    failed_sample: Array<Record<string, unknown>>;
  };
  popular_tags: TagCount[];
  generated_at: string;
}

export interface MatchedChunk {
  chunk_id: string;
  chunk_idx: number;
  text: string;
  page: number | null;
  section_title: string | null;
  highlight: Array<[number, number]>;
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

export interface SearchResponse {
  query: string;
  total: number;
  limit: number;
  offset: number;
  items: SearchHit[];
  took_ms: number;
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

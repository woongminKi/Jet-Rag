import { ApiError, apiGet, apiPost, apiPostFormData } from './client';
import type {
  ActiveDocsResponse,
  AdminFeedbackStatsResponse,
  AdminQueriesStatsResponse,
  AdminRange,
  AnswerResponse,
  BatchStatusResponse,
  DocumentDetailResponse,
  DocumentListResponse,
  DocumentStatusResponse,
  ReingestResponse,
  SearchResponse,
  SourceChannel,
  Stats,
  TrendMetric,
  TrendMode,
  TrendRange,
  TrendResponse,
  UploadResponse,
} from './types';

export * from './types';
export { ApiError } from './client';

export const getStats = () => apiGet<Stats>('/stats');

/** W16 Day 2 — `/stats/trend` 시계열 aggregate.
 *  - range: '24h' / '7d' / '30d' (default '7d')
 *  - mode : metric=search 만 적용. 'all' / 'hybrid' / 'dense' / 'sparse' (default 'all')
 *  - metric: 'search' / 'vision' (default 'search')
 *  - 마이그레이션 005·006·007 미적용 시 graceful — buckets=[] + error_code='migrations_pending'. */
export const getStatsTrend = (
  range: TrendRange = '7d',
  metric: TrendMetric = 'search',
  mode: TrendMode = 'all',
) => {
  const qs = new URLSearchParams({ range, metric, mode });
  return apiGet<TrendResponse>(`/stats/trend?${qs.toString()}`);
};

export const listDocuments = (limit = 20, offset = 0) =>
  apiGet<DocumentListResponse>(`/documents?limit=${limit}&offset=${offset}`);

export type SearchMode = 'hybrid' | 'dense' | 'sparse';

/** W11 Day 4 / W12 Day 1 / W14 Day 1 — docId / mode 지원
 *  · docId: 단일 문서 스코프 자연어 QA (US-08)
 *  · mode: hybrid (default) / dense / sparse — ablation (KPI '하이브리드 +5pp 우세') */
export const searchDocuments = (
  q: string,
  limit = 10,
  offset = 0,
  docId?: string | null,
  mode?: SearchMode,
) => {
  const qs = new URLSearchParams({
    q,
    limit: String(limit),
    offset: String(offset),
  });
  if (docId) qs.set('doc_id', docId);
  if (mode && mode !== 'hybrid') qs.set('mode', mode);
  return apiGet<SearchResponse>(`/search?${qs.toString()}`);
};

export const uploadDocument = (
  file: File,
  sourceChannel: SourceChannel = 'drag-drop',
) => {
  const fd = new FormData();
  fd.append('file', file);
  fd.append('source_channel', sourceChannel);
  return apiPostFormData<UploadResponse>('/documents', fd);
};

/** W2 §3.M 단건 조회 — `/doc/[id]` 페이지가 한 번에 필요한 메타·태그·요약·진행 상태. */
export const getDocument = (docId: string) =>
  apiGet<DocumentDetailResponse>(`/documents/${docId}`);

export const getDocumentStatus = (docId: string, includeLogs = false) =>
  apiGet<DocumentStatusResponse>(
    `/documents/${docId}/status${includeLogs ? '?include_logs=true' : ''}`,
  );

/** W2 §3.H batch 폴러 — 콤마 구분 doc_id 리스트, max 50. */
export const getBatchStatus = (docIds: string[]) =>
  apiGet<BatchStatusResponse>(
    `/documents/batch-status?ids=${docIds.map(encodeURIComponent).join(',')}`,
  );

/** W25 D14 Sprint 0 — /ingest 새로고침 후 진행 현황 복원.
 *  status IN (queued/running/failed) × 최근 N시간 (default 24h, max 168h). */
export const getActiveDocs = (hours = 24) =>
  apiGet<ActiveDocsResponse>(`/documents/active?hours=${hours}`);

/** W25 D14 — 답변 피드백 (👍/👎 + 옵션 코멘트). */
export interface AnswerFeedbackPayload {
  query: string;
  answer_text: string;
  helpful: boolean;
  comment?: string | null;
  doc_id?: string | null;
  sources_count?: number;
  model?: string | null;
}

export interface AnswerFeedbackResponse {
  feedback_id: number | null;
  skipped: boolean;
  note: string | null;
}

export const submitAnswerFeedback = async (
  payload: AnswerFeedbackPayload,
): Promise<AnswerFeedbackResponse> => {
  const res = await fetch(
    `${process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000'}/answer/feedback`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(payload),
      cache: 'no-store',
    },
  );
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json() as Promise<AnswerFeedbackResponse>;
};

/** W25 D14 — RAGAS 정량 평가 (Faithfulness + AnswerRelevancy + 옵션 메트릭). */
export interface RagasMetrics {
  faithfulness: number | null;
  answer_relevancy: number | null;
  context_precision: number | null;
  context_recall: number | null;
  answer_correctness: number | null;
}

export interface RagasEvalResponse {
  metrics: RagasMetrics;
  judge_model: string | null;
  took_ms: number | null;
  cached: boolean;
  skipped: boolean;
  note: string | null;
  created_at: string | null;
}

export interface RagasEvalPayload {
  query: string;
  answer_text: string;
  doc_id?: string | null;
  contexts: string[];
}

const _API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';

export const getRagasEval = (query: string, docId?: string | null) => {
  const qs = new URLSearchParams({ query });
  if (docId) qs.set('doc_id', docId);
  return apiGet<RagasEvalResponse>(`/answer/eval-ragas?${qs.toString()}`);
};

export const submitRagasEval = async (
  payload: RagasEvalPayload,
): Promise<RagasEvalResponse> => {
  const res = await fetch(`${_API_BASE}/answer/eval-ragas`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(payload),
    cache: 'no-store',
  });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json() as Promise<RagasEvalResponse>;
};

/** W25 D14 — 검색 적합도 (Context Precision) 만 측정 (LLM 호출 1개). */
export interface SearchPrecisionPayload {
  query: string;
  contexts: string[];
  doc_id?: string | null;
}

export const getSearchPrecision = (query: string, docId?: string | null) => {
  const qs = new URLSearchParams({ query });
  if (docId) qs.set('doc_id', docId);
  return apiGet<RagasEvalResponse>(`/search/eval-precision?${qs.toString()}`);
};

export const submitSearchPrecision = async (
  payload: SearchPrecisionPayload,
): Promise<RagasEvalResponse> => {
  const res = await fetch(`${_API_BASE}/search/eval-precision`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(payload),
    cache: 'no-store',
  });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json() as Promise<RagasEvalResponse>;
};

export const reingestDocument = (docId: string) =>
  apiPost<ReingestResponse>(`/documents/${docId}/reingest`);

/** S1 D3 — `/admin/queries/stats` 실 query 로그 통계.
 *  - range: '7d' / '14d' / '30d' (default '7d')
 *  - 마이그 006 미적용 시 graceful — error_code='migrations_pending', daily/distribution 빈 값.
 *  - single-user MVP: 별도 인증 없음 (production 진입 시 별도 sprint). */
export const getAdminQueriesStats = (range: AdminRange = '7d') => {
  const qs = new URLSearchParams({ range });
  return apiGet<AdminQueriesStatsResponse>(
    `/admin/queries/stats?${qs.toString()}`,
  );
};

/** S1 D4 — `/admin/feedback/stats` 사용자 피드백 통합 분석.
 *  - range: '7d' / '14d' / '30d' (default '7d')
 *  - 마이그 011 미적용 시 graceful — error_code='migrations_pending'.
 *  - 코멘트 자동 분류는 룰 기반 (LLM 호출 0). 1주 누적 후 룰 정합성 검증. */
export const getAdminFeedbackStats = (range: AdminRange = '7d') => {
  const qs = new URLSearchParams({ range });
  return apiGet<AdminFeedbackStatsResponse>(
    `/admin/feedback/stats?${qs.toString()}`,
  );
};

/** W25 D12 — `/answer` LLM RAG PoC.
 *  검색 → top_k chunks → Gemini 2.5 Flash 답변 + 출처 인용.
 *  · top_k: LLM 에 전달할 chunks 수 (default 5, max 10)
 *  · docId: 단일 문서 스코프 (US-08 패턴 동일) */
export const getAnswer = (
  q: string,
  topK = 5,
  docId?: string | null,
) => {
  const qs = new URLSearchParams({ q, top_k: String(topK) });
  if (docId) qs.set('doc_id', docId);
  return apiGet<AnswerResponse>(`/answer?${qs.toString()}`);
};

import { apiGet, apiPost, apiPostFormData } from './client';
import type {
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

export const reingestDocument = (docId: string) =>
  apiPost<ReingestResponse>(`/documents/${docId}/reingest`);

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

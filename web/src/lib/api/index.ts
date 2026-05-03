import { apiGet, apiPost, apiPostFormData } from './client';
import type {
  BatchStatusResponse,
  DocumentDetailResponse,
  DocumentListResponse,
  DocumentStatusResponse,
  ReingestResponse,
  SearchResponse,
  SourceChannel,
  Stats,
  UploadResponse,
} from './types';

export * from './types';
export { ApiError } from './client';

export const getStats = () => apiGet<Stats>('/stats');

export const listDocuments = (limit = 20, offset = 0) =>
  apiGet<DocumentListResponse>(`/documents?limit=${limit}&offset=${offset}`);

/** W11 Day 4 / W12 Day 1 — `docId` 지정 시 단일 문서 스코프 자연어 QA (US-08). */
export const searchDocuments = (
  q: string,
  limit = 10,
  offset = 0,
  docId?: string | null,
) => {
  const qs = new URLSearchParams({
    q,
    limit: String(limit),
    offset: String(offset),
  });
  if (docId) qs.set('doc_id', docId);
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

/** W25 D14 — 업로드 직후 indicator 즉시 갱신용 글로벌 broadcast.
 *
 *  이슈: IngestUI 의 setItems 로 처리 현황 카드는 즉시 표시되지만,
 *  헤더 indicator (useActiveDocsRealtime) 는 Realtime INSERT event 또는
 *  60s safety resync 까지 갱신 지연 → "처리 중 1" → "처리 중 2" 가 늦게 반영.
 *
 *  해결: window CustomEvent 로 글로벌 broadcast. emitter (IngestUI) 와
 *  listener (indicator hook) 가 컴포넌트 트리 분리되어 있어도 즉시 신호 전달.
 *  의존성 0 (브라우저 표준 API).
 */

const EVENT_NAME = 'jetrag:doc-uploaded';

export interface DocUploadedDetail {
  docId: string;
}

export function emitDocUploaded(detail: DocUploadedDetail): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<DocUploadedDetail>(EVENT_NAME, { detail }));
}

export function onDocUploaded(handler: (detail: DocUploadedDetail) => void): () => void {
  if (typeof window === 'undefined') return () => undefined;
  const wrapped = (e: Event) => {
    const ce = e as CustomEvent<DocUploadedDetail>;
    handler(ce.detail);
  };
  window.addEventListener(EVENT_NAME, wrapped);
  return () => window.removeEventListener(EVENT_NAME, wrapped);
}

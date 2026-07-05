import { IngestUI } from '@/components/jet-rag/ingest-ui';

/** W25 D14 — 처리 현황과 헤더 indicator 가 같은 데이터 source (useActiveDocsRealtime) 사용.
 *  page.tsx 는 단순 layout 만, IngestUI 가 client 에서 active doc 자동 동기. */
export default function IngestPage() {
  return (
    <main className="container mx-auto flex-1 px-4 py-8 md:px-6 md:py-12">
      <div className="mx-auto max-w-3xl space-y-6">
        <header className="space-y-2">
          <h1 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">
            문서 업로드
          </h1>
          <p className="text-sm text-muted-foreground">
            한국어 PDF, HWP, DOCX, 이미지 등을 올리면 자동으로 청킹·태그·요약·임베딩까지 처리됩니다.
          </p>
        </header>
        <IngestUI />
      </div>
    </main>
  );
}

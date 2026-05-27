import Link from 'next/link';
import { ArrowRight, FileText, Sparkles } from 'lucide-react';
import type { Document } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { docTypeLabel } from '@/lib/doc-type-label';
import { buildDocsUrl } from '@/lib/docs-filter';
import { formatRelativeTime } from '@/lib/format';

interface NewArrivalsCardProps {
  documents: Document[];
}

/**
 * 최근 추가 카드.
 * W25 D1·D2 — D-1 + D-4 결합 (QA 1차 fix 반영):
 *   - 행 전체가 `/doc/{id}` 로 진입하는 Link (hover 표시).
 *   - 태그 칩은 `/docs?tag=...` 별도 Link — 행 Link 와 중첩 회피를 위해
 *     pointer-events 위계 패턴 사용:
 *       * 행 컨테이너 = `relative`
 *       * 행 Link = absolute inset-0 (z-0) — 시각 hover/focus 영역, 포인터 이벤트 수신
 *       * 본문 (제목/태그/시간) = `relative z-10 pointer-events-none` — 클릭이 행 Link 로 통과
 *       * 태그 Link / 시간 표시는 `pointer-events-auto` 로 복원 — 태그만 별개 동작
 *   - 행 Link 에 가시 `focus-visible:ring` — 본문이 z-10 으로 가리는 케이스 방지.
 *   - 키보드 접근성 — 행 Link → 태그 Link 순서로 Tab 진입.
 */
export function NewArrivalsCard({ documents }: NewArrivalsCardProps) {
  return (
    <Card className="overflow-hidden rounded-2xl border-primary/20 bg-primary/5">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-lg font-semibold">
          <Sparkles className="h-5 w-5 text-primary" />
          최근 추가
          {documents.length > 0 && (
            <Badge variant="secondary" className="ml-1">
              {documents.length}건
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {documents.length === 0 ? (
          <EmptyState />
        ) : (
          // W26 v2 — ul 의 브라우저 기본 padding-inline-start: 40px 누적 → 모바일 우측 잘림 root cause.
          // list-none pl-0 m-0 으로 reset. LI 의 -mx-* 도 제거 (불필요한 overflow risk).
          <ul className="m-0 list-none divide-y divide-border pl-0">
            {documents.map((doc) => (
              <DocRow key={doc.id} doc={doc} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function DocRow({ doc }: { doc: Document }) {
  return (
    <li className="group relative rounded-md py-3 first:pt-0 last:pb-0 hover:bg-accent/40">
      <Link
        href={`/doc/${doc.id}`}
        aria-label={`${doc.title} 문서 열기`}
        className="absolute inset-0 z-0 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      />
      {/* W26 v3 — mobile: title 단독 + tags/time 다음 줄 stack. sm 부터: title+time inline.
          모바일 viewport 좁을 때 title+time 같은 줄이 viewport 초과하는 케이스 자체 제거. */}
      <div className="pointer-events-none relative z-10 overflow-hidden">
        {/* Row 1 — title (모바일 단독, sm 부터 time 동반) */}
        <div className="flex min-w-0 items-baseline gap-2">
          <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground group-hover:text-foreground">
            {doc.title}
          </p>
          {/* sm 부터 inline time. 모바일은 hidden (아래 meta row 에 표시). */}
          <time
            className="hidden shrink-0 whitespace-nowrap text-[11px] tabular-nums text-muted-foreground sm:inline"
            dateTime={doc.created_at}
            title={formatRelativeTime(doc.created_at)}
          >
            {formatRelativeTimeShort(doc.created_at)}
          </time>
        </div>
        {/* Row 2 — type badge + tags + time(모바일 only, 우측) */}
        <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-1.5">
          <Badge variant="outline" className="h-5 px-1.5 text-[10px]">
            {docTypeLabel(doc.doc_type)}
          </Badge>
          {doc.tags.slice(0, 2).map((tag) => (
            <Link
              key={tag}
              href={buildDocsUrl({ tag })}
              aria-label={`${tag} 태그로 좁혀 문서 보기`}
              title="이 태그로 좁히기"
              className="pointer-events-auto rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <Badge
                variant="secondary"
                className="h-5 max-w-full cursor-pointer truncate px-1.5 text-[10px] hover:bg-primary hover:text-primary-foreground"
              >
                #{tag}
              </Badge>
            </Link>
          ))}
          {/* 모바일: tags row 우측 끝에 time 작게. sm 부터는 위 row 의 time 사용 (여기는 숨김). */}
          <time
            className="ml-auto shrink-0 whitespace-nowrap text-[10px] tabular-nums text-muted-foreground sm:hidden"
            dateTime={doc.created_at}
            title={formatRelativeTime(doc.created_at)}
          >
            {formatRelativeTimeShort(doc.created_at)}
          </time>
        </div>
      </div>
    </li>
  );
}

// W26 v2 — 모바일 우측 잘림 방지용 짧은 상대 시간 ("13d", "2h", "5m"). Toss 풍.
// 본문 hover/title 에 긴 형식 ("13일 전") 표시.
function formatRelativeTimeShort(iso: string): string {
  const date = new Date(iso);
  const diffMs = Date.now() - date.getTime();
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return '방금';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}분`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}일`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}달`;
  const years = Math.floor(days / 365);
  return `${years}년`;
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-8 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-muted">
        <FileText className="h-5 w-5 text-muted-foreground" />
      </div>
      <p className="text-sm text-muted-foreground">
        아직 추가한 문서가 없어요. 첫 파일을 올려보세요.
      </p>
      <Link
        href="/ingest"
        className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
      >
        파일 업로드
        <ArrowRight className="h-4 w-4" />
      </Link>
    </div>
  );
}

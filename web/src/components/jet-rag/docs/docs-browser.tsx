'use client';

/**
 * `/docs` 브라우저 — 태그 필터 모드 (시나리오 X / A-α, W25 D1·D2 사용자 확정).
 *
 * - props: SSR initial fetch 결과를 그대로 받는다 (W17 패턴 1).
 * - in-memory 필터: 단일 사용자 100건 규모 가정 → refetch 없음. 백엔드 신규 라우트 0.
 * - URL 동기화: 칩/타입 토글 시 useTransition + router.push (W19 패턴 3 — race 방지).
 * - 빈 결과 fallback: 인기 태그 Top 3 (전체 빈도 기반) 클릭 시 즉시 좁혀진 상태로 진입.
 * - 빈 결과 시 useEffect 안 동기 setState 회피 (W17/W19 패턴 2). handler 가 setState 처리.
 */

import { useMemo, useTransition } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ArrowRight, FileText } from 'lucide-react';
import type { Document, DocType } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent } from '@/components/ui/card';
import { docTypeLabel } from '@/lib/doc-type-label';
import { formatRelativeTime } from '@/lib/format';
import {
  aggregateDocTypes,
  aggregateTags,
  buildDocsUrl,
  filterDocuments,
} from '@/lib/docs-filter';

interface DocsBrowserProps {
  initialDocuments: Document[];
  total: number;
  initialTag: string | null;
  initialType: DocType | null;
}

const TAG_CHIP_LIMIT = 20;
const FALLBACK_TAG_LIMIT = 3;

export function DocsBrowser({
  initialDocuments,
  total,
  initialTag,
  initialType,
}: DocsBrowserProps) {
  const router = useRouter();
  // W19 패턴 3 — 빠른 칩 클릭 race 방지 + 진행 중 시각 피드백.
  const [isPending, startTransition] = useTransition();

  // initialDocuments 는 props (immutable). aggregate 결과는 props 변할 때만 재계산.
  const tagCounts = useMemo(() => aggregateTags(initialDocuments), [initialDocuments]);
  const typeCounts = useMemo(
    () => aggregateDocTypes(initialDocuments),
    [initialDocuments],
  );
  const visibleTags = tagCounts.slice(0, TAG_CHIP_LIMIT);
  const fallbackTags = tagCounts.slice(0, FALLBACK_TAG_LIMIT);

  const filtered = useMemo(
    () => filterDocuments(initialDocuments, { tag: initialTag, type: initialType }),
    [initialDocuments, initialTag, initialType],
  );

  const navigate = (next: { tag?: string | null; type?: DocType | null }) => {
    const target = buildDocsUrl({
      tag: next.tag === undefined ? initialTag : next.tag,
      type: next.type === undefined ? initialType : next.type,
    });
    startTransition(() => {
      router.push(target);
    });
  };

  const toggleTag = (tag: string) => {
    navigate({ tag: initialTag === tag ? null : tag });
  };

  const toggleType = (type: DocType) => {
    navigate({ type: initialType === type ? null : type });
  };

  const clearAll = () => {
    if (!initialTag && !initialType) return;
    navigate({ tag: null, type: null });
  };

  const hasActiveFilter = initialTag !== null || initialType !== null;

  return (
    <div className="space-y-6" aria-busy={isPending}>
      <FilterPanel
        visibleTags={visibleTags}
        typeCounts={typeCounts}
        activeTag={initialTag}
        activeType={initialType}
        isPending={isPending}
        onToggleTag={toggleTag}
        onToggleType={toggleType}
        onClear={clearAll}
        hasActiveFilter={hasActiveFilter}
        total={total}
        filteredCount={filtered.length}
      />

      {filtered.length === 0 ? (
        <EmptyResult
          fallbackTags={fallbackTags}
          isPending={isPending}
          onPickTag={(tag) => navigate({ tag, type: null })}
        />
      ) : (
        <DocList docs={filtered} />
      )}
    </div>
  );
}

interface FilterPanelProps {
  visibleTags: Array<{ tag: string; count: number }>;
  typeCounts: Array<{ type: DocType; count: number }>;
  activeTag: string | null;
  activeType: DocType | null;
  isPending: boolean;
  onToggleTag: (tag: string) => void;
  onToggleType: (type: DocType) => void;
  onClear: () => void;
  hasActiveFilter: boolean;
  total: number;
  filteredCount: number;
}

function FilterPanel({
  visibleTags,
  typeCounts,
  activeTag,
  activeType,
  isPending,
  onToggleTag,
  onToggleType,
  onClear,
  hasActiveFilter,
  total,
  filteredCount,
}: FilterPanelProps) {
  return (
    <Card className="overflow-hidden rounded-2xl">
      <CardContent className="space-y-4 py-5">
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span>
            전체 {total}건 중{' '}
            <span className="font-semibold text-foreground">{filteredCount}건</span>{' '}
            표시
          </span>
          {hasActiveFilter && (
            <button
              type="button"
              onClick={onClear}
              disabled={isPending}
              className="rounded-md border border-border bg-card px-2 py-0.5 text-xs font-medium text-foreground hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
              aria-label="모든 필터 해제"
            >
              필터 해제
            </button>
          )}
        </div>

        {typeCounts.length > 0 && (
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              문서 타입
            </p>
            <div className="flex flex-wrap gap-1.5">
              {typeCounts.map(({ type, count }) => {
                const active = activeType === type;
                return (
                  <button
                    key={type}
                    type="button"
                    onClick={() => onToggleType(type)}
                    disabled={isPending}
                    aria-pressed={active}
                    aria-label={
                      active
                        ? `${docTypeLabel(type)} 타입 필터 해제`
                        : `${docTypeLabel(type)} 타입으로 좁히기`
                    }
                    className={
                      active
                        ? 'inline-flex h-7 items-center gap-1 rounded-full bg-primary px-3 text-xs font-medium text-primary-foreground transition-opacity disabled:opacity-50'
                        : 'inline-flex h-7 items-center gap-1 rounded-full border border-border bg-card px-3 text-xs font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:opacity-50'
                    }
                  >
                    {docTypeLabel(type)}
                    <span className="text-[10px] opacity-70">{count}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {visibleTags.length > 0 && (
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              태그
            </p>
            <div className="flex flex-wrap gap-1.5">
              {visibleTags.map(({ tag, count }) => {
                const active = activeTag === tag;
                return (
                  <button
                    key={tag}
                    type="button"
                    onClick={() => onToggleTag(tag)}
                    disabled={isPending}
                    aria-pressed={active}
                    aria-label={
                      active
                        ? `${tag} 태그 필터 해제`
                        : `${tag} 태그로 좁히기`
                    }
                    className={
                      active
                        ? 'inline-flex h-7 items-center gap-1 rounded-full bg-primary px-3 text-xs font-medium text-primary-foreground transition-opacity disabled:opacity-50'
                        : 'inline-flex h-7 items-center gap-1 rounded-full border border-border bg-card px-3 text-xs font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:opacity-50'
                    }
                  >
                    #{tag}
                    <span className="text-[10px] opacity-70">{count}</span>
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {visibleTags.length === 0 && typeCounts.length === 0 && (
          <p className="text-sm text-muted-foreground">
            아직 등록된 문서가 없어요. 첫 파일을 올려보세요.
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function DocList({ docs }: { docs: Document[] }) {
  return (
    <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {docs.map((doc) => (
        <li key={doc.id}>
          <DocRow doc={doc} />
        </li>
      ))}
    </ul>
  );
}

function DocRow({ doc }: { doc: Document }) {
  return (
    <Link
      href={`/doc/${doc.id}`}
      className="block overflow-hidden rounded-2xl border border-border bg-card p-4 transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-2">
            <p className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">{doc.title}</p>
            <time
              className="shrink-0 whitespace-nowrap text-xs tabular-nums text-muted-foreground"
              dateTime={doc.created_at}
            >
              {formatRelativeTime(doc.created_at)}
            </time>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="outline" className="h-5 px-1.5 text-[10px]">
              {docTypeLabel(doc.doc_type)}
            </Badge>
            {doc.tags.slice(0, 4).map((tag) => (
              <Badge
                key={tag}
                variant="secondary"
                className="h-5 px-1.5 text-[10px]"
              >
                #{tag}
              </Badge>
            ))}
          </div>
          {doc.summary && (
            <p className="line-clamp-2 break-words text-xs text-muted-foreground">{doc.summary}</p>
          )}
        </div>
      </div>
    </Link>
  );
}

interface EmptyResultProps {
  fallbackTags: Array<{ tag: string; count: number }>;
  isPending: boolean;
  onPickTag: (tag: string) => void;
}

function EmptyResult({ fallbackTags, isPending, onPickTag }: EmptyResultProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-border bg-muted/20 px-6 py-16 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-full bg-muted">
        <FileText className="h-6 w-6 text-muted-foreground" />
      </div>
      <p className="text-base font-medium text-foreground">
        이 조합으로 일치하는 문서가 없어요
      </p>
      <p className="text-sm text-muted-foreground">
        다른 조합을 시도하거나 아래 인기 태그로 시작해 보세요.
      </p>
      {fallbackTags.length > 0 ? (
        <div className="mt-1 flex flex-wrap items-center justify-center gap-2">
          {fallbackTags.map(({ tag, count }) => (
            <button
              key={tag}
              type="button"
              onClick={() => onPickTag(tag)}
              disabled={isPending}
              className="inline-flex h-7 items-center gap-1 rounded-full border border-border bg-card px-3 text-xs font-medium text-foreground transition-colors hover:bg-accent hover:text-accent-foreground disabled:opacity-50"
              aria-label={`${tag} 태그로 좁히기`}
            >
              #{tag}
              <span className="text-[10px] opacity-70">{count}</span>
            </button>
          ))}
        </div>
      ) : (
        <Link
          href="/ingest"
          className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
        >
          파일 업로드
          <ArrowRight className="h-4 w-4" />
        </Link>
      )}
    </div>
  );
}

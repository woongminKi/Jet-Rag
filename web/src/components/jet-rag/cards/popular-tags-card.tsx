import Link from 'next/link';
import type { TagCount } from '@/lib/api';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { buildDocsUrl } from '@/lib/docs-filter';

interface PopularTagsCardProps {
  tags: TagCount[];
}

/**
 * 인기 태그 칩.
 * W25 D1·D2 — destination 통일: `/search?q=태그` → `/docs?tag=태그`.
 *   - `/search` 는 자연어, `/docs` 는 메타 (태그·타입) — 역할 분담 명확화.
 *   - 태그 칩의 의도는 "이 태그로 좁히기" (자연어 질의 X) → 메타 필터가 더 정확.
 */
export function PopularTagsCard({ tags }: PopularTagsCardProps) {
  const top = tags.slice(0, 10);

  return (
    <Card className="overflow-hidden rounded-2xl">
      <CardHeader className="pb-3">
        <CardTitle className="text-lg font-semibold">인기 태그</CardTitle>
      </CardHeader>
      <CardContent>
        {top.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            아직 집계된 태그가 없어요.
          </p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {top.map((t) => (
              <Link
                key={t.tag}
                href={buildDocsUrl({ tag: t.tag })}
                aria-label={`${t.tag} 태그로 좁혀 문서 보기`}
                title="이 태그로 좁히기"
              >
                <Badge
                  variant="secondary"
                  className="cursor-pointer break-all transition-colors hover:bg-primary hover:text-primary-foreground"
                >
                  #{t.tag}
                  <span className="ml-1 text-[10px] opacity-70">{t.count}</span>
                </Badge>
              </Link>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

'use client';

import { useEffect, useState } from 'react';
import { use } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Eye,
  Info,
  Loader2,
  Search,
  Shield,
  Sparkles,
  Tag,
  XCircle,
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import {
  ApiError,
  getDocument,
  type DocumentDetailResponse,
} from '@/lib/api';
import { docTypeLabel } from '@/lib/doc-type-label';
import { formatBytes } from '@/lib/format';

const POLL_INTERVAL_MS = 1500;
const MAX_POLL_DURATION_MS = 5 * 60 * 1000;

interface PageProps {
  params: Promise<{ id: string }>;
}

export default function DocPage({ params }: PageProps) {
  const { id } = use(params);
  return <DocDetail docId={id} />;
}

function DocDetail({ docId }: { docId: string }) {
  const searchParams = useSearchParams();
  const justUploaded = searchParams.get('uploaded') === '1';
  const justDuplicated = searchParams.get('duplicated') === '1';

  const [doc, setDoc] = useState<DocumentDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const start = Date.now();

    const tick = async () => {
      try {
        const data = await getDocument(docId);
        if (cancelled) return;
        setDoc(data);
        setError(null);
        const status = data.latest_job?.status;
        const stillRunning = status === 'queued' || status === 'running';
        const expired = Date.now() - start > MAX_POLL_DURATION_MS;
        if (stillRunning && !expired) {
          timer = setTimeout(tick, POLL_INTERVAL_MS);
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
          return;
        }
        const message =
          err instanceof ApiError ? err.detail : '문서를 불러오지 못했습니다.';
        setError(message);
      }
    };

    tick();

    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
    };
  }, [docId]);

  if (notFound) {
    return (
      <main className="container mx-auto flex-1 px-4 py-16 md:px-6">
        <Card className="mx-auto max-w-md p-8 text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-muted">
            <AlertCircle className="h-6 w-6 text-muted-foreground" />
          </div>
          <h1 className="text-lg font-semibold text-foreground">문서를 찾을 수 없어요</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            삭제되었거나 잘못된 주소일 수 있습니다.
          </p>
          <div className="mt-6 flex justify-center gap-2">
            <Button asChild variant="outline">
              <Link href="/">홈으로</Link>
            </Button>
            <Button asChild>
              <Link href="/ingest">새 문서 업로드</Link>
            </Button>
          </div>
        </Card>
      </main>
    );
  }

  return (
    <main className="container mx-auto flex-1 px-4 py-8 md:px-6 md:py-12">
      <div className="mx-auto max-w-3xl space-y-6">
        {justDuplicated && (
          <DuplicatedBanner />
        )}
        {justUploaded && doc?.latest_job?.status === 'completed' && (
          <UploadedBanner />
        )}

        <HeroSearch />

        {doc ? (
          <>
            <DocSummaryHeader doc={doc} />
            <DocStatusSection doc={doc} />
            {doc.summary && <SummarySection summary={doc.summary} />}
            {doc.tags.length > 0 && <TagsSection tags={doc.tags} />}
            <FlagsSection doc={doc} />
          </>
        ) : error ? (
          <ErrorCard message={error} />
        ) : (
          <DocSkeleton />
        )}
      </div>
    </main>
  );
}

// =====================================================
// Hero 검색 (전역, DE-27)
// =====================================================
function HeroSearch() {
  const router = useRouter();
  const [q, setQ] = useState('');
  const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmed = q.trim();
    if (!trimmed) return;
    router.push(`/search?q=${encodeURIComponent(trimmed)}`);
  };
  return (
    <form onSubmit={handleSubmit} className="relative">
      <Search className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-muted-foreground" />
      <Input
        type="search"
        placeholder="다른 문서에서 찾아보기"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        className="h-12 rounded-xl border-2 border-border bg-card pl-12 pr-24 text-sm shadow-sm focus:border-primary"
      />
      <Button
        type="submit"
        size="sm"
        className="absolute right-2 top-1/2 h-9 -translate-y-1/2 px-4"
      >
        검색
      </Button>
    </form>
  );
}

// =====================================================
// 헤더 — 제목 + 메타
// =====================================================
function DocSummaryHeader({ doc }: { doc: DocumentDetailResponse }) {
  const createdAt = new Date(doc.created_at).toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
  return (
    <header className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="outline">{docTypeLabel(doc.doc_type)}</Badge>
        <Badge variant="outline" className="text-muted-foreground">
          {doc.source_channel}
        </Badge>
        {doc.flags?.scan === true && (
          <Badge variant="secondary" className="gap-1">
            <Eye className="h-3 w-3" />
            스캔본
          </Badge>
        )}
        {doc.flags?.failed === true && (
          <Badge variant="destructive" className="gap-1">
            <XCircle className="h-3 w-3" /> 실패
          </Badge>
        )}
      </div>
      <h1 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">
        {doc.title}
      </h1>
      <div className="flex flex-wrap items-center gap-4 text-sm text-muted-foreground">
        <span>{formatBytes(doc.size_bytes)}</span>
        <span>·</span>
        <span>{createdAt}</span>
        <span>·</span>
        <span>청크 {doc.chunks_count}개</span>
        {doc.received_ms != null && (
          <>
            <span>·</span>
            <span>수신 {doc.received_ms}ms</span>
          </>
        )}
      </div>
      {doc.source_url && (
        <a
          href={doc.source_url}
          target="_blank"
          rel="noreferrer noopener"
          className="inline-flex items-center gap-1 text-sm text-primary hover:underline"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          원본 페이지 열기
        </a>
      )}
    </header>
  );
}

// =====================================================
// 상태 (인제스트 진행 / 완료 / 실패)
// =====================================================
function DocStatusSection({ doc }: { doc: DocumentDetailResponse }) {
  const job = doc.latest_job;
  if (!job) return null;
  if (job.status === 'completed') {
    return (
      <Card className="flex items-center gap-3 border-success/30 bg-success/5 p-4">
        <CheckCircle2 className="h-5 w-5 text-success" />
        <p className="text-sm text-foreground">
          처리 완료 — 검색에서 이 문서를 찾을 수 있어요.
        </p>
      </Card>
    );
  }
  if (job.status === 'failed') {
    return (
      <Card className="space-y-2 border-destructive/30 bg-destructive/5 p-4">
        <div className="flex items-center gap-2">
          <XCircle className="h-5 w-5 text-destructive" />
          <p className="text-sm font-medium text-destructive">
            처리 실패 ({job.current_stage ?? '?'} 단계)
          </p>
        </div>
        {job.error_msg && (
          <p className="rounded-md border border-destructive/20 bg-card px-3 py-2 text-xs text-destructive">
            {job.error_msg}
          </p>
        )}
      </Card>
    );
  }
  // queued / running
  return (
    <Card className="flex items-center gap-3 border-primary/30 bg-primary/5 p-4">
      <Loader2 className="h-5 w-5 animate-spin text-primary" />
      <div className="text-sm text-foreground">
        <p className="font-medium">처리 중</p>
        <p className="text-muted-foreground">
          현재 단계: {job.current_stage ?? '대기 중'}
        </p>
      </div>
    </Card>
  );
}

// =====================================================
// 요약·태그·플래그 섹션
// =====================================================
function SummarySection({ summary }: { summary: string }) {
  return (
    <Card className="p-5">
      <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Sparkles className="h-4 w-4 text-primary" />
        요약
      </div>
      <p className="whitespace-pre-line text-sm leading-relaxed text-foreground/90">
        {summary}
      </p>
    </Card>
  );
}

function TagsSection({ tags }: { tags: string[] }) {
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Tag className="h-4 w-4 text-primary" />
        태그
      </div>
      <div className="flex flex-wrap gap-2">
        {tags.map((t) => (
          <Link key={t} href={`/search?q=${encodeURIComponent(t)}`}>
            <Badge variant="secondary" className="cursor-pointer hover:bg-secondary/80">
              #{t}
            </Badge>
          </Link>
        ))}
      </div>
    </Card>
  );
}

function FlagsSection({ doc }: { doc: DocumentDetailResponse }) {
  const f = doc.flags || {};
  const items: { label: string; tone: 'warn' | 'info' }[] = [];
  if (f.has_pii === true) items.push({ label: '개인정보 포함', tone: 'warn' });
  if (f.has_watermark === true) {
    const hits = Array.isArray(f.watermark_hits) ? (f.watermark_hits as string[]) : [];
    items.push({
      label: hits.length > 0 ? `워터마크: ${hits.join(', ')}` : '워터마크 감지',
      tone: 'warn',
    });
  }
  if (f.third_party === true) items.push({ label: '제3자 대화 감지', tone: 'warn' });
  if (f.scan === true) items.push({ label: '스캔본 (Vision OCR 사용)', tone: 'info' });
  if (items.length === 0) return null;
  return (
    <Card className="p-5">
      <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground">
        <Shield className="h-4 w-4 text-warning" />
        주의사항
      </div>
      <ul className="space-y-2">
        {items.map((it, i) => (
          <li key={i} className="flex items-start gap-2 text-sm text-foreground/90">
            <Info
              className={`mt-0.5 h-4 w-4 flex-shrink-0 ${
                it.tone === 'warn' ? 'text-warning' : 'text-muted-foreground'
              }`}
            />
            {it.label}
          </li>
        ))}
      </ul>
    </Card>
  );
}

// =====================================================
// 배너 (중복 / 업로드 완료) + 스켈레톤 + 에러
// =====================================================
function DuplicatedBanner() {
  return (
    <Card className="flex items-start gap-3 border-warning/40 bg-warning/10 p-4">
      <Info className="mt-0.5 h-4 w-4 flex-shrink-0 text-warning" />
      <p className="text-sm text-foreground">
        이미 같은 내용이 등록되어 있어요. 기존 문서로 이동했습니다.
      </p>
    </Card>
  );
}

function UploadedBanner() {
  return (
    <Card className="flex items-start gap-3 border-success/40 bg-success/10 p-4">
      <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-success" />
      <p className="text-sm text-foreground">
        업로드가 완료되어 이 문서로 이동했어요. 이제 검색에서 찾을 수 있습니다.
      </p>
    </Card>
  );
}

function ErrorCard({ message }: { message: string }) {
  return (
    <Card className="space-y-2 border-destructive/30 bg-destructive/5 p-4">
      <div className="flex items-center gap-2">
        <XCircle className="h-5 w-5 text-destructive" />
        <p className="text-sm font-medium text-destructive">
          문서를 불러오지 못했어요
        </p>
      </div>
      <p className="text-xs text-muted-foreground">{message}</p>
    </Card>
  );
}

function DocSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <div className="flex gap-2">
          <Skeleton className="h-5 w-12" />
          <Skeleton className="h-5 w-16" />
        </div>
        <Skeleton className="h-9 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
      </div>
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}

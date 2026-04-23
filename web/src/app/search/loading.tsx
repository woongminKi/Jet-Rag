import { Skeleton } from '@/components/ui/skeleton';

export default function SearchLoading() {
  return (
    <main className="flex-1">
      <div className="sticky top-16 z-40 border-b border-border bg-card/95 backdrop-blur">
        <div className="container mx-auto flex items-center gap-3 px-4 py-3 md:px-6">
          <Skeleton className="h-9 w-9 rounded-md" />
          <Skeleton className="h-10 flex-1 max-w-2xl rounded-md" />
          <Skeleton className="hidden h-6 w-32 sm:inline-block" />
        </div>
      </div>
      <div className="container mx-auto px-4 py-6 md:px-6">
        <div className="grid gap-6 lg:grid-cols-[260px_1fr]">
          <div className="hidden space-y-4 lg:block">
            <Skeleton className="h-32 w-full rounded-lg" />
            <Skeleton className="h-32 w-full rounded-lg" />
          </div>
          <div className="space-y-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-44 w-full rounded-lg" />
            ))}
          </div>
        </div>
      </div>
    </main>
  );
}

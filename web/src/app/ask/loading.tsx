import { Skeleton } from '@/components/ui/skeleton';

export default function AskLoading() {
  return (
    <main className="flex-1">
      <div className="border-b border-border bg-card/95 backdrop-blur">
        <div className="container mx-auto flex items-center gap-2 px-4 py-4 md:px-6">
          <Skeleton className="h-5 w-5 rounded-md" />
          <Skeleton className="h-6 flex-1 max-w-md rounded-md" />
        </div>
      </div>
      <div className="container mx-auto px-4 py-6 md:px-6">
        <section className="mx-auto max-w-3xl space-y-6">
          <Skeleton className="h-32 w-full rounded-lg" />
          <div className="space-y-3">
            <Skeleton className="h-4 w-24" />
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-20 w-full rounded-md" />
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}

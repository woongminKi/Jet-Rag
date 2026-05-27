import { Clock } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

export function RecentlyViewedCard() {
  return (
    <Card className="overflow-hidden rounded-2xl">
      <CardHeader className="pb-3">
        <CardTitle className="flex flex-wrap items-center gap-2 text-lg font-semibold">
          <Clock className="h-5 w-5 shrink-0 text-muted-foreground" />
          <span className="break-words">최근 열람 문서</span>
          <Badge variant="outline" className="ml-auto sm:ml-1">
            곧 활성화
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="break-words rounded-xl bg-muted/50 p-4 text-sm text-muted-foreground">
          최근 열람 기능은 Day 7 이후에 활성화됩니다. 그 전까지는 상단의 검색 또는 최근 추가 카드를 활용해 주세요.
        </p>
      </CardContent>
    </Card>
  );
}

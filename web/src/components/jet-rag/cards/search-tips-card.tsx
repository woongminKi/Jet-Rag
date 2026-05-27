import { Lightbulb } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';

export function SearchTipsCard() {
  return (
    <Card className="overflow-hidden rounded-2xl bg-secondary/30">
      <CardContent>
        <h3 className="mb-2 flex items-center gap-1.5 text-base font-semibold">
          <Lightbulb className="h-4 w-4 shrink-0 text-warning" />
          <span className="break-words">검색 팁</span>
        </h3>
        <ul className="space-y-1.5 break-words text-sm text-muted-foreground">
          <li>
            • 자연어로 물어보세요. 예) <em>&quot;지난달 반도체 보고서&quot;</em>
          </li>
          <li>• 숫자·고유명사를 섞으면 더 정확해요.</li>
          <li>• 인기 태그 칩을 클릭하면 빠르게 필터링됩니다.</li>
        </ul>
      </CardContent>
    </Card>
  );
}

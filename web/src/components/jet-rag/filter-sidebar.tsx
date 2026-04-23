import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

const SECTIONS: Array<{ title: string; items: string[] }> = [
  { title: '문서 타입', items: ['PDF', 'HWP/HWPX', 'DOCX', '이미지', '메모'] },
  { title: '기간', items: ['오늘', '이번 주', '이번 달', '이번 년'] },
  { title: '태그', items: ['상위 태그를 누르면 검색됨'] },
];

export function FilterSidebar() {
  return (
    <aside className="hidden lg:block">
      <div className="sticky top-32 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-foreground">필터</h2>
          <Badge variant="outline" className="text-[10px]">
            준비 중
          </Badge>
        </div>
        {SECTIONS.map((section) => (
          <Card key={section.title} className="opacity-60">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-medium text-muted-foreground">
                {section.title}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="space-y-2">
                {section.items.map((item) => (
                  <li
                    key={item}
                    className="flex items-center gap-2 text-xs text-muted-foreground"
                  >
                    <span className="inline-block h-3.5 w-3.5 rounded-sm border border-border" />
                    {item}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        ))}
      </div>
    </aside>
  );
}

import type { DocType } from '@/lib/api';

const LABELS: Record<DocType, string> = {
  pdf: 'PDF',
  hwp: 'HWP',
  hwpx: 'HWPX',
  docx: 'DOCX',
  pptx: 'PPTX',
  image: '이미지',
  url: 'URL',
  txt: 'TXT',
  md: 'MD',
};

export function docTypeLabel(t: DocType | string): string {
  return LABELS[t as DocType] ?? t.toUpperCase();
}

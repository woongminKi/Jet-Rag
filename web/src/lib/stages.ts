import type { StageValue } from '@/lib/api';

export const STAGE_ORDER: StageValue[] = [
  'extract',
  'chunk',
  'tag_summarize',
  'load',
  'embed',
  'doc_embed',
  'dedup',
];

export const STAGE_LABELS: Record<StageValue, string> = {
  extract: '추출',
  chunk: '청킹',
  tag_summarize: '태그·요약',
  load: '적재',
  embed: '임베딩',
  doc_embed: '문서 벡터',
  dedup: '중복 감지',
};

export const ACCEPTED_EXTENSIONS = [
  '.pdf',
  '.hwp',
  '.hwpx',
  '.docx',
  '.pptx',
  '.jpg',
  '.jpeg',
  '.png',
  '.heic',
  '.txt',
  '.md',
] as const;

export const ACCEPT_ATTRIBUTE = ACCEPTED_EXTENSIONS.join(',');

export function inferDocType(fileName: string): string {
  const ext = fileName.toLowerCase().split('.').pop() ?? '';
  if (['jpg', 'jpeg', 'png', 'heic'].includes(ext)) return '이미지';
  if (['hwp', 'hwpx'].includes(ext)) return 'HWP';
  if (ext === 'pdf') return 'PDF';
  if (ext === 'docx') return 'DOCX';
  if (ext === 'pptx') return 'PPTX';
  if (ext === 'md') return 'MD';
  if (ext === 'txt') return 'TXT';
  return '파일';
}

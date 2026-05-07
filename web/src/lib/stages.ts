import type { StageValue } from '@/lib/api';

// E1 1차 ship (2026-05-07) — backend `app/ingest/eta.py` 와 9 stage 정합.
// 기존 8 stage 에서 `chunk_filter` 누락이 카운터 "1/8" 와 backend "9 stage" 사이
// semantic mismatch 를 만들었음. 추가하여 user 가 보는 표시와 실 동작 일치.
export const STAGE_ORDER: StageValue[] = [
  'extract',
  'chunk',
  'chunk_filter',
  'content_gate',
  'tag_summarize',
  'load',
  'embed',
  'doc_embed',
  'dedup',
];

export const STAGE_LABELS: Record<StageValue, string> = {
  extract: '추출',
  chunk: '청킹',
  chunk_filter: '청크 필터',
  content_gate: '안전 검사',
  tag_summarize: '태그·요약',
  load: '적재',
  embed: '임베딩',
  doc_embed: '문서 벡터',
  dedup: '중복 감지',
  done: '완료',
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

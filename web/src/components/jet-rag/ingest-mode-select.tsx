'use client';

/**
 * S2 D3 (2026-05-09) — 운영 모드 (fast / default / precise) UI 토글.
 *
 * master plan §6 S2 D3. 사용자 결정:
 * - Q-S2-1: default = 'default' (page_cap 50)
 * - Q-S2-1c: upload form + doc 카드 재처리 옆 양쪽에 노출
 * - Q-S2-1f: localStorage `jetrag.ingest_mode.last` 로 prefill
 * - Q-S2-1g: precise 비용 안내는 inline only (confirm dialog X)
 *
 * 본 컴포넌트는 controlled — 부모가 value/onChange 보유 + localStorage 동기.
 */

import { useSyncExternalStore } from 'react';
import type { IngestMode } from '@/lib/api';
import { cn } from '@/lib/utils';

const STORAGE_KEY = 'jetrag.ingest_mode.last';

/** 외부 시스템 (localStorage) 변경 구독 — useSyncExternalStore 의 subscribe.
 *  탭 간 storage 이벤트도 자동 반영. SSR 시 noop. */
function _subscribeStorage(onChange: () => void): () => void {
  if (typeof window === 'undefined') return () => {};
  const handler = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY || e.key === null) onChange();
  };
  window.addEventListener('storage', handler);
  return () => window.removeEventListener('storage', handler);
}

function _readSnapshot(): IngestMode {
  if (typeof window === 'undefined') return 'default';
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === 'fast' || stored === 'default' || stored === 'precise') {
      return stored;
    }
  } catch {
    // private mode 등 — graceful fallback.
  }
  return 'default';
}

function _serverSnapshot(): IngestMode {
  return 'default';
}

/** 부모 컴포넌트가 mount 직후 last mode 를 가져오는 hook.
 *  - SSR: 항상 'default' 반환 (hydration mismatch 0)
 *  - CSR: localStorage 값 반환 + storage 이벤트 자동 구독
 *  React 19 `react-hooks/set-state-in-effect` 회피 — useEffect + setState 패턴 대체.
 */
export function useLastIngestMode(): IngestMode {
  return useSyncExternalStore(
    _subscribeStorage,
    _readSnapshot,
    _serverSnapshot,
  );
}

/** 모드별 라벨/힌트 카피. 명세 §6.1 정합. emoji 금지 (메모리 규칙). */
const MODE_OPTIONS: Array<{
  value: IngestMode;
  label: string;
  hint: string;
}> = [
  {
    value: 'fast',
    label: '빠른 (≤10페이지, 깊은 분석 생략 가능)',
    hint: '메모·짧은 문서용. 큰 PDF 의 후반부는 처리되지 않을 수 있어요.',
  },
  {
    value: 'default',
    label: '기본 (≤50페이지, 권장)',
    hint: '일반적인 자료에 안전한 기본값.',
  },
  {
    value: 'precise',
    label: '정밀 (페이지 무제한, 비용 한도까지)',
    hint: '큰 자료를 끝까지 분석. 일일 비용 한도가 먼저 닿으면 거기서 멈춰요.',
  },
];

interface IngestModeSelectProps {
  value: IngestMode;
  onChange: (next: IngestMode) => void;
  /** select 자체 disabled (예: 진행 중 doc 의 재처리 버튼 옆). */
  disabled?: boolean;
  /** 라벨 표시 위치/크기 조정용 className. */
  className?: string;
  /** 힌트 표시 여부. doc 카드처럼 좁은 영역은 false. default true. */
  showHint?: boolean;
  /** select element 의 id (label 연결용). 기본 'ingest-mode-select'. */
  id?: string;
}

/** localStorage 에서 동기 1회 read (lazy initial state 용). hook 이 아니라
 *  useSyncExternalStore subscription 비용을 피하고 싶을 때 사용. SSR 시 'default'. */
export function loadLastIngestMode(): IngestMode {
  return _readSnapshot();
}

/** localStorage 에 mode 저장 (handler 안에서 동기 호출). React 19 set-state-in-effect
 *  회피 — useEffect 가 아니라 user interaction 시점에만 호출. SSR 시 noop. */
function _persistLastIngestMode(mode: IngestMode): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // private mode 등 — graceful skip.
  }
}

export function IngestModeSelect({
  value,
  onChange,
  disabled = false,
  className,
  showHint = true,
  id = 'ingest-mode-select',
}: IngestModeSelectProps) {
  // user interaction 시점에 동기 호출 — useEffect 안 setState lint rule 회피
  // (handler 안 동기 setState/sideEffect 는 OK, AGENTS.md 패턴 2).
  const handleChange = (next: IngestMode) => {
    _persistLastIngestMode(next);
    onChange(next);
  };

  const current = MODE_OPTIONS.find((o) => o.value === value) ?? MODE_OPTIONS[1];

  return (
    <div className={cn('space-y-1', className)}>
      <label
        htmlFor={id}
        className="block text-xs font-medium text-foreground"
      >
        처리 모드
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => handleChange(e.target.value as IngestMode)}
        disabled={disabled}
        className={cn(
          'w-full rounded-md border border-border bg-card px-2 py-1.5 text-sm text-foreground',
          'focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary',
          'disabled:cursor-not-allowed disabled:opacity-60',
        )}
      >
        {MODE_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
      {showHint && (
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {current.hint}
        </p>
      )}
    </div>
  );
}

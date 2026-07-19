'use client';

import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

const THEME_STORAGE_KEY = 'theme';

function isDarkApplied() {
  return document.documentElement.classList.contains('dark');
}

function applyTheme(dark: boolean) {
  document.documentElement.classList.toggle('dark', dark);
  window.localStorage.setItem(THEME_STORAGE_KEY, dark ? 'dark' : 'light');
}

interface ThemeToggleProps {
  /** 'icon' = 데스크톱 헤더 아이콘 버튼(40px). 'full' = 모바일 패널 전체폭 버튼(라벨 포함). */
  variant?: 'icon' | 'full';
  className?: string;
  onToggle?: () => void;
}

/**
 * design.md §8.3 — 시스템 설정(prefers-color-scheme) 기본 + 수동 오버라이드(localStorage) 조합.
 * 실제 다크/라이트 판정은 layout.tsx 의 THEME_INIT_SCRIPT(FOUC 방지, 하이드레이션 전 실행)가
 * <html> 에 이미 반영해둔 클래스를 그대로 읽는다 — 이 컴포넌트는 토글 UI + state 동기화만 담당.
 *
 * hydration mismatch 회피: 서버는 테마를 모르므로 mount 전에는 항상 동일한 초기값(Moon·disabled)을
 * 렌더 — 서버 렌더와 최초 클라이언트 렌더가 일치해 경고 없음. mount 후 useEffect 가 실제 DOM 상태로 갱신.
 */
export function ThemeToggle({ variant = 'icon', className, onToggle }: ThemeToggleProps) {
  const [mounted, setMounted] = useState(false);
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    // AGENTS.md #2 — useEffect 본문 안 동기 setState 는 lint 에러. .then() 콜백은 비동기라 허용.
    Promise.resolve().then(() => {
      setIsDark(isDarkApplied());
      setMounted(true);
    });
  }, []);

  const handleClick = () => {
    const next = !isDark;
    applyTheme(next);
    setIsDark(next);
    onToggle?.();
  };

  const label = isDark ? '라이트 모드로 전환' : '다크 모드로 전환';
  const Icon = isDark ? Sun : Moon;

  if (variant === 'full') {
    return (
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={handleClick}
        disabled={!mounted}
        // 터치 타깃 40px 이상 (design.md §4) — size="sm" 기본 h-8(32px) 를 h-10 으로 override.
        className={cn('h-10 w-full gap-2', className)}
      >
        <Icon className="h-4 w-4" />
        {label.replace('로 전환', '')}
      </Button>
    );
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-lg"
      onClick={handleClick}
      disabled={!mounted}
      aria-label={label}
      title={label}
      className={className}
    >
      <Icon className="h-4 w-4" />
    </Button>
  );
}

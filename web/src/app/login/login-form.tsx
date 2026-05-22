'use client';

import { useActionState } from 'react';
import { LogIn } from 'lucide-react';
import {
  signInWithGoogle,
  signInWithPassword,
  type AuthActionState,
} from '@/lib/auth/actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

/**
 * D1 Phase B — 로그인 폼 (Email/PW + Google).
 *
 * server action(signInWithPassword) + useActionState 로 에러 표시. Google 버튼은
 * 별도 form action — 콜백이 code exchange 후 세션 수립.
 */
const INITIAL: AuthActionState = { error: null };

export function LoginForm({ returnTo }: { returnTo: string }) {
  const [state, formAction, pending] = useActionState(
    signInWithPassword,
    INITIAL,
  );

  return (
    <div className="space-y-4">
      <form action={formAction} className="space-y-3">
        <input type="hidden" name="returnTo" value={returnTo} />
        <div className="space-y-1.5">
          <label htmlFor="email" className="text-sm font-medium text-foreground">
            이메일
          </label>
          <Input
            id="email"
            name="email"
            type="email"
            autoComplete="email"
            required
            placeholder="you@example.com"
          />
        </div>
        <div className="space-y-1.5">
          <label
            htmlFor="password"
            className="text-sm font-medium text-foreground"
          >
            비밀번호
          </label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
          />
        </div>

        {state.error && (
          <p className="text-sm text-destructive" role="alert">
            {state.error}
          </p>
        )}

        <Button type="submit" className="w-full gap-2" disabled={pending}>
          <LogIn className="h-4 w-4" />
          {pending ? '로그인 중…' : '로그인'}
        </Button>
      </form>

      <div className="relative">
        <div className="absolute inset-0 flex items-center">
          <span className="w-full border-t border-border" />
        </div>
        <div className="relative flex justify-center text-xs">
          <span className="bg-card px-2 text-muted-foreground">또는</span>
        </div>
      </div>

      <form action={signInWithGoogle}>
        <input type="hidden" name="returnTo" value={returnTo} />
        <Button type="submit" variant="outline" className="w-full">
          Google 계정으로 계속
        </Button>
      </form>
    </div>
  );
}

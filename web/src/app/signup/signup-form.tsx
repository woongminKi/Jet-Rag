'use client';

import { useActionState } from 'react';
import { UserPlus } from 'lucide-react';
import {
  signInWithGoogle,
  signUp,
  type AuthActionState,
} from '@/lib/auth/actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

/**
 * D1 Phase B — 가입 폼 (Email/PW + Google).
 *
 * W31 follow-up — 초대 코드 게이트 제거 (공개 가입). Email/PW 는 signUp, Google 은
 * signInWithGoogle. 둘 다 인증만 통과하면 즉시 사용 가능.
 */
const INITIAL: AuthActionState = { error: null };

export function SignupForm() {
  const [state, formAction, pending] = useActionState(signUp, INITIAL);

  return (
    <div className="space-y-4">
      <form action={formAction} className="space-y-3">
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
            autoComplete="new-password"
            required
            minLength={6}
          />
        </div>

        {state.error && (
          <p className="text-sm text-destructive" role="alert">
            {state.error}
          </p>
        )}

        <Button type="submit" className="w-full gap-2" disabled={pending}>
          <UserPlus className="h-4 w-4" />
          {pending ? '가입 중…' : '가입하기'}
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
        <Button type="submit" variant="outline" className="w-full">
          Google 계정으로 가입
        </Button>
      </form>
    </div>
  );
}

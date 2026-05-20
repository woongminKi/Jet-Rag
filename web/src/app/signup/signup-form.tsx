'use client';

import { useActionState, useState } from 'react';
import { UserPlus } from 'lucide-react';
import {
  signInWithGoogle,
  signUpWithInvite,
  type AuthActionState,
} from '@/lib/auth/actions';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

/**
 * D1 Phase B — 가입 폼 (Email/PW + 초대 코드 + Google).
 *
 * Email/PW: signUpWithInvite (가입 직후 redeem 게이트, D1-Q5).
 * Google: signInWithGoogle 에 inviteCode 전달 → server action 이 pending 쿠키 보관 후
 *   OAuth 시작 → 콜백이 그 코드로 redeem (plan §1.1).
 *
 * 초대 코드를 controlled state 로 유지해 두 form 이 같은 값을 공유한다.
 */
const INITIAL: AuthActionState = { error: null };

export function SignupForm() {
  const [state, formAction, pending] = useActionState(
    signUpWithInvite,
    INITIAL,
  );
  const [inviteCode, setInviteCode] = useState('');

  return (
    <div className="space-y-4">
      <form action={formAction} className="space-y-3">
        <div className="space-y-1.5">
          <label
            htmlFor="inviteCode"
            className="text-sm font-medium text-foreground"
          >
            초대 코드
          </label>
          <Input
            id="inviteCode"
            name="inviteCode"
            value={inviteCode}
            onChange={(e) => setInviteCode(e.target.value)}
            required
            placeholder="발급받은 초대 코드"
          />
        </div>
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
        <input type="hidden" name="inviteCode" value={inviteCode} />
        <Button type="submit" variant="outline" className="w-full">
          Google 계정으로 가입
        </Button>
      </form>
      <p className="text-center text-xs text-muted-foreground">
        Google 가입 시에도 초대 코드가 필요합니다.
      </p>
    </div>
  );
}

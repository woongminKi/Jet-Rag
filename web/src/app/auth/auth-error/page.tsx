import Link from 'next/link';
import { AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';

/**
 * D1 Phase B — 인증 실패 안내 (공개 경로).
 * reason 별 사용자 노출용 한국어 메시지.
 */
interface AuthErrorPageProps {
  searchParams: Promise<{ reason?: string }>;
}

const REASON_MESSAGES: Record<string, string> = {
  no_invite: '초대 코드가 필요합니다. 가입 시 발급받은 코드를 입력해 주세요.',
  invite: '초대 코드 검증에 실패했습니다. 코드가 유효한지 확인해 주세요.',
  exchange: '로그인 처리에 실패했습니다. 다시 시도해 주세요.',
  missing_code: '인증 정보가 올바르지 않습니다. 다시 시도해 주세요.',
  oauth: 'Google 로그인을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요.',
  config: '인증 설정이 완료되지 않았습니다. 잠시 후 다시 시도해 주세요.',
};

const DEFAULT_MESSAGE = '로그인 중 문제가 발생했습니다. 다시 시도해 주세요.';

export default async function AuthErrorPage({
  searchParams,
}: AuthErrorPageProps) {
  const { reason } = await searchParams;
  const message = (reason && REASON_MESSAGES[reason]) || DEFAULT_MESSAGE;
  const needsInvite = reason === 'no_invite' || reason === 'invite';

  return (
    <main className="flex flex-1 items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm space-y-6 rounded-xl border border-border bg-card p-6 text-center shadow-sm">
        <div className="flex flex-col items-center gap-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-destructive/10 text-destructive">
            <AlertCircle className="h-5 w-5" />
          </div>
          <h1 className="text-lg font-semibold text-foreground">로그인 실패</h1>
          <p className="text-sm text-muted-foreground">{message}</p>
        </div>

        <div className="flex flex-col gap-2">
          {needsInvite && (
            <Button asChild className="w-full">
              <Link href="/signup">초대 코드로 가입하기</Link>
            </Button>
          )}
          <Button asChild variant="outline" className="w-full">
            <Link href="/login">로그인으로 돌아가기</Link>
          </Button>
        </div>
      </div>
    </main>
  );
}

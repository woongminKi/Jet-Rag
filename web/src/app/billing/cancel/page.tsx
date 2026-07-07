import Link from 'next/link';

export default function BillingCancelPage() {
  return (
    <main className="mx-auto max-w-md px-4 py-16 text-center">
      <h1 className="text-xl font-bold">결제를 취소했습니다</h1>
      <p className="mt-2 text-sm text-gray-500">언제든지 다시 구독할 수 있습니다.</p>
      <Link href="/settings" className="mt-4 inline-block rounded border px-4 py-2 text-sm">
        설정으로 돌아가기
      </Link>
    </main>
  );
}

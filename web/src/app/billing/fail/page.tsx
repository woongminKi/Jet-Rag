import Link from 'next/link';

export default function BillingFailPage() {
  return (
    <main className="mx-auto max-w-md px-4 py-16 text-center">
      <h1 className="text-xl font-bold">결제에 실패했습니다</h1>
      <p className="mt-2 text-sm text-gray-500">
        결제가 처리되지 않았습니다. 잠시 후 다시 시도해 주세요.
      </p>
      <Link href="/settings" className="mt-4 inline-block rounded border px-4 py-2 text-sm">
        설정으로 돌아가기
      </Link>
    </main>
  );
}

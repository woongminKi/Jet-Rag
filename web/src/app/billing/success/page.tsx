import { BillingApprove } from './billing-approve';

// Next 16 — searchParams 는 Promise. await 후 client 자식에 전달.
export default async function BillingSuccessPage({
  searchParams,
}: {
  searchParams: Promise<{ pg_token?: string }>;
}) {
  const { pg_token } = await searchParams;
  return <BillingApprove pgToken={pg_token ?? null} />;
}

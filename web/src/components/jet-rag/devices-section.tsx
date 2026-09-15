'use client';

import { useEffect, useState } from 'react';
import { ApiError, apiDelete, apiGet, apiPostJson } from '@/lib/api/client';

interface Device {
  id: string;
  name: string;
  token_prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

/**
 * 4xx 는 서버가 쓴 한국어 detail 을 그대로 보여준다 — "1~60자여야 합니다", "최대 20개"처럼
 * 사용자가 바로 고칠 수 있는 내용이다. 5xx 는 내부 사정이라 일반 문구로 덮는다.
 */
function messageOf(e: unknown, fallback: string): string {
  return e instanceof ApiError && e.status < 500 ? e.detail : fallback;
}

function formatWhen(iso: string | null): string {
  if (!iso) return '아직 사용 안 함';
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? '-'
    : d.toLocaleString('ko-KR', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      });
}

export function DevicesSection() {
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [name, setName] = useState('');
  const [issued, setIssued] = useState<{ name: string; token: string } | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    apiGet<{ devices: Device[] }>('/me/devices')
      .then((r) => {
        setDevices(r.devices);
        setError(null);
      })
      .catch(() => setError('기기 목록을 불러오지 못했습니다.'));

  useEffect(() => {
    void load();
  }, []);

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const d = await apiPostJson<Device & { token: string }>('/me/devices', {
        name: name.trim(),
      });
      setIssued({ name: d.name, token: d.token });
      setCopied(false);
      setName('');
      await load();
    } catch (e) {
      setError(messageOf(e, '기기 추가에 실패했습니다. 잠시 후 다시 시도해 주세요.'));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (d: Device) => {
    if (
      !window.confirm(
        `"${d.name}" 의 토큰을 폐기하면 그 기기는 즉시 업로드가 막힙니다. 계속할까요?`,
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      await apiDelete(`/me/devices/${d.id}`);
      await load();
    } catch (e) {
      setError(messageOf(e, '폐기에 실패했습니다. 잠시 후 다시 시도해 주세요.'));
    } finally {
      setBusy(false);
    }
  };

  const active = (devices ?? []).filter((d) => !d.revoked_at);

  return (
    <section className="mt-6 rounded-lg border p-4">
      <h2 className="font-semibold">연결된 기기</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        PC 에이전트·단축어·앱이 문서를 자동으로 올릴 때 쓰는 토큰입니다. 기기별로 폐기할 수
        있습니다.
      </p>
      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      {issued && (
        <div className="mt-3 rounded border border-amber-400 bg-amber-50 p-3 text-sm dark:bg-amber-950">
          <p>
            <strong>{issued.name}</strong> 토큰입니다. <strong>지금 한 번만</strong> 표시됩니다 —
            복사해 기기에 넣어 주세요.
          </p>
          <code className="mt-2 block select-all break-all rounded bg-background px-3 py-2">
            {issued.token}
          </code>
          <button
            type="button"
            className="mt-2 rounded border px-3 py-1"
            onClick={() => {
              // http 나 권한 거부 환경에서는 clipboard 자체가 없다 — 조용히 넘기면
              // 사용자는 복사됐다고 믿고 창을 닫는다(토큰은 다시 못 본다).
              const cb = navigator.clipboard;
              if (!cb) {
                setError('복사에 실패했습니다. 아래 토큰을 직접 선택해 복사해 주세요.');
                return;
              }
              void cb
                .writeText(issued.token)
                .then(() => setCopied(true))
                .catch(() =>
                  setError('복사에 실패했습니다. 아래 토큰을 직접 선택해 복사해 주세요.'),
                );
            }}
          >
            {copied ? '복사됨' : '복사'}
          </button>
          <button
            type="button"
            className="ml-2 mt-2 rounded border px-3 py-1"
            onClick={() => setIssued(null)}
          >
            확인했습니다
          </button>
        </div>
      )}

      <div className="mt-3 flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="기기 이름 (예: 회사 노트북)"
          maxLength={60}
          className="flex-1 rounded border px-3 py-1 text-sm"
        />
        <button
          type="button"
          onClick={() => void create()}
          disabled={busy || !name.trim()}
          className="rounded bg-primary px-3 py-1 text-sm text-primary-foreground disabled:opacity-50"
        >
          기기 추가
        </button>
      </div>

      {devices === null ? (
        <p className="mt-2 text-sm text-gray-500">불러오는 중…</p>
      ) : active.length === 0 ? (
        <p className="mt-2 text-sm text-gray-500">연결된 기기가 없습니다.</p>
      ) : (
        <ul className="mt-3 divide-y text-sm">
          {active.map((d) => (
            <li key={d.id} className="flex items-center justify-between py-2">
              <div>
                <div className="font-medium">{d.name}</div>
                <div className="text-xs text-muted-foreground">
                  {d.token_prefix}… · 마지막 사용 {formatWhen(d.last_used_at)}
                </div>
              </div>
              <button
                type="button"
                onClick={() => void revoke(d)}
                disabled={busy}
                className="rounded border px-2 py-1 text-xs disabled:opacity-50"
              >
                폐기
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

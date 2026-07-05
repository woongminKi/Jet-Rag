/**
 * W28 — Android Web Share Target 진입점.
 *
 * 흐름:
 *   1. Android Chrome PWA 가 설치되면 manifest 의 `share_target.action=/share` 가
 *      OS 공유 시트에 "Jet-Rag" 항목으로 등록된다 (Adobe Acrobat 등에서 직접 공유 가능).
 *   2. 사용자가 PDF 를 공유하면 브라우저는 `POST /share` 로 multipart/form-data 를 보낸다.
 *   3. 본 route 가 검증 후 동일 multipart 를 백엔드 `POST {API_BASE}/documents` 로 forward 한다.
 *
 * 안전 조건:
 *   - POST only — GET 은 /docs 로 redirect (PC 사용자가 URL 직접 타이핑해도 무해).
 *   - Service Worker 없음 — fetch 캐싱 안 함, PC 검색/답변 결과 stale 위험 0.
 *   - 백엔드 0 수정 — 본 route 가 단순 forward 만 수행, 기존 /documents 라우터 재사용.
 *
 * iOS 한계:
 *   - iOS Safari 는 PWA share_target 미지원 (2026-05 기준 ITP 정책 유지).
 *   - iOS 사용자는 단축어 앱(/work-log/2026-05-28 iOS Shortcuts PDF 공유 가이드.md) 으로 우회.
 *
 * Portfolio Mode:
 *   - 현재 백엔드 ENV `JETRAG_DEMO_READONLY=true` 일 때 /documents POST 는 503 응답.
 *   - 본 route 는 503 을 그대로 client 로 전달하되, 본문 message 를 한국어로 wrap.
 *   - 추후 로그인 모드 복원 시 cookie forward 만 추가하면 됨 (아래 NOTE 참고).
 */

import { NextResponse } from 'next/server';

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000';
const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024; // 50MB — 기획서 §10 의 PDF 단일 업로드 한도.
const ACCEPTED_MIME = 'application/pdf';

/**
 * GET — PWA share_target 은 POST 만 사용. PC 사용자가 URL 직접 입력하거나
 * 검색엔진 크롤러가 진입하는 경우를 대비해 /docs 로 redirect (303 See Other).
 */
export function GET(request: Request): Response {
  return NextResponse.redirect(new URL('/docs', request.url), 303);
}

/**
 * POST — Android Chrome 의 web share target 호출 entry.
 *
 * manifest.json 의 `share_target.params.files[0].name=file` 와 일치하는 키로
 * 파일이 들어온다. 일부 브라우저는 `files` (복수형) 로 보내는 케이스가 있어 양쪽 시도.
 */
export async function POST(request: Request): Promise<Response> {
  let formData: FormData;
  try {
    formData = await request.formData();
  } catch {
    return jsonError(400, '공유된 파일을 읽을 수 없습니다. 다시 시도하세요.');
  }

  const file = pickFile(formData);
  if (!file) {
    return jsonError(400, '공유된 항목에서 PDF 파일을 찾을 수 없습니다.');
  }

  if (file.type !== ACCEPTED_MIME) {
    return jsonError(400, 'PDF 파일만 업로드할 수 있습니다.');
  }

  if (file.size > MAX_FILE_SIZE_BYTES) {
    return jsonError(
      413,
      `파일 크기가 50MB 를 초과합니다 (받은 크기: ${(file.size / 1024 / 1024).toFixed(1)}MB).`,
    );
  }

  // 로그인 사용자의 세션 토큰을 백엔드로 forward (PWA share_target 업로드 인증).
  // 실제 쿠키는 `sb-<ref>-auth-token` 청크 분할 JSON — 추출은 기존 헬퍼 재사용.
  const { getServerForwardToken } = await import('@/lib/api/server-token');
  const accessToken = await getServerForwardToken();

  const forwardForm = new FormData();
  forwardForm.append('file', file, file.name || 'shared.pdf');

  const fetchHeaders: HeadersInit = {};
  if (accessToken) {
    fetchHeaders['Authorization'] = `Bearer ${accessToken}`;
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE_URL}/documents`, {
      method: 'POST',
      body: forwardForm,
      headers: fetchHeaders,
      cache: 'no-store',
    });
  } catch (err) {
    const reason = err instanceof Error ? err.message : 'unknown';
    return jsonError(
      502,
      `백엔드에 연결할 수 없습니다 (${reason}). 잠시 후 다시 시도하세요.`,
    );
  }

  // Portfolio Mode (503) — 사용자에게 친절한 한국어 메시지로 wrap.
  if (upstream.status === 503) {
    return jsonError(
      503,
      '현재 데모 모드입니다. 본인 로그인 후 다시 시도하세요.',
    );
  }

  if (upstream.ok) {
    // 업로드 성공 — 브라우저는 PWA 안에서 응답을 표시하지 못하니 /docs 로 redirect.
    return NextResponse.redirect(new URL('/docs', request.url), 303);
  }

  // 그 외 에러는 status 를 보존하고 백엔드 본문을 그대로 전달 (디버깅 용이).
  let detail = '업로드에 실패했습니다.';
  try {
    const body = (await upstream.json()) as { detail?: unknown };
    if (typeof body?.detail === 'string') {
      detail = body.detail;
    }
  } catch {
    // JSON 파싱 실패 — 기본 메시지 유지.
  }
  return jsonError(upstream.status, detail);
}

/**
 * manifest 가 `name: "file"` 로 선언했지만, 일부 Android 브라우저 (구버전 Samsung Internet 등)
 * 가 `files` 복수형으로 보내는 케이스가 보고되어 fallback 시도.
 */
function pickFile(formData: FormData): File | null {
  for (const key of ['file', 'files'] as const) {
    const value = formData.get(key);
    if (value instanceof File) {
      return value;
    }
  }
  return null;
}

function jsonError(status: number, message: string): Response {
  return NextResponse.json({ error: message }, { status });
}

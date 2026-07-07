// Jet-Rag W4 — Cloudflare Email Worker.
// Email Routing(catch-all @in.woong-s.com) → 본 Worker → 백엔드 POST /ingest/email.
// 검증(토큰·발신자·플랜)은 전부 백엔드가 담당 — Worker 는 파싱·전달만.
import PostalMime from 'postal-mime';

function toBase64(arrayBuffer) {
  // 대용량 첨부에서 String.fromCharCode(...spread) 는 스택 초과 — 청크 처리.
  const bytes = new Uint8Array(arrayBuffer);
  let binary = '';
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(binary);
}

export default {
  async email(message, env, ctx) {
    try {
      const email = await new PostalMime().parse(message.raw);
      const attachments = (email.attachments || []).map((a) => ({
        filename: a.filename || 'attachment',
        content_type: a.mimeType || 'application/octet-stream',
        content_base64: toBase64(a.content),
      }));
      const payload = {
        to: message.to,
        // 봉투(MAIL FROM)가 아니라 파싱된 From 헤더 — 포워딩 메일러 경유 시
        // 봉투 발신자가 달라져 화이트리스트가 오탐하므로 헤더를 우선한다.
        from: email.from?.address || message.from,
        subject: email.subject || '',
        attachments,
      };
      const resp = await fetch(`${env.JETRAG_API_URL}/ingest/email`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Jetrag-Webhook-Secret': env.JETRAG_EMAIL_WEBHOOK_SECRET,
        },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        console.error(`jetrag webhook 실패: ${resp.status} ${await resp.text()}`);
      }
    } catch (err) {
      // 실패해도 메일 반송(setReject)하지 않음 — 거절 정책 '조용히 무시'와 정합.
      console.error(`jetrag email worker 오류: ${err}`);
    }
  },
};

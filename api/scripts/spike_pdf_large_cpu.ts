/**
 * 대형 PDF 의 **문서 열기 비용**과 페이지당 CPU 를 잰다.
 *
 * ## 왜 다시 재는가
 * Phase 0 S2 는 "페이지당 CPU 최대 100.8ms" 로 PASS 했다. 그건 **7 페이지 문서**
 * 기준이다. 운영 chunk 의 99.3% 는 수백 페이지짜리 사업보고서에서 나온다.
 *
 * Edge 워커는 상태가 없어 **태스크마다 문서를 새로 연다.** 500 페이지 문서를 N 페이지씩
 * 나누면 문서 열기가 그만큼 반복된다. 열기가 비싸면 페이지 분할 설계가 통째로 바뀐다 —
 * 그래서 페이지당 CPU 가 아니라 **열기 : 추출 비율**이 진짜 질문이다.
 *
 * ## 로컬 값을 그대로 쓰지 않는다
 * Phase 0 의 교훈 그대로 로컬 Deno 통과는 Edge 통과의 근거가 아니다. 여기서는
 * `law sample3.pdf` p0 을 같이 재서 **Phase 0 Edge 실측(63.6ms)과의 배율**을 뽑고,
 * 대형 PDF 수치를 그 배율로 환산해 판단 재료로 쓴다.
 *
 * 사용:
 *   deno run --config supabase/functions/spike/deno.json --allow-all \
 *     api/scripts/spike_pdf_large_cpu.ts <pdf> [<pdf> ...]
 */

// deno-lint-ignore no-explicit-any
type Any = any;

/** CPU 시간만 재고 싶지만 Deno 에 프로세스 CPU 시계가 없다 — 벽시계로 재되 I/O 를 뺀다. */
function ms(): number {
  return performance.now();
}

function stat(xs: number[]) {
  const s = [...xs].sort((a, b) => a - b);
  const sum = s.reduce((a, b) => a + b, 0);
  return {
    n: s.length,
    mean: sum / s.length,
    p50: s[Math.floor(s.length * 0.5)],
    p95: s[Math.min(s.length - 1, Math.floor(s.length * 0.95))],
    max: s[s.length - 1],
  };
}

const mupdf = await import("mupdf") as Any;
const { toPageDict, STEXT_OPTS } = await import(
  new URL("../../supabase/functions/_shared/pdf_dict.ts", import.meta.url).href
) as Any;

const files = Deno.args;
if (files.length === 0) {
  console.error("PDF 경로를 인자로 주세요.");
  Deno.exit(2);
}

// 페이지가 많으면 전부 재지 않고 고르게 표본을 뽑는다. 앞·중간·뒤가 다 들어가야 한다.
const MAX_SAMPLE = 40;

for (const path of files) {
  const bytes = await Deno.readFile(path);
  const name = path.split("/").pop();

  // --- 1) 문서 열기 ---
  const openTimes: number[] = [];
  let pageCount = 0;
  for (let i = 0; i < 3; i++) {
    const t0 = ms();
    const doc = mupdf.Document.openDocument(bytes, "application/pdf");
    pageCount = doc.countPages();
    openTimes.push(ms() - t0);
    doc.destroy?.();
  }

  // --- 2) 페이지 추출 (loadPage + toStructuredText + walk 변환) ---
  const doc = mupdf.Document.openDocument(bytes, "application/pdf");
  const idxs: number[] = [];
  if (pageCount <= MAX_SAMPLE) {
    for (let i = 0; i < pageCount; i++) idxs.push(i);
  } else {
    for (let k = 0; k < MAX_SAMPLE; k++) {
      idxs.push(Math.floor((k * (pageCount - 1)) / (MAX_SAMPLE - 1)));
    }
  }

  const perPage: number[] = [];
  let blocks = 0;
  for (const i of idxs) {
    const t0 = ms();
    const page = doc.loadPage(i);
    const st = page.toStructuredText(STEXT_OPTS);
    const dict = toPageDict(st, page.getBounds());
    perPage.push(ms() - t0);
    blocks += dict.blocks.length;
    st.destroy?.();
    page.destroy?.();
  }
  doc.destroy?.();

  const o = stat(openTimes);
  const p = stat(perPage);
  console.log(JSON.stringify({
    file: name,
    size_mb: +(bytes.length / 1e6).toFixed(2),
    pages: pageCount,
    open_ms: { mean: +o.mean.toFixed(1), max: +o.max.toFixed(1) },
    page_ms: {
      n: p.n,
      mean: +p.mean.toFixed(1),
      p50: +p.p50.toFixed(1),
      p95: +p.p95.toFixed(1),
      max: +p.max.toFixed(1),
    },
    blocks_total: blocks,
    // 문서 전체를 한 태스크에서 처리했을 때의 추정 — 2s 예산과 바로 비교된다.
    est_full_doc_ms: +(o.mean + p.mean * pageCount).toFixed(0),
  }));
}

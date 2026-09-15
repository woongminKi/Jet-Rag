/**
 * `chunk` 창 분할 벤치 — 창 하나가 Edge 예산(CPU 2s · 메모리 256MB) 안에 드는지 잰다.
 *
 * 분석 때 scratchpad 에서 돌리던 스크립트를 상시화한 것이다. 예산을 넘긴 적이 있는
 * 코드라 수치를 **다시 잴 수 있어야** 한다 — 못 재면 회귀를 못 본다.
 *
 * 재는 것(패스 3개):
 * - (A) 같은 입력을 **한 번에** 처리 — 창 없음. 예전 동작이자 등가성 정답.
 * - (B) 창 단위 CPU ms. 등가성 대조를 위해 레코드를 **전부 들고 있는다**.
 * - (C) 창 단위 피크 heap. 레코드를 창마다 **버린다** — Edge 는 창마다 별도 실행이라
 *       한 창의 작업 세트만 메모리에 있다. (B) 의 heap 은 문서 전체를 쥐고 있어
 *       실제 Edge 수치가 아니다. 이걸 구분 안 하면 벤치가 거짓말을 한다.
 *
 * 사용:
 *   deno run --allow-read --allow-env _shared/ingest/chunk_bench.ts \
 *     --sections /tmp/bench_sections.json --window 4
 *
 *   # heap 수치는 **같은 프로세스에서 앞 패스가 남긴 쓰레기**에 오염된다.
 *   # 깨끗한 값이 필요하면 (C) 만 새 프로세스로 돌리거나 GC 를 열어 준다:
 *   deno run --allow-read --allow-env --v8-flags=--expose-gc \
 *     _shared/ingest/chunk_bench.ts --sections … --window 4 --pass mem
 *
 * 입력 JSON 은 `api/scripts/make_bench_sections.py` 가 만든다(운영과 같은 파서).
 *
 * `--pages-per-artifact` 는 `extract` 가 아티팩트 하나에 담는 페이지 수
 * (`pdf_extract.PDF_PAGES_PER_TASK`, 기본 10)다. 창 = 아티팩트 `--window` 개.
 *
 * ## 주의 — 랩탑 수치는 Edge 수치가 아니다
 * Edge vCPU 는 이보다 느리다. 창 하나가 랩탑 200ms 를 넘으면 Edge 2s 예산이 위태롭다고
 * 보는 게 분석 때의 기준이었다(전체 3,811ms 랩탑 → Edge 추정 8~15s).
 */

import type { ExtractedSection } from "./hwp_extract.ts";
import { type ChunkRecord, runChunkStage } from "./chunk_records.ts";
import { runContentGateStage } from "./content_gate.ts";
import { accumulateHfCounts, splitTailByPage } from "./chunk_window.ts";
import { headerFooterTexts } from "./chunk_filter.ts";

const ENV = {
  captionPrefixEnabled: false,
  synonymInjectionEnabled: false,
  synonymLlmEnabled: false,
};

function arg(name: string, fallback?: string): string {
  const i = Deno.args.indexOf(`--${name}`);
  if (i >= 0 && i + 1 < Deno.args.length) return Deno.args[i + 1];
  if (fallback !== undefined) return fallback;
  console.error(`--${name} 이 필요하다`);
  Deno.exit(2);
}

/** 숫자 인자. NaN 이면 즉시 죽는다 — `Number("4개")` 로 창 수가 NaN 이 되면 벤치가
 * 0 창을 돌고도 "통과" 처럼 보인다. */
function numArg(name: string, fallback: string): number {
  const raw = arg(name, fallback);
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 1) {
    console.error(`--${name} 은 1 이상의 수여야 한다 (받은 값: ${raw})`);
    Deno.exit(2);
  }
  return Math.floor(n);
}

function mb(n: number): string {
  return `${(n / 1e6).toFixed(0)}MB`;
}

/**
 * `--v8-flags=--expose-gc` 로 열어 줬으면 GC 를 부른다.
 *
 * 안 부르면 heap 수치가 **앞 패스의 쓰레기를 포함**한다. 실제로 그랬다 — 레코드를
 * 안 들고 도는 패스가 들고 도는 패스보다 heap 이 높게 나왔다. 자[尺]를 먼저 의심할 것.
 */
function maybeGc(): void {
  (globalThis as { gc?: () => void }).gc?.();
}

/** `extract` 아티팩트 흉내 — 페이지 `pagesPerArtifact` 개씩 한 행. */
function toArtifacts(sections: ExtractedSection[], pagesPerArtifact: number): ExtractedSection[][] {
  const out: ExtractedSection[][] = [];
  let cur: ExtractedSection[] = [];
  let lo: number | null = null;
  for (const s of sections) {
    const p = s.page;
    if (p !== null && p !== undefined) {
      if (lo === null) lo = p;
      if (p >= lo + pagesPerArtifact) {
        out.push(cur);
        cur = [];
        lo = p;
      }
    }
    cur.push(s);
  }
  if (cur.length > 0) out.push(cur);
  return out;
}

interface WindowStat {
  ms: number;
  records: number;
  heap: number;
  rss: number;
  carrySections: number;
  hfKeys: number;
  /** 이 창이 jsonb 로 쓰는 payload 바이트(레코드 + 캐리). */
  bytes: number;
  /** 마지막 창에서만 채운다 — 문서 전체 머리말 판정 개수. */
  hfTexts?: number;
}

function main(): void {
  const path = arg("sections");
  const perWindow = numArg("window", "4");
  const pagesPerArtifact = numArg("pages-per-artifact", "10");
  // `mem` 이면 (C) 만 돈다 — 앞 패스가 남긴 쓰레기 없이 heap 을 재려면 그래야 한다.
  const pass = arg("pass", "all");

  const sections: ExtractedSection[] = JSON.parse(Deno.readTextFileSync(path));
  const chars = sections.reduce((a, s) => a + s.text.length, 0);
  const artifacts = toArtifacts(sections, pagesPerArtifact);
  const totalWindows = Math.max(1, Math.ceil(artifacts.length / perWindow));
  console.log(
    `입력: 섹션 ${sections.length.toLocaleString()} · ${chars.toLocaleString()}자 · ` +
      `아티팩트 ${artifacts.length} (${pagesPerArtifact}p/행) → 창 ${totalWindows} (${perWindow}행/창)`,
  );

  // ── 2) 창 단위 ──
  /** `keep` 이 참이면 레코드를 모아 둔다(등가성 대조용). 거짓이면 창마다 버린다. */
  function runWindows(keep: boolean): { stats: WindowStat[]; got: ChunkRecord[] } {
    const stats: WindowStat[] = [];
    const got: ChunkRecord[] = [];
    let carry: ExtractedSection[] = [];
    let hfCounts: Record<string, number> = {};
    let idx = 0;
    for (let w = 0; w < totalWindows; w++) {
      const isLast = w === totalWindows - 1;
      const windowSections = artifacts.slice(w * perWindow, w * perWindow + perWindow).flat();
      if (!keep) {
        // 메모리 패스에서는 다 쓴 아티팩트를 **버린다**. Edge 는 창 하나치 섹션만
        // 읽으므로, 입력 전체를 쥐고 재면 rss 가 벤치 자신의 입력에 지배당한다.
        for (let a = w * perWindow; a < Math.min(artifacts.length, (w + 1) * perWindow); a++) {
          artifacts[a] = [];
        }
      }
      const s = performance.now();
      const combined = carry.concat(windowSections);
      const { head, tail } = isLast
        ? { head: combined, tail: [] as ExtractedSection[] }
        : splitTailByPage(combined);
      const records = runContentGateStage({
        chunks: runChunkStage({ docId: "d", sections: head, env: ENV, idxOffset: idx }),
      }).chunks;
      hfCounts = accumulateHfCounts(hfCounts, records);
      // 저장 직전 직렬화까지 창 비용이다 — Edge 는 이걸 jsonb 로 보낸다.
      const bytes = JSON.stringify({ records, carry: { sections: tail, hfCounts } }).length;
      const ms = performance.now() - s;

      idx += records.length;
      carry = tail;
      if (keep) got.push(...records);
      else maybeGc(); // 메모리 패스에서만 — CPU 패스에 GC 를 섞으면 ms 가 오염된다
      const mem = Deno.memoryUsage();
      stats.push({
        ms,
        records: records.length,
        heap: mem.heapUsed,
        rss: mem.rss,
        carrySections: tail.length,
        hfKeys: Object.keys(hfCounts).length,
        bytes,
        // 마지막 창만 — 문서 전체 판정이라 중간 창에서는 의미가 없다.
        hfTexts: isLast ? headerFooterTexts(Object.entries(hfCounts)).size : undefined,
      });
    }
    return { stats, got };
  }

  if (pass === "mem") {
    // 원본 배열을 놓아 준다 — 아티팩트가 같은 객체를 참조하므로 객체는 창마다 풀린다.
    sections.length = 0;
    const memOnly = runWindows(false).stats;
    const last = memOnly.at(-1)!;
    console.log(
      `창 단위 메모리(레코드 미보유): 피크 heap ${mb(Math.max(...memOnly.map((s) => s.heap)))} · ` +
        `피크 rss ${mb(Math.max(...memOnly.map((s) => s.rss)))}` +
        ((globalThis as { gc?: unknown }).gc ? "" : "  ⚠ --expose-gc 없음 — 상한값이다"),
    );
    console.log(
      `캐리: 꼬리 섹션 최대 ${Math.max(...memOnly.map((s) => s.carrySections))}개 · ` +
        `hfCounts 최종 ${last.hfKeys.toLocaleString()}키 · 머리말 판정 ${last.hfTexts ?? 0}개`,
    );
    return;
  }

  // ── 1) 전체를 한 번에 (창 없음) — 예전 동작. 비교 기준이자 등가성 정답. ──
  const t0 = performance.now();
  const wholeRaw = runChunkStage({ docId: "d", sections, env: ENV });
  const whole = runContentGateStage({ chunks: wholeRaw }).chunks;
  const wholeMs = performance.now() - t0;
  const wholeMem = Deno.memoryUsage();
  console.log(
    `전체 1회: ${wholeMs.toFixed(0)}ms · 청크 ${whole.length.toLocaleString()} · ` +
      `heap ${mb(wholeMem.heapUsed)} · rss ${mb(wholeMem.rss)}`,
  );

  const { stats, got } = runWindows(true);
  const msList = stats.map((s) => s.ms);
  const sum = msList.reduce((a, b) => a + b, 0);
  const worst = stats.reduce((a, b) => (b.ms > a.ms ? b : a));
  console.log(
    `창 단위 CPU: 합계 ${sum.toFixed(0)}ms · 창당 평균 ${(sum / stats.length).toFixed(0)}ms · ` +
      `최대 ${worst.ms.toFixed(0)}ms (청크 ${worst.records})`,
  );
  console.log(
    `창 산출물: 최대 ${mb(Math.max(...stats.map((s) => s.bytes)))} · ` +
      `합계 ${mb(stats.reduce((a, b) => a + b.bytes, 0))} (jsonb 로 쓰는 양)`,
  );

  // 느린 창 5개 — 어디가 무거운지 보려고 남긴다.
  const slow = [...stats.entries()].sort((a, b) => b[1].ms - a[1].ms).slice(0, 5);
  console.log(
    `느린 창: ${slow.map(([i, s]) => `#${i} ${s.ms.toFixed(0)}ms/${s.records}청크`).join("  ")}`,
  );

  // ── 3) 등가성 — 다르면 위 수치는 아무 의미가 없다. ──
  const same = got.length === whole.length &&
    JSON.stringify(got) === JSON.stringify(whole);
  console.log(
    `등가성: 창 ${got.length.toLocaleString()} vs 전체 ${whole.length.toLocaleString()} → ${
      same ? "identical" : "**다르다**"
    }`,
  );
  if (!same) Deno.exit(1);

  // ── 4) 메모리 패스 — 레코드를 안 들고 다시 돈다. Edge 한 창의 작업 세트다. ──
  got.length = 0;
  whole.length = 0;
  wholeRaw.length = 0;
  const memStats = runWindows(false).stats;
  console.log(
    `창 단위 메모리(레코드 미보유): 피크 heap ${mb(Math.max(...memStats.map((s) => s.heap)))} · ` +
      `피크 rss ${mb(Math.max(...memStats.map((s) => s.rss)))}` +
      "  ⚠ 앞 패스 쓰레기 포함 — 깨끗한 값은 `--pass mem` 을 새 프로세스로",
  );
  const last = memStats.at(-1)!;
  console.log(
    `캐리: 꼬리 섹션 최대 ${Math.max(...memStats.map((s) => s.carrySections))}개 · ` +
      `hfCounts 최종 ${last.hfKeys.toLocaleString()}키 · ` +
      `머리말 판정 ${last.hfTexts ?? 0}개`,
  );
}

if (import.meta.main) main();

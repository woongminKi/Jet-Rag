/**
 * Phase 0 / S1 — HWP WASM 출력 덤프.
 *
 * `@ohah/hwpjs` 의 **순수 WASM 빌드**(네이티브 .node 우회)로 HWP 를 파싱해 세 출력을
 * 그대로 파일에 떨군다. 품질 판정(파이썬 기준선 대비 유사도)은 `api/scripts/spike_hwp_similarity.py`
 * 가 이 산출물을 읽어서 한다 — 추출과 채점을 분리해야 추출기를 고칠 때마다 채점이 재현된다.
 *
 * 네이티브가 아니라 WASM 을 태우는 이유는 Edge Functions(Linux, 네이티브 애드온 불가)에서
 * 실제로 돌 경로가 WASM 뿐이기 때문이다. 여기서 나온 품질 수치만이 이관 판정에 유효하다.
 *
 * 사용:
 *   cd scripts && deno run -A spike_hwp_extract.ts <hwp파일> [출력디렉토리]
 */

import { toHtml, toJson, toMarkdown } from "@ohah/hwpjs-wasm32-wasi";
import { extractParagraphs } from "../supabase/functions/_shared/hwp_text.ts";

const src = Deno.args[0];
if (!src) {
  console.error("사용법: deno run -A spike_hwp_extract.ts <hwp파일> [출력디렉토리]");
  Deno.exit(2);
}
const outDir = Deno.args[1] ?? "./spike_out";
await Deno.mkdir(outDir, { recursive: true });

const bytes = await Deno.readFile(src);
console.error(`샘플: ${src} (${bytes.byteLength} bytes) → ${outDir}`);

/** 반환 타입이 string 인지 객체인지가 그 자체로 정보다 — 강제로 문자열화하기 전에 기록한다. */
function dump(name: string, ext: string, fn: (b: Uint8Array) => unknown) {
  const t0 = performance.now();
  const raw = fn(bytes);
  const ms = performance.now() - t0;
  const kind = typeof raw;
  const text = kind === "string" ? raw as string : JSON.stringify(raw, null, 2);
  const path = `${outDir}/${name}.${ext}`;
  Deno.writeTextFileSync(path, text);
  console.error(`${name.padEnd(12)} type=${kind} chars=${text.length} ms=${ms.toFixed(1)} → ${path}`);
}

dump("toJson", "json", toJson);
dump("toHtml", "html", toHtml);
dump("toMarkdown", "md", toMarkdown);

// 판정 대상 — toJson 위에 얹은 자체 추출기(_shared/hwp_text.ts). Edge 에서 쓸 코드 그대로다.
{
  const t0 = performance.now();
  const paras = extractParagraphs(JSON.parse(toJson(bytes) as string));
  const ms = performance.now() - t0;
  const text = paras.join("\n");
  Deno.writeTextFileSync(`${outDir}/extracted.txt`, text);
  console.error(
    `${"extracted".padEnd(12)} paras=${paras.length} chars=${text.length} ms=${ms.toFixed(1)} → ${outDir}/extracted.txt`,
  );
}

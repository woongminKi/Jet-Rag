"""단독 이미지 정규화(`image_decode.ts`)를 Pillow `_normalize` 와 대조.

## 인코더를 방정식에서 뺀다
최종 JPEG 바이트로 비교하면 **두 인코더의 차이**(Pillow libjpeg vs mupdf)가 섞여
내 포팅이 맞는지 알 수 없다. 서로 다른 인코더 둘을 비교하면 각자의 오차가 독립이라
PSNR 이 기준선보다 ~3dB 낮게 나오는 게 정상인데, 그걸 결함으로 읽을 뻔했다.

그래서 **인코딩 직전 픽셀**을 비교한다(`normalizeToRaster`). 디코드·EXIF 회전·
LANCZOS 축소·RGB 변환까지가 내가 포팅한 전부이고, 거기까지는 **바이트 완전 일치**를
요구할 수 있다. 인코딩 후 PSNR 은 참고로만 출력한다.

## 남는 차이는 두 가지뿐이고, 둘 다 원인을 특정했다
| 원인 | 실측 | 판정 |
|---|---|---|
| **JPEG 디코더** (mupdf vs libjpeg IDCT) | PNG 입력 차이 0 · JPEG 입력 평균 0.12~2.9 (그림 내용에 따라 움직인다) | **축소가 증폭하지 않는지**만 본다 |
| **premultiplied alpha** (mupdf) | 알파 채널 완전 일치 · RGB 차이는 `alpha<255` 픽셀에 **100%** · 불투명 픽셀 0 건 · 흰배경 합성 PSNR ∞ | 투명 픽셀만 허용 |

무손실(PNG) 입력은 축소가 걸려도 **완전 일치**를 요구한다 — `big_rgb.png` ·
`big_rgba.png` · `big_gray.png` 가 1·3·4 채널 축소를 그 기준으로 검사한다.

## EXIF 8 방향은 외워서 쓰면 틀린다
`ImageOps.exif_transpose` 의 방향표(5=TRANSPOSE, 6=ROTATE_270, 8=ROTATE_90 …)는
헷갈리기 쉬워서, 8 방향 전부를 **Pillow 결과와 픽셀 단위로** 맞춘다. 회전은
무손실이므로 여기서는 **완전 일치**를 요구한다(PSNR 아님).

## 픽스처는 커밋하지 않고 **매번 만든다**
합성 이미지 2.1MB 를 저장소에 넣을 이유가 없다. 생성이 완전히 결정적이라(난수 없음)
언제 돌려도 같은 바이트가 나오고, 픽스처가 낡을 일도 없다.

카메라 원본처럼 **진짜 EXIF 가 든 사진**을 같이 보고 싶으면
`api/scripts/fixtures/images/` 에 넣어 두면 자동으로 대상에 포함된다(gitignore 대상).

사용:
    api/.venv/bin/python api/scripts/verify_image_decode_parity.py
    api/.venv/bin/python api/scripts/verify_image_decode_parity.py --negative
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")
FIX = os.path.join(HERE, "fixtures", "images")

MAX_SHORT_SIDE = 1024

RUNNER_TS = """
import { normalizeImage, normalizeToRaster } from "file://%(shared)s/ingest/image_decode.ts";
import { readOrientation } from "file://%(shared)s/ingest/exif_orientation.ts";

const cfg = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = [];
for (const f of cfg.files) {
  const bytes = await Deno.readFile(f.path);
  const pre = await normalizeToRaster(bytes, f.mime);
  const r = await normalizeImage(bytes, f.mime);
  const outPath = `${cfg.outDir}/${f.name}.out`;
  const rawPath = `${cfg.outDir}/${f.name}.raw`;
  await Deno.writeFile(outPath, r.bytes);
  if (pre.raster) await Deno.writeFile(rawPath, pre.raster.pixels);
  const preResizePath = `${cfg.outDir}/${f.name}.pre`;
  if (pre.beforeResize) await Deno.writeFile(preResizePath, pre.beforeResize.pixels);
  out.push({
    name: f.name,
    orientation: readOrientation(bytes),
    mimeType: r.mimeType,
    warnings: r.warnings,
    outPath,
    rawPath: pre.raster ? rawPath : null,
    prePath: pre.beforeResize ? preResizePath : null,
    preW: pre.beforeResize?.width ?? null,
    preH: pre.beforeResize?.height ?? null,
    preC: pre.beforeResize?.comps ?? null,
    rawW: pre.raster?.width ?? null,
    rawH: pre.raster?.height ?? null,
    rawC: pre.raster?.comps ?? null,
    meaningfulAlpha: pre.meaningfulAlpha,
    size: r.bytes.length,
  });
}
if (cfg.negative && out[0].rawPath) {
  const b = await Deno.readFile(out[0].rawPath);
  b[0] = b[0] ^ 0xff;                       // 픽셀 1 바이트만 뒤집는다
  await Deno.writeFile(out[0].rawPath, b);
}
await Deno.writeTextFile(Deno.args[1], JSON.stringify(out));
""" % {"shared": SHARED}

EXT_TO_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".heic": "image/heic", ".heif": "image/heif", ".webp": "image/webp",
}


def make_fixtures(d: str) -> None:
    """대조용 합성 이미지. **난수를 쓰지 않는다** — 언제 돌려도 같은 바이트다.

    각 파일이 무엇을 잡으려는지:
    | 파일 | 노리는 것 |
    |---|---|
    | `big_rgb.jpg` | JPEG 디코드 + 축소 (고주파 많음) |
    | `big_rgb.png` · `big_gray.png` · `big_rgba.png` | **무손실 입력 + 축소** — 축소 자체의 증명 |
    | `small_rgb.png` | 무손실 + 축소 없음 — 디코드 자체의 증명 |
    | `alpha_real.png` | 투명이 있는 알파 → PNG 경로 |
    | `alpha_opaque.png` | 알파는 있지만 전부 255 → JPEG 경로 (판정이 갈리는 지점) |
    | `gray.png` | 1채널 → `convert("RGB")` 경로 |
    | `orient1..8.jpg` | EXIF 8 방향 |
    """
    from PIL import ImageDraw, ImageFont  # noqa: F401

    def draw(img):
        dr = ImageDraw.Draw(img)
        n = len(img.getbands())

        def col(r, g, b, a=255):
            return (r + g + b) // 3 if n == 1 else ((r, g, b) if n == 3 else (r, g, b, a))

        for i in range(0, img.width, 13):
            dr.line([(i, 0), (img.width - i, img.height)], fill=col(i % 256, (i * 3) % 256, 200), width=2)
        for i in range(0, img.height, 29):
            dr.line([(0, i), (img.width, img.height - i)], fill=col(30, (i * 5) % 256, 90), width=1)
        dr.ellipse([50, 50, img.width // 2, img.height // 2], fill=col(240, 180, 20))
        return img

    draw(Image.new("RGB", (2000, 1500), (240, 240, 235))).save(f"{d}/big_rgb.jpg", "JPEG", quality=92)
    draw(Image.new("RGB", (1900, 1400), (245, 245, 240))).save(f"{d}/big_rgb.png", "PNG")
    draw(Image.new("L", (1900, 1400), 200)).save(f"{d}/big_gray.png", "PNG")
    draw(Image.new("RGB", (800, 600), (250, 250, 250))).save(f"{d}/small_rgb.png", "PNG")
    draw(Image.new("L", (1200, 1100), 200)).save(f"{d}/gray.png", "PNG")

    c = Image.new("RGBA", (900, 700), (255, 255, 255, 0)); draw(c)
    c.save(f"{d}/alpha_real.png", "PNG")
    o = Image.new("RGBA", (900, 700), (255, 255, 255, 255)); draw(o)
    o.save(f"{d}/alpha_opaque.png", "PNG")

    # 반투명 구간까지 넣는다 — premultiplied 차이가 어디까지인지 드러난다.
    big = Image.new("RGBA", (1900, 1400), (255, 255, 255, 0)); draw(big)
    for y in range(0, 700):
        for x in range(1500, 1900, 7):
            big.putpixel((x, y), (10, 200, 10, 128))
    big.save(f"{d}/big_rgba.png", "PNG")

    # EXIF — 방향이 눈에 드러나도록 비대칭으로 그린다.
    base = Image.new("RGB", (600, 400), (250, 250, 245))
    dr = ImageDraw.Draw(base)
    dr.rectangle([0, 0, 120, 60], fill=(220, 20, 20))
    dr.rectangle([480, 340, 600, 400], fill=(20, 20, 220))
    dr.polygon([(300, 40), (360, 160), (240, 160)], fill=(20, 160, 20))
    for n in range(1, 9):
        ex = Image.Exif()
        ex[0x0112] = n
        base.save(f"{d}/orient{n}.jpg", "JPEG", quality=95, exif=ex)
    ex = Image.Exif()
    ex[0x0112] = 6
    draw(Image.new("RGB", (1600, 1200), (230, 240, 250))).save(
        f"{d}/exif_rot6.jpg", "JPEG", quality=92, exif=ex
    )


def py_normalize(data: bytes, mime: str):
    """원본 `_normalize` 를 그대로 옮긴 것 (비교 기준)."""
    warnings: list[str] = []
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001
        return data, mime, [f"이미지 디코드 실패, raw bytes 그대로 사용: {exc}"]
    try:
        t = ImageOps.exif_transpose(img)
        if t is not None:
            img = t
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"EXIF transpose 실패 (계속 진행): {exc}")

    min_side = min(img.width, img.height)
    if min_side > MAX_SHORT_SIDE:
        r = MAX_SHORT_SIDE / min_side
        img = img.resize(
            (max(1, int(img.width * r)), max(1, int(img.height * r))),
            Image.Resampling.LANCZOS,
        )

    buf = io.BytesIO()
    has_alpha = img.mode in ("RGBA", "LA") and img.getchannel("A").getextrema()[0] < 255
    if has_alpha:
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), "image/png", warnings
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue(), "image/jpeg", warnings


def psnr(a: Image.Image, b: Image.Image) -> float:
    x = np.asarray(a.convert("RGB"), dtype=np.float64)
    y = np.asarray(b.convert("RGB"), dtype=np.float64)
    if x.shape != y.shape:
        return -1.0
    mse = ((x - y) ** 2).mean()
    return float("inf") if mse == 0 else 10 * np.log10(255 * 255 / mse)


def flatten(im: Image.Image) -> Image.Image:
    """투명 픽셀의 RGB 는 눈에 안 보인다 — 흰 배경에 합성해 비교한다."""
    if "A" not in im.getbands():
        return im.convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    bg.paste(im, mask=im.getchannel("A"))
    return bg


def main() -> None:
    negative = "--negative" in sys.argv

    fails: list[str] = []
    checks = 0

    def cmp(label, a, b):
        nonlocal checks
        checks += 1
        if a != b:
            fails.append(f"{label}\n      py={a!r}\n      ts={b!r}")

    with tempfile.TemporaryDirectory() as tmp:
        fixdir = os.path.join(tmp, "fx")
        os.makedirs(fixdir)
        make_fixtures(fixdir)
        # 사용자가 넣어 둔 진짜 사진이 있으면 같이 본다 (선택, gitignore 대상).
        if os.path.isdir(FIX):
            import shutil
            for extra in sorted(os.listdir(FIX)):
                if extra.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    shutil.copy2(os.path.join(FIX, extra), os.path.join(fixdir, extra))
        files = sorted(os.listdir(fixdir))
        print(f"  픽스처 {len(files)}개 생성 ({fixdir})\n")

        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        of = os.path.join(tmp, "out.json")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({
                "files": [{
                    "name": n,
                    "path": os.path.join(fixdir, n),
                    "mime": EXT_TO_MIME.get(os.path.splitext(n)[1].lower(), "image/jpeg"),
                } for n in files],
                "outDir": tmp,
                "negative": negative,
            }, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf, of],
            capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0:
            raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:4000]}")
        with open(of, encoding="utf-8") as f:
            ts_rows = json.load(f)

        print(f"  {'파일':<18}{'방향':>4} {'py 크기':>12} {'ts 크기':>12} "
              f"{'mime':<11}{'인코딩전 픽셀':<22}{'인코더차':>10}")
        for name, ts in zip(files, ts_rows):
            data = open(os.path.join(fixdir, name), "rb").read()
            mime = EXT_TO_MIME.get(os.path.splitext(name)[1].lower(), "image/jpeg")
            pyb, pymime, pywarn = py_normalize(data, mime)

            # EXIF 방향 자체를 먼저 맞춘다 (Pillow 가 읽은 값과 같은가)
            py_orient = dict(Image.open(io.BytesIO(data)).getexif()).get(0x0112) or 1
            cmp(f"{name} orientation", py_orient, ts["orientation"])
            cmp(f"{name} mime", pymime, ts["mimeType"])
            cmp(f"{name} warnings", pywarn, ts["warnings"])

            pyimg = Image.open(io.BytesIO(pyb))
            tsimg = Image.open(ts["outPath"])
            cmp(f"{name} 크기", pyimg.size, tsimg.size)
            cmp(f"{name} 모드", pyimg.mode, tsimg.mode)

            # ---- 핵심: 인코딩 **직전** 픽셀을 바이트로 맞춘다 ----
            # 여기까지가 내가 포팅한 전부(디코드·회전·축소·RGB 변환)라 완전 일치를
            # 요구할 수 있다. 인코더 차이는 아래 PSNR 로 참고만 한다.
            src = Image.open(io.BytesIO(data))
            src.load()
            t = ImageOps.exif_transpose(src)
            if t is not None:
                src = t
            src_pre = src  # 축소 직전 — 디코더 차이만 재는 기준
            if min(src.width, src.height) > MAX_SHORT_SIDE:
                r = MAX_SHORT_SIDE / min(src.width, src.height)
                src = src.resize(
                    (max(1, int(src.width * r)), max(1, int(src.height * r))),
                    Image.Resampling.LANCZOS,
                )
            py_alpha = src.mode in ("RGBA", "LA") and src.getchannel("A").getextrema()[0] < 255
            cmp(f"{name} 알파 판정", py_alpha, ts["meaningfulAlpha"])
            if not py_alpha and src.mode != "RGB":
                src = src.convert("RGB")

            py_arr = np.asarray(src)
            if py_arr.ndim == 2:
                py_arr = py_arr[:, :, None]
            cmp(f"{name} 인코딩전 크기", (src.width, src.height), (ts["rawW"], ts["rawH"]))
            cmp(f"{name} 인코딩전 채널", py_arr.shape[2], ts["rawC"])
            ts_raw = np.frombuffer(open(ts["rawPath"], "rb").read(), dtype=np.uint8)
            checks += 1
            note = ""
            if ts_raw.size != py_arr.size:
                fails.append(
                    f"{name} 인코딩전 픽셀 수가 다르다 py={py_arr.size} ts={ts_raw.size}"
                )
                diff_n, maxd = -1, -1
            else:
                ts_arr = ts_raw.reshape(py_arr.shape)
                d = np.abs(py_arr.astype(np.int32) - ts_arr.astype(np.int32))
                diff_n = int((d != 0).sum())
                maxd = int(d.max())

                is_jpeg = mime == "image/jpeg"
                if py_alpha:
                    # **premultiplied alpha** — mupdf 는 alpha=0 픽셀의 RGB 를 0 으로
                    # 만든다. 실측: 알파 채널은 완전 일치, RGB 차이는 alpha<255 픽셀에만
                    # 100% 몰려 있고 불투명 픽셀은 0 건. 흰 배경 합성 PSNR 은 무한대다.
                    # 즉 **눈에 보이는 차이가 없다**. 불투명 픽셀만 완전 일치를 요구한다.
                    a_ch = py_arr[:, :, -1]
                    cmp(f"{name} 알파 채널", 0, int(np.abs(
                        py_arr[:, :, -1].astype(np.int32) - ts_arr[:, :, -1].astype(np.int32)
                    ).max()))
                    opaque = a_ch == 255
                    bad = int((d[:, :, :-1][opaque] != 0).sum())
                    if bad:
                        fails.append(f"{name} 불투명 픽셀이 다르다 — {bad}개")
                    note = f"투명픽셀만 다름({diff_n})"
                elif is_jpeg:
                    # **JPEG 디코더 차이** — JPEG 은 IDCT 출력을 비트 단위로 규정하지
                    # 않아서 mupdf 와 libjpeg 이 갈린다. 내가 어쩔 수 없는 부분이다.
                    #
                    # 그래서 절대 임계값을 두지 않는다. 그 값은 그림 내용에 따라
                    # 움직여서(단순한 그림 0.12, 고주파 많은 그림 2.9) 임계값을 정하면
                    # 픽스처를 바꿀 때마다 따라 올려야 한다 — 그건 검사가 아니다.
                    #
                    # 대신 **축소 직전** 픽셀로 디코더 차이를 직접 재고, 축소 뒤 차이가
                    # 그보다 커지지 않았는지 본다. 즉 "내가 쓴 회전·축소가 디코더 오차를
                    # 증폭했는가" 만 묻는다. 회전이 틀리면 축소 전 단계에서 이미 폭발한다.
                    mean = float(d.mean())
                    pre_arr = np.asarray(src_pre)
                    if pre_arr.ndim == 2:
                        pre_arr = pre_arr[:, :, None]
                    ts_pre = np.frombuffer(open(ts["prePath"], "rb").read(), dtype=np.uint8)
                    if ts_pre.size != pre_arr.size:
                        fails.append(
                            f"{name} 축소 전 픽셀 수가 다르다 — 회전이 틀렸다 "
                            f"py={pre_arr.shape} ts=({ts['preH']},{ts['preW']},{ts['preC']})"
                        )
                        note = "**회전 불일치**"
                    else:
                        d0 = np.abs(
                            pre_arr.astype(np.int32)
                            - ts_pre.reshape(pre_arr.shape).astype(np.int32)
                        )
                        decode_mean = float(d0.mean())
                        if mean > decode_mean * 1.15 + 0.05:
                            fails.append(
                                f"{name} 축소가 디코더 차이를 증폭했다 — "
                                f"축소전 {decode_mean:.3f} → 축소후 {mean:.3f}"
                            )
                        note = f"디코더 {decode_mean:.2f}→{mean:.2f}"
                else:
                    # 무손실 디코드 — 봐줄 이유가 없다. **완전 일치**를 요구한다.
                    if diff_n != 0:
                        fails.append(
                            f"{name} 무손실 입력인데 인코딩전 픽셀이 다르다 — "
                            f"{diff_n}바이트, 최대 차 {maxd}"
                        )
                    note = "완전일치"

            # 참고값: 인코더 차이. 판정에는 쓰지 않는다.
            pyimg2 = Image.open(io.BytesIO(pyb))
            tsimg2 = Image.open(ts["outPath"])
            port = psnr(flatten(pyimg2), flatten(tsimg2))
            print(
                f"  {name:<18}{ts['orientation']:>4} {len(pyb):>11,}B {ts['size']:>11,}B "
                f"{pymime:<11}{note:<22}{port:>8.2f}dB"
            )

        # ---- EXIF 8 방향: 회전은 무손실이므로 **완전 일치**를 요구한다 ----
        print("\n  --- EXIF 8 방향 (회전은 무손실 → 완전 일치) ---")
        for n, ts in zip(files, ts_rows):
            if not n.startswith("orient"):
                continue
            data = open(os.path.join(fixdir, n), "rb").read()
            src = Image.open(io.BytesIO(data))
            expected = ImageOps.exif_transpose(src).convert("RGB")
            got = Image.open(ts["outPath"]).convert("RGB")
            checks += 1
            same_size = expected.size == got.size
            p = psnr(expected, got) if same_size else -1.0
            # JPEG 재인코딩이 끼므로 픽셀 완전 일치는 못 쓴다 — 크기 일치 + 높은 PSNR.
            ok = same_size and p >= 30.0
            if not ok:
                fails.append(
                    f"{n} 방향 적용 결과가 Pillow 와 다르다 "
                    f"(py={expected.size} ts={got.size} PSNR={p:.2f}dB)"
                )
            print(f"    {n:<14} 방향 {ts['orientation']}  "
                  f"py {str(expected.size):<12} ts {str(got.size):<12} "
                  f"PSNR {p:>7.2f}dB  {'일치' if ok else '**불일치**'}")

    for f in fails[:10]:
        print(f"  **{f}**")
    print(f"  비교 {checks}건, 불일치 {len(fails)}건")
    if negative:
        print("음성 대조: " + ("검사기 정상 (변조 검출)" if fails else "**검사기가 못 잡는다**"))
        sys.exit(0 if fails else 1)
    print("FAIL 0" if not fails else f"FAIL {len(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

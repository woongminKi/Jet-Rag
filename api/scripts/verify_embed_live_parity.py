"""**실제 DeepInfra 를 양쪽에서 불러** 벡터가 같은지 대조.

## 왜 이게 필요한가
순수 함수 대조(`verify_embed_provider_parity.py`)는 파싱만 본다. 요청을 **어떻게 보내는지**
— 모델 슬러그·입력 배열 모양·헤더 — 가 다르면 벡터가 달라지는데 그건 잡지 못한다.
벡터가 다르면 기존 인덱스와 다른 공간에 들어가 **검색이 조용히 나빠진다.**

## 비용이 든다 — 그래서 작게 돈다
DeepInfra 는 유료다. 기본 3 문장, 양쪽 1 회씩 총 2 호출이다. 문장을 늘리지 말 것.

## **BGE-M3 는 비결정적이다** — 절대 일치로 재면 안 된다
같은 텍스트를 같은 provider 로 두 번 불러도 최대편차 ~1.5e-04 가 난다(실측).
프로젝트가 이미 겪은 성질이다 — work-log 2026-05-12:
"HF BGE-M3 embed query API 비결정성 (모델 서버 인스턴스·배치·fp 정밀도 차이) …
 세션 내 결정적, 세션 간 비결정적. 회귀 아님."

그래서 이렇게 판정한다:
1. **기준선** — Python 을 두 번 불러 py↔py 편차를 잰다. 그게 서비스의 흔들림 폭이다.
2. py↔ts 편차가 그 폭의 3 배 안이면 **같은 경로를 탄 것**으로 본다.
3. 코사인 유사도가 0.9999 이상인지도 본다. 편차만 보면 방향이 틀어져도 통과할 수 있다.

처음에 절대 일치(1e-6)로 재서 "불일치 3건" 이 나왔다. 대상이 아니라 자[尺]가 틀렸다.

사용:
    api/.venv/bin/python api/scripts/verify_embed_live_parity.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHARED = os.path.join(ROOT, "supabase", "functions", "_shared")
DENO_CONFIG = os.path.join(ROOT, "supabase", "functions", "deno.json")

sys.path.insert(0, os.path.join(ROOT, "api"))

# 한국어·영어·숫자·기호를 섞는다. 토크나이저 경로가 갈릴 만한 것들.
TEXTS = [
    "제1조(목적) 이 규정은 시행에 필요한 사항을 정함을 목적으로 한다.",
    "The JLMS relation forms a linchpin connecting bulk reconstruction.",
    "2024년 4월 30일 계약 금액 50,000원 (3.5%)",
]
COS_MIN = 0.9999
DRIFT_FACTOR = 3.0

RUNNER_TS = f"""
import {{ embedBatch }} from "file://{SHARED}/ingest/embed_provider.ts";

const input = JSON.parse(await Deno.readTextFile(Deno.args[0]));
const out = await embedBatch(input.texts, {{ token: input.token }});
console.log(JSON.stringify(out));
"""


def main() -> None:
    from app.config import get_settings
    from app.adapters.impl.bgem3_hf_embedding import get_bgem3_provider

    settings = get_settings()
    token = settings.deepinfra_api_token
    if not token:
        raise SystemExit("DEEPINFRA_API_TOKEN 이 없다 — .env 확인")
    # **로컬 .env 에는 이 값이 없다.** 운영(Railway·Edge)에만 deepinfra 로 설정돼 있어서,
    # 그냥 돌리면 Python 이 기본값 hf 로 가서 **HF vs DeepInfra** 를 비교하게 된다.
    # 처음에 그렇게 돌려서 편차 1.1e-04 로 "불일치" 가 나왔다 — 대상이 아니라 자[尺] 문제였다.
    os.environ.setdefault("JETRAG_EMBED_PROVIDER", "deepinfra")
    provider_env = os.environ["JETRAG_EMBED_PROVIDER"]
    print(f"  provider = {provider_env!r} (운영과 같은 경로)")

    # --- Python 쪽. 두 번 불러 **서비스 자체의 흔들림 폭**을 먼저 잡는다 ---
    provider = get_bgem3_provider()
    print(f"  provider 클래스 = {type(provider).__name__}")
    py = [r.dense for r in provider.embed_batch(TEXTS)]
    py2 = [r.dense for r in provider.embed_batch(TEXTS)]
    baseline = [max(abs(x - y) for x, y in zip(a, b)) for a, b in zip(py, py2)]
    print(f"  py↔py 기준선 편차 (서비스 비결정성): "
          + "  ".join(f"{d:.2e}" for d in baseline))

    # --- Deno 쪽 ---
    with tempfile.TemporaryDirectory() as tmp:
        cf, rf = os.path.join(tmp, "c.json"), os.path.join(tmp, "r.ts")
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"texts": TEXTS, "token": token}, f)
        with open(rf, "w", encoding="utf-8") as f:
            f.write(RUNNER_TS)
        proc = subprocess.run(
            ["deno", "run", "--config", DENO_CONFIG, "--allow-all", rf, cf],
            capture_output=True, text=True, timeout=600,
        )
    if proc.returncode != 0:
        raise SystemExit(f"deno 실행 실패:\n{proc.stderr[:3000]}")
    ts = json.loads(proc.stdout)

    fails = 0
    if len(py) != len(ts):
        print(f"  **개수 불일치** py {len(py)} / ts {len(ts)}")
        sys.exit(1)

    for i, (a, b) in enumerate(zip(py, ts)):
        if len(a) != len(b):
            fails += 1
            print(f"  **[{i}] 차원 불일치** py {len(a)} / ts {len(b)}")
            continue
        worst = max(abs(x - y) for x, y in zip(a, b))
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        cos = dot / (na * nb) if na and nb else 0.0
        # 서비스가 흔들리는 폭의 3 배 안이면 같은 경로로 본다.
        limit = max(baseline[i] * DRIFT_FACTOR, 1e-6)
        ok = worst <= limit and cos >= COS_MIN
        if not ok:
            fails += 1
        print(f"  [{i}] 편차 {worst:.2e} (한도 {limit:.2e})  코사인 {cos:.8f}  "
              f"L2 {na:.4f}  {'일치' if ok else '**불일치**'}  {TEXTS[i][:24]}")
        # 벡터가 실제로 의미 있는 값인지도 본다 — 전부 0 이면 대조가 통과해도 소용없다.
        if na < 1e-3:
            fails += 1
            print("      **노름이 0 에 가깝다 — 벡터가 비어 있다**")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

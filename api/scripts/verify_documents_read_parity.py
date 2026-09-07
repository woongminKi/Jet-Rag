"""`/documents` 읽기 3종을 **Railway 응답과 직접 대조**.

## 왜 HTTP 대조인가
이 라우트들은 DB 조회와 조립이 전부다. 함수 단위로 옮겨 봐야 "내가 고른 입력에서 같다"
는 증거뿐이고, 진짜 위험은 **응답 스키마의 미세한 차이**(키 누락·타입·정렬)다.
그건 양쪽을 같은 요청으로 두들겨 JSON 을 통째로 비교해야 잡힌다.

`/search`·`/me/*` 이관 때 쓴 방식과 같다.

## 인증
토큰을 안 보낸다. 그러면 양쪽 다 owner 컨텍스트(`isAuthenticated=false`)로 떨어져
같은 문서를 본다 — 수익화 W1 "데모 병행" 설계다. 쓰기가 아니라 안전하다.

## 비교에서 빼는 것
없다. **전체 JSON 을 비교한다.** 시각 필드(`created_at` 등)는 조회 시점과 무관하게
DB 값이라 흔들리지 않는다. 흔들리는 게 나오면 그 자체가 발견이다.

사용:
    api/.venv/bin/python api/scripts/verify_documents_read_parity.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

RAILWAY = "https://jet-rag-production.up.railway.app"
EDGE = os.environ.get(
    "EDGE_BASE",
    "https://mpmtydudhojpukuuadrd.supabase.co/functions/v1/api-documents",
)


def fetch(base: str, path: str, *, edge: bool) -> tuple[int, object]:
    """Edge 는 `X-Forwarded-Path` 로 원본 경로를 알려 준다(프록시가 하는 일)."""
    url = base + (path if not edge else path)
    req = urllib.request.Request(url, method="GET")
    if edge:
        # 프록시가 붙이는 헤더. 없으면 함수가 `/api-documents/...` 를 보게 된다.
        req.add_header("X-Forwarded-Path", path.split("?")[0])
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, body


def main() -> None:
    # 실재하는 doc_id 하나를 목록에서 얻는다 — 상세·상태 케이스에 쓴다.
    st, body = fetch(RAILWAY, "/documents?limit=1", edge=False)
    real_id = None
    if st == 200 and isinstance(body, dict) and body.get("items"):
        real_id = body["items"][0]["id"]
    print(f"  대상 doc_id: {real_id}")

    cases = [
        "/documents",
        "/documents?limit=1",
        "/documents?limit=100",
        "/documents?limit=3&offset=1",
        "/documents?offset=9999",
        "/documents?include_failed=true",
        "/documents?include_failed=1",
        "/documents?include_failed=maybe",
        "/documents?include_failed=",
        "/documents?limit=3.5",
        "/documents?offset=abc",
        "/documents?limit=0&offset=-1",
        # 경계·무효 — FastAPI 의 422 를 그대로 내는지
        "/documents?limit=0",
        "/documents?limit=101",
        "/documents?limit=abc",
        "/documents?offset=-1",
        # 없는 문서 — 404 여야 한다(존재 위장)
        "/documents/00000000-0000-0000-0000-000000000000",
        "/documents/00000000-0000-0000-0000-000000000000/status",
        "/documents/not-a-uuid",
    ]
    if real_id:
        cases += [
            f"/documents/{real_id}",
            f"/documents/{real_id}/status",
            f"/documents/{real_id}/status?include_logs=true",
            f"/documents/{real_id}/status?include_logs=false",
            f"/documents/{real_id}/status?include_logs=maybe",
            f"/documents/{real_id}/status?include_logs=1",
        ]

    fails = 0
    codes: dict[int, int] = {}
    for path in cases:
        rs, rb = fetch(RAILWAY, path, edge=False)
        es, eb = fetch(EDGE, path, edge=True)
        codes[rs] = codes.get(rs, 0) + 1
        same_code = rs == es
        same_body = rb == eb
        mark = "OK" if same_code and same_body else "**불일치**"
        print(f"  {path[:52]:<52} {rs}/{es}  {mark}")
        if not (same_code and same_body):
            fails += 1
            if not same_code:
                print(f"      코드 railway={rs} edge={es}")
            if not same_body:
                rjs = json.dumps(rb, ensure_ascii=False, sort_keys=True)
                ejs = json.dumps(eb, ensure_ascii=False, sort_keys=True)
                # 어디서 갈렸는지 첫 지점만
                for i, (a, b) in enumerate(zip(rjs, ejs)):
                    if a != b:
                        print(f"      railway …{rjs[max(0, i - 60):i + 60]}")
                        print(f"      edge    …{ejs[max(0, i - 60):i + 60]}")
                        break
                else:
                    print(f"      길이만 다름 railway={len(rjs)} edge={len(ejs)}")

    print()
    print(f"  응답 코드 분포(railway): {dict(sorted(codes.items()))}")
    # 케이스 무효 검사 — 200 만 나오면 오류 경로를 안 태운 것이다.
    if len(codes) < 2:
        fails += 1
        print("    **케이스 무효** — 한 가지 상태 코드만 나왔다")

    print()
    print("FAIL 0" if fails == 0 else f"FAIL {fails}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()

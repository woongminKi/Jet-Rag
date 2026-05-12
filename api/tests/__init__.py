"""Jet-Rag api 단위 테스트 패키지.

W15 Day 3 — 단위 테스트 진입 시 metrics DB write-through 비활성.
운영 코드 (uvicorn) 와 분리된 환경 — connection timeout 회피.

W17 Day 4 — async path 강제 비활성 (PERSIST_ASYNC='0').
ThreadPoolExecutor background thread 가 assertLogs / mock 검증 race 유발 회피.
"""

import os

# 단위 테스트는 metrics persist 비활성 — Supabase 연결 시도 timeout 회피.
# 운영 (uvicorn) 환경은 default "1" 유지.
# 강제 "0" 설정 — 셸 환경에 다른 값 있어도 override (단위 테스트 격리).
os.environ["JET_RAG_METRICS_PERSIST_ENABLED"] = "0"

# W17 Day 4 한계 #88 — async fire-and-forget 도 단위 테스트에서는 sync 강제.
# ENABLED='0' 일 때는 어차피 skip 이지만, ENABLED='1' 로 override 하는 테스트
# (FirstWarnPatternTest 등) 에서 background thread race 회피 위해.
os.environ["JET_RAG_METRICS_PERSIST_ASYNC"] = "0"

# S4-B 후속 — `embed_query` 영구 캐시(embed_query_cache, 마이그 016) 도 단위 테스트에서는
# 기본 비활성. 로컬 .env 에 실 Supabase 자격이 있으면 mock 안 한 테스트가 실 DB 에
# 가비지 벡터를 write 할 수 있어서. 영구 캐시 자체를 검증하는 test_embed_query_cache.py 는
# 자기 안에서 "1" 로 override + supabase mock 으로 격리.
os.environ["JETRAG_EMBED_QUERY_CACHE"] = "0"

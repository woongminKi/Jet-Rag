"""인제스트 파이프라인의 스테이지별 구현.

기획서 §10.2 [4]~[10] 단계를 각 모듈로 분리.

- extract: 포맷별 추출 (Day 4 — PDF 우선)
- chunk: §10.5 청킹 (Day 4)
- tag_summarize: §10.6 태그·요약·diff (Day 4.5)
- load: chunks 적재 (Day 4.5 — 임베딩 NULL 상태로 먼저 쌓기)
- embed: BGE-M3 임베딩 (Day 5)
- dedup: Tier 2/3 감지 (Day 5 이후)
"""

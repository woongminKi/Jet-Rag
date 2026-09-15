#!/bin/bash
# hnsw_recall.sh — HNSW 검색이 정확 top-50 대비 몇 개를 찾는지(재현율)와 DB 내부 지연을 잰다.
#
# 2026-09-15~16 에 스크래치패드에 run.sh/ids.sh/truth.sh 로 나뉘어 있던 측정기를 하나로 합쳤다.
# 034(ef_search)·036(halfvec) 같은 인덱스·설정 변경의 전후 비교에 쓴다.
#
# 사용 (프로젝트 루트에서)
#   api/scripts/hnsw_recall.sh [질의수=3] [ef=40] [iterative_scan=off]
#   - 질의 벡터는 DB 의 미필터 청크에서 chunk_idx 간격으로 뽑는다(재현 가능하게 같은 문서·같은 idx).
#   - 정확 top-50 은 enable_indexscan=off 로 브루트포스.
#   - 출력: 질의별  n(반환 행수) / recall(정확 top-50 과 겹침) / run1_ms / run2_ms (셸에서 잰 관리 API 왕복 포함 시간)
#   - ef·iterative_scan 은 SET LOCAL 로 트랜잭션 안에서만 바꾼다(운영 설정은 안 건드린다).
#
# 해석
#   n < 50 이면 ef_search 예산이 필터에 먹힌 것(034 참조). recall 이 낮은데 n=50 이면 인덱스 품질(죽은 엔트리, m/ef_construction).
#   run1/run2 는 관리 API 왕복(~300ms)이 포함된 값이고, 직전 브루트포스가 테이블을 읽어 캐시는 이미 웜이다.
#   "콜드" 를 재려면 이 스크립트가 아니라 재시작 직후 API 를 직접 쳐야 한다.

set -u
N="${1:-3}"; EF="${2:-40}"; IT="${3:-off}"
[ -d supabase/.temp ] || { echo "프로젝트 루트에서 실행하세요" >&2; exit 2; }
OWNER="$(supabase db query --linked "select user_id from documents where deleted_at is null order by created_at limit 1" 2>/dev/null | jq -r '.rows[0].user_id')"
[ -n "$OWNER" ] && [ "$OWNER" != "null" ] || { echo "owner 를 못 찾았다" >&2; exit 2; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# 관리 API 는 SQL 오류여도 exit 0 에 {"_tag":"Error"} 를 stdout 으로 준다 — 그걸 그대로 jq 에 넣으면 truth·got 이
# 둘 다 "null" 한 줄이 되어 recall=1/50 같은 **가짜 값**이 나온다. rows 가 없으면 여기서 죽는다.
q() {
  local o
  o="$(supabase db query --linked "$1" 2>/dev/null </dev/null)" || { echo "supabase 호출 실패" >&2; exit 3; }
  jq -e 'has("rows")' >/dev/null <<<"$o" || { echo "SQL 오류: $(printf '%s' "$o" | head -c 300)" >&2; exit 3; }
  printf '%s' "$o"
}
now_ms() { perl -MTime::HiRes=time -e 'printf "%.0f\n", time*1000'; }

# 질의 벡터 N 개 — 벡터가 있는 미필터 청크를 chunk_idx 순으로 N 등분한 지점에서.
q "select dense_vec::text as v from (select dense_vec, row_number() over (order by doc_id, chunk_idx) rn, count(*) over () cnt
      from chunks where dense_vec is not null and (flags->>'filtered_reason') is null) t
   where rn in (select greatest(1, (cnt * g / ($N + 1))::int) from generate_series(1, $N) g) order by rn" \
  | jq -r '.rows[].v' > "$TMP/vecs.txt"
cnt="$(grep -c . "$TMP/vecs.txt")"
[ "$cnt" -gt 0 ] || { echo "질의 벡터를 못 뽑았다" >&2; exit 2; }
echo "ef_search=$EF iterative_scan=$IT owner=$OWNER 질의=$cnt"

i=0
while IFS= read -r V; do
  i=$((i+1))
  WHERE="c.dense_vec is not null and d.deleted_at is null and d.user_id='$OWNER'::uuid and (c.flags->>'filtered_reason') is null"
  q "begin; set local enable_indexscan=off;
     select string_agg(id::text, ',') as ids from (select c.id from chunks c join documents d on d.id=c.doc_id
       where $WHERE order by c.dense_vec <=> '$V'::vector limit 50) t; commit;" \
    | jq -r '.rows[0].ids' | tr ',' '\n' | sort > "$TMP/truth"
  SQL="begin; set local hnsw.ef_search=$EF; set local hnsw.iterative_scan='$IT';
       select string_agg(chunk_id::text, ',') as ids, count(*) as n
         from search_dense_only('$V'::vector, 60, 50, '$OWNER'::uuid); commit;"
  t0=$(now_ms); r1="$(q "$SQL" | jq -c '.rows[0]')"; t1=$(now_ms); r2="$(q "$SQL" | jq -c '.rows[0]')"; t2=$(now_ms)
  printf '%s' "$r1" | jq -r '.ids' | tr ',' '\n' | sort > "$TMP/got"
  echo "q$i n=$(printf '%s' "$r1" | jq -r '.n') recall=$(comm -12 "$TMP/got" "$TMP/truth" | wc -l | tr -d ' ')/50 run1_ms=$((t1-t0)) run2_ms=$((t2-t1)) (관리 API 왕복 포함, 직전 브루트포스로 캐시는 이미 웜)"
done < "$TMP/vecs.txt"

#!/bin/bash
# ingest_watchdog.sh — 대량 임베딩·재인제스트 중 DB 가 버티는지 감시하고, 못 버티면 큐를 멈춘다.
#
# 배경 (2026-09-15 사고 3): Micro 인스턴스(RAM 1GB, shared_buffers 224MB)에서 embed UPDATE 와
# autovacuum 이 겹치자 디스크 I/O 예산이 소진돼 검색·목록 API 가 2시간 503 이었다. 그날 스크래치패드에
# 두고 쓴 감시 루프를 프로젝트에 남긴 것이다(규칙: docs/ops/db-write-control.md).
#
# 하는 일 (INTERVAL 초마다)
#   1. DB 안에서 `search_dense_only` 를 한 번 돌려 걸린 ms 를 잰다(쿼리 벡터는 첫 실행 때 DB 에서 하나 뽑아 캐시).
#   2. 큐 길이(pgmq.q_ingest_tasks)와 대상 잡의 상태를 같이 찍는다.
#   3. 검색이 THRESHOLD_MS 를 넘거나 응답이 없으면(관리 API 타임아웃) `cron.unschedule('ingest-drain')` 로
#      큐를 멈추고 종료한다. 재개는 사람이 한다(`select cron.schedule('ingest-drain','* * * * *', ...)` — 028 참조).
#   4. 큐가 0 이고 잡이 끝났으면(QUEUE_IDLE_ROUNDS 회 연속) 정상 종료한다.
#
# 사용
#   api/scripts/ingest_watchdog.sh [job_id] [threshold_ms=5000] [interval_s=60]
#   프로젝트 루트에서 실행한다(`supabase db query --linked` 가 supabase/.temp 의 링크를 읽는다).
#   로그: stdout. 백그라운드로 두고 tail 하면 된다.
#
# 이 스크립트가 재는 값은 **DB 내부 실행 시간**이다. Edge → PostgREST → DB 왕복(사용자가 겪는 지연)은
# 그보다 0.3~1s 크다. 임계값은 그걸 감안해 5s 로 뒀다(정상은 콜드 1s 미만, 웜 100ms 미만).

set -u
JOB_ID="${1:-}"
THRESHOLD_MS="${2:-5000}"
INTERVAL="${3:-60}"
QUEUE_IDLE_ROUNDS=3
OWNER_SQL="(select user_id from documents where deleted_at is null order by created_at limit 1)"

if [ ! -d supabase/.temp ]; then
  echo "프로젝트 루트에서 실행하세요 (supabase/.temp 가 없음)" >&2
  exit 2
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# 쿼리 벡터 1개를 뽑아 둔다(필터 안 된 청크 중 첫 것). 컬럼 타입이 vector 든 halfvec 든 ::text 로 받는다.
supabase db query --linked "select dense_vec::text as v from chunks where dense_vec is not null and (flags->>'filtered_reason') is null limit 1" 2>/dev/null \
  | jq -r '.rows[0].v' > "$TMP/vec.txt"
if [ ! -s "$TMP/vec.txt" ] || [ "$(cat "$TMP/vec.txt")" = "null" ]; then
  echo "쿼리 벡터를 못 뽑았다 — chunks 에 벡터가 없거나 DB 응답 없음" >&2
  exit 2
fi
V="$(cat "$TMP/vec.txt")"

idle=0
while true; do
  JOB_EXPR="null"
  if [ -n "$JOB_ID" ]; then
    JOB_EXPR="(select status||'/'||coalesce(stage,'') from ingest_jobs where id='$JOB_ID')"
  fi
  SQL="select (select count(*) from pgmq.q_ingest_tasks) as queue_len,
              $JOB_EXPR as job,
              (select count(*) from chunks where dense_vec is null and (flags->>'filtered_reason') is null) as unfiltered_null,
              (select count(*) from search_dense_only('$V'::vector, 60, 50, $OWNER_SQL)) as n,
              (extract(epoch from (clock_timestamp() - statement_timestamp())) * 1000)::int as ms"
  out="$(timeout 40 supabase db query --linked "$SQL" 2>/dev/null | jq -c '.rows[0]' 2>/dev/null)"
  ts="$(date +%T)"
  if [ -z "$out" ] || [ "$out" = "null" ]; then
    echo "$ts 응답 없음 → 큐 정지"
    supabase db query --linked "select cron.unschedule('ingest-drain')" >/dev/null 2>&1
    echo "$ts WATCHDOG: cron ingest-drain unscheduled (no response)"
    exit 1
  fi
  ms="$(echo "$out" | jq -r '.ms')"
  q="$(echo "$out" | jq -r '.queue_len')"
  job="$(echo "$out" | jq -r '.job')"
  echo "$ts search_ms=$ms n=$(echo "$out" | jq -r '.n') queue=$q job=$job unfiltered_null=$(echo "$out" | jq -r '.unfiltered_null')"
  if [ "$ms" -gt "$THRESHOLD_MS" ]; then
    supabase db query --linked "select cron.unschedule('ingest-drain')" >/dev/null 2>&1
    echo "$ts WATCHDOG: cron ingest-drain unscheduled (search_ms=$ms > $THRESHOLD_MS)"
    exit 1
  fi
  if [ "$q" = "0" ] && { [ -z "$JOB_ID" ] || [[ "$job" == succeeded/* || "$job" == failed/* ]]; }; then
    idle=$((idle + 1))
    if [ "$idle" -ge "$QUEUE_IDLE_ROUNDS" ]; then
      echo "$ts 큐 0 · 잡 종료 — 감시 종료"
      exit 0
    fi
  else
    idle=0
  fi
  sleep "$INTERVAL"
done

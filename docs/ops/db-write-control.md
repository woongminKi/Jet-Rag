# DB 대량 쓰기 통제 규칙 (Supabase Micro)

> 2026-09-15 장애 3건(청크 유실 → 검색 500 → **I/O 포화로 2시간 503**) 뒤에 세운 운영 규칙.
> 컴퓨트를 올리지 않고 Micro(RAM 1GB, shared_buffers 224MB, I/O 버스트 예산)에서 버티는 방법이다.
> 근본 조치 두 가지(필터 청크 임베딩 생략 = 마이그 035, `halfvec` 전환 = 마이그 036)는 끝났고,
> 이 문서는 **그 위에서 지켜야 할 운용 규칙**이다. 규칙을 어기면 사고 3이 재발한다.

## 0. 왜 Micro 가 무너졌나 (한 문단)

HNSW 인덱스가 237MB 로 shared_buffers(224MB)보다 컸다. 인덱스가 캐시에 안 들어가니 임베딩 UPDATE 마다
디스크를 읽고, 25k 청크를 지우고 다시 넣은 뒤 autovacuum 이 1.5시간 돌면서 I/O 버스트 예산을 다 썼다.
예산이 바닥나면 **페이지 하나 읽는 데 89ms**(정상 <1ms)가 되어 PostgREST 스키마 캐시 조회조차 타임아웃 → 전 API 503.
재시작해도 안 돌아온다(예산 문제라 캐시만 식는다). 회복까지 약 2시간.

## 1. 크기 예산 — 인덱스는 캐시 안에

| 항목 | 2026-09-15 낮 | 2026-09-16 (035+036 후) | 상한 |
|---|---:|---:|---:|
| `idx_chunks_dense` | 237~265MB | **25MB** | shared_buffers 224MB 의 절반, **110MB** |
| `chunks` 총 크기 | 506MB | **75MB** | — |
| 벡터 수(dense_vec NOT NULL) | 14,281(필터 4,612 포함) | 9,669 | halfvec 기준 약 **40,000** 개에서 110MB |

- 벡터 1개 ≈ 2KB(halfvec 1024) + 그래프 ≈ 2.7KB/행. 40k 벡터 ≈ 110MB.
- 월 1회 또는 대형 문서 인제스트 뒤 재본다:
  ```sql
  select pg_size_pretty(pg_relation_size('idx_chunks_dense')), count(*) filter (where dense_vec is not null) from chunks;
  ```
- 110MB 를 넘기면 그때 **컴퓨트 상향**을 결정한다(Small: RAM 2GB). 그 전엔 올릴 이유가 없다.

## 2. 대량 쓰기 — 야간·순차·감시

"대량"의 기준: **청크 2,000행 이상**을 한 문서에서 만들거나 지우는 일. 재인제스트, `recover_doc_local.py`,
필터 규칙 변경 뒤 재마킹, 컬럼 타입 변경 마이그가 여기 든다.

1. **야간(KST 01~06시)·저부하에만** 한다. 낮에 하지 않는다.
2. **한 번에 하나**만. 두 문서를 동시에 재인제스트하지 않는다.
3. 시작 전에 `api/scripts/ingest_watchdog.sh <job_id>` 를 띄운다. 검색이 5s 를 넘거나 DB 가 응답하지 않으면
   스크립트가 `ingest-drain` 을 내린다. 재개는 사람이 한다.
4. 임베딩은 **매분 cron(`* * * * *`) + 태스크당 64개** 그대로 둔다(약 64벡터/분). 더 빠르게 돌리지 않는다.
   12,408개 잔여분이면 약 3.2시간 — 그게 맞다. 급하면 컴퓨트를 올리는 게 맞지, 주기를 줄이는 게 아니다.
5. **운영 문서를 측정 목적으로 재인제스트하지 않는다**(사고 1). 처리량 실측은 fixture 문서로.
6. 재인제스트 `POST /documents/{id}/reingest` 는 청크 25k 문서에서 Edge 요청 상한에 걸려 500 이 난다(P1 후속: 동기 리셋의 큐 비동기화).
   그때까지 대형 문서 재인제스트는 `reingestDocument` 를 로컬 Deno 로 부른다(work-log 2026-09-15 §8 참조).

## 3. autovacuum — 켜 두되, 대량 쓰기 직후는 손으로

- `chunks` 의 autovacuum 은 **켜 둔다**(2026-09-16 01:52 복구, 036 재작성으로 죽은 행 0 인 상태에서 시작).
  사고 3 때 껐던 건 인덱스 265MB 를 vacuum 하느라 1.5시간 I/O 를 먹어서였고, 지금은 25MB 라 같은 일이 안 난다.
- 대량 삭제(청크 2,000행 이상) 직후에는 autovacuum 이 알아서 돌기 전에 **야간에 손으로**:
  ```sql
  vacuum (analyze) chunks;   -- 120s 안에 안 끝나면 §4 의 pg_cron 경로
  ```
- 다시 끄는 조건은 하나다: `pg_stat_activity` 에 `autovacuum: VACUUM ... chunks` 가 **10분 이상** 떠 있고 검색이 느려질 때.
  ```sql
  alter table chunks set (autovacuum_enabled = false);   -- 끝나면 반드시 true 로
  ```
  autovacuum 워커는 비-superuser 가 취소 못 한다(42501). 끄면 다음 사이클부터 안 뜰 뿐이다.

## 4. 120초를 넘는 문장은 pg_cron 으로

`supabase db query --linked` 는 **statement_timeout 120s** 다(실측: `pg_sleep(150)` → 57014). 테이블 재작성·인덱스 빌드·
대형 VACUUM 은 pg_cron 일회성 잡으로 돌린다. DB 안에서 실행되므로 HTTP 와 무관하고, 잡이 자기 자신을 unschedule 한다.

```sql
select cron.schedule('<이름>', '* * * * *', $job$
  SET statement_timeout = 0;
  SET maintenance_work_mem = '128MB';           -- 인덱스 빌드면. 1GB RAM 에서 이 이상 올리지 않는다
  DO $do$ BEGIN
    EXECUTE 'LOCK TABLE public.chunks IN ACCESS EXCLUSIVE MODE';   -- 1분 뒤 2번째 실행이 뜨면 여기서 막혔다가 건너뛴다
    IF <아직 안 됐다는 조건> THEN
      EXECUTE '<문장 1>'; EXECUTE '<문장 2>';
    END IF;
  END $do$;
  SELECT cron.unschedule('<이름>') WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = '<이름>');
$job$);
```

- 멱등 조건(`IF`)이 핵심이다. 첫 실행이 60초를 넘기면 두 번째 실행이 뜨는데, LOCK 에서 기다렸다가 조건이 거짓이라 건너뛴다.
- 진행은 `cron.job_run_details` 와 `pg_stat_activity where application_name like 'pg_cron%'` 로 본다.
- 036 이 이 경로로 37초에 끝났다(2026-09-16 01:46). 스크래치 예시는 `api/migrations/036_dense_vec_halfvec.sql` 헤더.
- `VACUUM` 은 트랜잭션 블록 안에서 못 돈다 → DO 블록 밖에, 잡의 최상위 문장으로 둔다.

## 5. 절대 하지 않는 것

- `ALTER DATABASE ... SET hnsw.iterative_scan` — dense 전용 모드가 1s→10~14s 로 악화됐다(2026-09-15, PostgREST 파라미터 LIMIT 의 generic plan 과 상호작용). `hnsw.ef_search` 만 올리는 건 별개로 실측한다(work-log 2026-09-16).
- `ALTER FUNCTION ... SET ...` — SQL 함수 인라인을 막아 계획이 바뀐다.
- 필터된 청크(`flags->>'filtered_reason'`)에 벡터를 채우는 것 — 검색이 안 보는 벡터다. embed 단계가 건너뛴다(035).
- 낮에 청크 2,000행 이상 쓰기.

## 6. 관련 파일

- `api/migrations/035_dense_vec_skip_filtered.sql` — 필터 벡터 비우기(배치 함수 `null_filtered_dense_vec`).
- `api/migrations/036_dense_vec_halfvec.sql` — halfvec 전환(헤더에 실행 기록).
- `api/scripts/ingest_watchdog.sh` — 감시견.
- `api/scripts/golden_batch_smoke.py` — 검색 품질 회귀(120행, `JETRAG_API_BASE_URL=https://jetrag-api.woong-s.com`, `.env` 의 service key).
- 메모리: `jetrag_edge_chunk_large_doc_hazard.md`.

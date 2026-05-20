"""D2 (2026-05-20) — Storage 객체를 `user/<uid>/` prefix 로 1회성 이관.

두 가지 deploy 패턴 (마이그 020 헤더 참조):

패턴 A — 단순 (단일유저 베타 권장)
    Step 1. 코드 deploy (HEAD 에 본 sprint commit 포함)
    Step 2. 마이그 020 PART 1 (documents.storage_path 일괄 prefix UPDATE)
    Step 3. ★ 본 스크립트 default 모드 실행 ★ — Storage 객체 move + old 제거
    Step 4. 마이그 020 PART 2 (Storage RLS 정책 활성화)
    특성: PART 1 ~ Step 3 사이 짧은 (~1분) downtime. 심야/maintenance 권장.

패턴 B — downtime 0 (멀티유저 운영)
    Step 1. 코드 deploy
    Step 2. ★ 본 스크립트 `--copy-only` 실행 ★ — old/new 양쪽 보존
    Step 3. 마이그 020 PART 1
    Step 4. ★ 본 스크립트 `--cleanup-only` 실행 ★ — new 존재 확인 후 old 제거
    Step 5. 마이그 020 PART 2
    특성: 객체 일시적으로 2배. PART 1 직후도 응답 정상 (객체가 new path 에 이미
    존재). 응답 404 0.

전제
- D1 ship 완료, 018(default_user_id → 본인 UUID 이관) 적용.
- 패턴 A: PART 1 가 documents.storage_path 를 새 위치로 갱신한 상태에서 본 스크립트.
- 패턴 B: --copy-only 는 PART 1 적용 전 (documents.storage_path 가 old) 에 실행.
         --cleanup-only 는 PART 1 적용 후 (documents.storage_path 가 new) 에 실행.

동작 (모드별)
- default (move): documents.storage_path = new path 전제. old → new 이동 후 old 제거.
- --copy-only:   documents.storage_path = old path 전제. new path 계산
                 (`user/<user_id>/<old>`) 후 old → new 복사 (old 보존).
- --cleanup-only: documents.storage_path = new path 전제. new 존재 확인 후 old 제거.

old_path / new_path 도출 규칙
- default (move) / --cleanup-only: storage_path 는 이미 new (`user/<uid>/<rest>`).
  old = storage_path 에서 첫 `user/<uid>/` prefix 제거.
- --copy-only: storage_path 는 아직 old (PART 1 미적용). new = `user/<user_id>/<old>`.

사용법
    cd api
    uv run python scripts/migrate_storage_to_per_user.py --dry-run     # 영향만 (default 모드)
    uv run python scripts/migrate_storage_to_per_user.py               # default = move
    uv run python scripts/migrate_storage_to_per_user.py --copy-only   # 패턴 B Step 2
    uv run python scripts/migrate_storage_to_per_user.py --cleanup-only # 패턴 B Step 4

stdout
    processed=N moved=M copied=C cleaned=L skipped=K errors=E
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.db import get_supabase_client

logger = logging.getLogger("migrate_storage_to_per_user")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)

# bucket 안의 "이미 존재" 응답 시 폴백 download 후 upload 의 upsert 충돌 회피용.
_LIST_LIMIT = 1000


@dataclass(frozen=True)
class _Counters:
    processed: int = 0
    moved: int = 0
    copied: int = 0
    cleaned: int = 0
    skipped: int = 0
    errors: int = 0


def _split_user_prefix(new_path: str) -> tuple[str, str] | None:
    """`user/<uid>/<rest>` → (uid, rest). 패턴 미일치면 None.

    rest 는 첫 user prefix 제거 후의 옛 path. legacy `<sha256>{ext}` 또는
    legacy `pending/default/<uuid>{ext}` 형식.
    """
    if not new_path.startswith("user/"):
        return None
    parts = new_path.split("/", 2)
    if len(parts) < 3:
        return None
    _, uid, rest = parts
    if not uid or not rest:
        return None
    return uid, rest


def _object_exists(client: Any, bucket: str, path: str) -> bool:
    """Storage 객체 존재 확인 — list() 결과 첫 매칭.

    supabase-py 의 list 는 폴더 단위 nav 라 정확한 단건 조회는 지원 빈약. 본
    스크립트는 sha256 prefix / pending/default prefix 두 패턴만 다루므로 정확한
    상위 폴더 list 후 이름 매칭으로 단순화.
    """
    folder, _, leaf = path.rpartition("/")
    try:
        objs = client.storage.from_(bucket).list(
            path=folder or "",
            options={"limit": _LIST_LIMIT, "search": leaf},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("list 실패 — exists 판정 보수적 false (%s): %s", path, exc)
        return False
    return any((obj.get("name") == leaf) for obj in (objs or []))


def _try_move(
    client: Any, bucket: str, old_path: str, new_path: str, *, dry_run: bool
) -> str:
    """default 모드 — old → new 이동 후 old 제거.

    반환값: "moved" / "skipped" / "error" (로그에 사유).
    dry_run=True 면 실제 호출 0 (영향만 보고).
    """
    if dry_run:
        return "moved"

    # 1) 이미 new_path 존재면 skip (재실행 멱등)
    if _object_exists(client, bucket, new_path):
        logger.info("skip — 이미 new_path 존재: %s", new_path)
        return "skipped"

    # 2) old_path 없으면 skip (이미 옮겨졌거나 처음부터 없음)
    if not _object_exists(client, bucket, old_path):
        logger.info("skip — old_path 없음 (이미 이동했거나 미존재): %s", old_path)
        return "skipped"

    storage = client.storage.from_(bucket)

    # 3) move() 시도
    try:
        storage.move(old_path, new_path)
        logger.info("moved (native): %s → %s", old_path, new_path)
        return "moved"
    except Exception as exc:  # noqa: BLE001 — 폴백 download/upload/delete
        logger.warning("move() 실패 — 폴백 사용 (%s → %s): %s", old_path, new_path, exc)

    # 4) 폴백 download + upload(upsert) + delete
    try:
        data = storage.download(old_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("download 실패 (%s): %s", old_path, exc)
        return "error"

    try:
        storage.upload(
            path=new_path,
            file=data,
            file_options={"upsert": "true"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("upload 실패 (%s): %s", new_path, exc)
        return "error"

    try:
        storage.remove([old_path])
    except Exception as exc:  # noqa: BLE001
        # remove 실패는 멱등성 영향 — 다음 실행에서 _object_exists(old) → 재 skip
        logger.warning("remove 실패 (남겨도 무방, 다음 실행 skip): %s — %s", old_path, exc)

    logger.info("moved (fallback): %s → %s", old_path, new_path)
    return "moved"


def _try_copy(
    client: Any, bucket: str, old_path: str, new_path: str, *, dry_run: bool
) -> str:
    """--copy-only 모드 — old → new 복사 (old 보존). 패턴 B Step 2.

    반환값: "copied" / "skipped" / "error".
    dry_run=True 면 실제 호출 0.
    """
    if dry_run:
        return "copied"

    # 1) 이미 new_path 존재면 skip (재실행 멱등)
    if _object_exists(client, bucket, new_path):
        logger.info("skip — 이미 new_path 존재: %s", new_path)
        return "skipped"

    # 2) old_path 없으면 skip (이미 옮겨졌거나 처음부터 없음)
    if not _object_exists(client, bucket, old_path):
        logger.info("skip — old_path 없음 (copy 대상 없음): %s", old_path)
        return "skipped"

    storage = client.storage.from_(bucket)

    # 3) Supabase Storage SDK 의 copy() 가 있으면 사용, 없으면 download/upload 폴백.
    #    supabase-py 1.x 는 copy() 미지원 — 폴백 path 가 안전.
    try:
        data = storage.download(old_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("download 실패 (%s): %s", old_path, exc)
        return "error"

    try:
        storage.upload(
            path=new_path,
            file=data,
            file_options={"upsert": "true"},
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("upload 실패 (%s): %s", new_path, exc)
        return "error"

    logger.info("copied: %s → %s (old 보존)", old_path, new_path)
    return "copied"


def _try_cleanup(
    client: Any, bucket: str, old_path: str, new_path: str, *, dry_run: bool
) -> str:
    """--cleanup-only 모드 — new path 존재 확인 후 old 제거. 패턴 B Step 4.

    반환값: "cleaned" / "skipped" / "error".
    dry_run=True 면 실제 호출 0.
    """
    if dry_run:
        return "cleaned"

    # 1) new_path 가 없으면 보수적으로 skip (혹시 PART 1 미적용 / copy 누락 상태에서
    #    잘못 호출되어 old 가 사라지는 사고를 방어).
    if not _object_exists(client, bucket, new_path):
        logger.warning(
            "skip — new_path 없음 (--copy-only 누락 추정, old 보존): %s", new_path,
        )
        return "skipped"

    # 2) old_path 가 이미 없으면 skip (다른 실행에서 이미 정리됨)
    if not _object_exists(client, bucket, old_path):
        logger.info("skip — old_path 이미 없음 (정리 완료): %s", old_path)
        return "skipped"

    storage = client.storage.from_(bucket)
    try:
        storage.remove([old_path])
    except Exception as exc:  # noqa: BLE001
        logger.error("remove 실패 (%s): %s", old_path, exc)
        return "error"

    logger.info("cleaned: %s (new 존재 확인 후 제거)", old_path)
    return "cleaned"


def _resolve_old_new(
    doc: dict, *, mode: str, default_user_id: str,
) -> tuple[str, str] | None:
    """모드별 old_path / new_path 도출.

    반환값: (old_path, new_path) 또는 None (skip 대상).

    - default / cleanup-only: storage_path 가 이미 new (`user/<uid>/<rest>`). old 는
      prefix 제거. uid 가 doc.user_id 와 불일치 시 None.
    - copy-only: storage_path 가 아직 old (PART 1 미적용). new = `user/<user_id>/<old>`.
                 user_id 는 doc.user_id (fallback: default_user_id) 그대로 사용.
                 단 storage_path 가 이미 user/ prefix 인 row 는 멱등 skip → None.
    """
    storage_path = doc["storage_path"]

    if mode == "copy-only":
        # PART 1 미적용 전제 — 이미 prefix 있는 row 는 skip (멱등)
        if storage_path.startswith("user/"):
            return None
        uid = str(doc.get("user_id") or default_user_id)
        if not uid:
            return None
        old_path = storage_path
        new_path = f"user/{uid}/{old_path}"
        return old_path, new_path

    # default (move) / cleanup-only — storage_path 는 이미 new
    split = _split_user_prefix(storage_path)
    if split is None:
        return None
    uid, old_rest = split
    if uid != str(doc["user_id"]):
        return None
    return old_rest, storage_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Storage 객체를 user/<uid>/ prefix 로 1회성 이관 (D2).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="영향 row 수만 보고 (실제 Storage 호출 0).",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--copy-only",
        action="store_true",
        help=(
            "패턴 B Step 2 — old → new 복사 (old 보존). PART 1 적용 전 실행. "
            "documents.storage_path 는 아직 old path."
        ),
    )
    mode_group.add_argument(
        "--cleanup-only",
        action="store_true",
        help=(
            "패턴 B Step 4 — new 존재 확인 후 old 제거. PART 1 적용 후 실행. "
            "documents.storage_path 는 이미 new path."
        ),
    )
    args = parser.parse_args(argv)

    if args.copy_only:
        mode = "copy-only"
    elif args.cleanup_only:
        mode = "cleanup-only"
    else:
        mode = "default"

    settings = get_settings()
    bucket = settings.supabase_storage_bucket
    default_uid = settings.default_user_id
    client = get_supabase_client()

    docs_resp = (
        client.table("documents")
        .select("id, user_id, storage_path")
        .is_("deleted_at", "null")
        .execute()
    )
    docs = docs_resp.data or []
    logger.info(
        "documents 대상 row=%d (bucket=%s, mode=%s, dry_run=%s)",
        len(docs), bucket, mode, args.dry_run,
    )

    counters = _Counters()
    for doc in docs:
        resolved = _resolve_old_new(doc, mode=mode, default_user_id=default_uid)
        if resolved is None:
            # 모드별 skip 조건 — 로그는 _resolve_old_new 호출처에서 단순화.
            logger.warning(
                "skip — 모드 %s 의 path 도출 실패 (doc_id=%s storage_path=%s user_id=%s)",
                mode, doc.get("id"), doc.get("storage_path"), doc.get("user_id"),
            )
            counters = _replace(counters, processed=counters.processed + 1,
                                skipped=counters.skipped + 1)
            continue
        old_path, new_path = resolved

        if mode == "copy-only":
            result = _try_copy(client, bucket, old_path, new_path, dry_run=args.dry_run)
        elif mode == "cleanup-only":
            result = _try_cleanup(client, bucket, old_path, new_path, dry_run=args.dry_run)
        else:
            result = _try_move(client, bucket, old_path, new_path, dry_run=args.dry_run)

        if result == "moved":
            counters = _replace(counters, processed=counters.processed + 1,
                                moved=counters.moved + 1)
        elif result == "copied":
            counters = _replace(counters, processed=counters.processed + 1,
                                copied=counters.copied + 1)
        elif result == "cleaned":
            counters = _replace(counters, processed=counters.processed + 1,
                                cleaned=counters.cleaned + 1)
        elif result == "skipped":
            counters = _replace(counters, processed=counters.processed + 1,
                                skipped=counters.skipped + 1)
        else:
            counters = _replace(counters, processed=counters.processed + 1,
                                errors=counters.errors + 1)

    summary = (
        f"processed={counters.processed} moved={counters.moved} "
        f"copied={counters.copied} cleaned={counters.cleaned} "
        f"skipped={counters.skipped} errors={counters.errors}"
    )
    print(summary)
    logger.info(summary)
    return 0 if counters.errors == 0 else 1


def _replace(c: _Counters, **kw: int) -> _Counters:
    """dataclass(frozen) 누적용 — 작은 비용."""
    return _Counters(
        processed=kw.get("processed", c.processed),
        moved=kw.get("moved", c.moved),
        copied=kw.get("copied", c.copied),
        cleaned=kw.get("cleaned", c.cleaned),
        skipped=kw.get("skipped", c.skipped),
        errors=kw.get("errors", c.errors),
    )


if __name__ == "__main__":
    sys.exit(main())

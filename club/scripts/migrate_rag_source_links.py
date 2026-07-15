#!/usr/bin/env python3
"""Миграция ссылок в Chroma expert_materials: public/private вместо legacy group_message_link."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chromadb

from rag.source_links import apply_classified_link_metadata, classify_source_link_visibility


def migrate(*, chroma_path: str, dry_run: bool) -> int:
    client = chromadb.PersistentClient(path=chroma_path)
    coll = client.get_collection("expert_materials")
    total = coll.count()
    print(f"chunks: {total}")

    stats = {
        "updated": 0,
        "unchanged": 0,
        "to_public": 0,
        "to_private": 0,
        "cleared_legacy": 0,
    }

    offset = 0
    page = 500
    while True:
        raw = coll.get(include=["metadatas"], limit=page, offset=offset)
        ids, metas = raw["ids"], raw["metadatas"]
        if not ids:
            break

        update_ids: list[str] = []
        update_metas: list[dict] = []

        for cid, meta in zip(ids, metas):
            m = dict(meta or {})
            had_legacy = bool((m.get("group_message_link") or "").strip())
            new_meta, changed = apply_classified_link_metadata(m)
            if not changed and not had_legacy:
                stats["unchanged"] += 1
                continue

            url = (new_meta.get("public_source_link") or new_meta.get("private_source_link") or "")
            vis = classify_source_link_visibility(url) if url else None
            if vis == "public":
                stats["to_public"] += 1
            elif vis == "private":
                stats["to_private"] += 1
            if had_legacy:
                stats["cleared_legacy"] += 1

            update_ids.append(cid)
            update_metas.append(new_meta)
            stats["updated"] += 1

        if update_ids and not dry_run:
            coll.update(ids=update_ids, metadatas=update_metas)

        offset += len(ids)

    print("stats:", stats)
    if dry_run:
        print("DRY RUN — в Chroma ничего не записано")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--chroma-path",
        default=os.getenv(
            "RAG_CHROMA_PERSIST_DIR",
            "/home/appuser/dev/kostya/avatar_kostya/chroma_data",
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(migrate(chroma_path=args.chroma_path, dry_run=args.dry_run))


if __name__ == "__main__":
    main()

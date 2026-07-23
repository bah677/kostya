"""Откат загрузки Telemost → RAG по номеру встречи (meeting_id)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from telemost_mail.cache_keys import telemost_mail_cache_key
from storage.db.rag_import_cache import IMPORT_TELEMOST_MAIL

logger = logging.getLogger(__name__)


def _delete_chroma_for_meeting(
    material_index,
    *,
    meeting_id: str,
    imap_uid: str,
) -> int:
    """
    Удаляет чанки expert_materials, связанные со встречей.

    Фильтры (OR по наборам id):
    - ``telemost_imap_uid`` == imap_uid конспекта
    - ``meeting_id`` == номер (новые записи)
    - ``private_source_link`` / ``public_source_link`` содержит номер
    """
    if material_index is None:
        return 0
    coll = material_index._store.expert_collection
    mid = (meeting_id or "").strip()
    uid = (imap_uid or "").strip()
    to_delete: List[str] = []

    def _add_ids(where: Dict[str, Any]) -> None:
        try:
            r = coll.get(where=where, include=[])
            for i in r.get("ids") or []:
                if i not in to_delete:
                    to_delete.append(i)
        except Exception as e:
            logger.warning("chroma get where=%s: %s", where, e)

    if uid:
        _add_ids({"telemost_imap_uid": uid})
    if mid:
        _add_ids({"meeting_id": mid})
        # Старые чанки без meeting_id — ищем по ссылке Телемоста.
        needle = f"/j/{mid}"
        try:
            offset = 0
            while True:
                batch = coll.get(
                    where={"import_source": "telemost_mail"},
                    include=["metadatas"],
                    limit=500,
                    offset=offset,
                )
                ids = batch.get("ids") or []
                if not ids:
                    break
                for i, m in enumerate(batch.get("metadatas") or []):
                    m = m or {}
                    link = str(
                        m.get("private_source_link")
                        or m.get("public_source_link")
                        or ""
                    )
                    if needle in link or mid in link:
                        cid = ids[i]
                        if cid not in to_delete:
                            to_delete.append(cid)
                offset += len(ids)
                if len(ids) < 500:
                    break
        except Exception as e:
            logger.warning("chroma scan by link meeting_id=%s: %s", mid, e)

    if not to_delete:
        return 0
    try:
        coll.delete(ids=to_delete)
        return len(to_delete)
    except Exception as e:
        logger.error("chroma delete meeting_id=%s: %s", mid, e, exc_info=True)
        return 0


async def rollback_telemost_meeting(
    storage,
    material_index,
    meeting_id: str,
) -> Dict[str, Any]:
    """
    Откатывает RAG-загрузку конспекта Телемоста по № встречи.

    - удаляет чанки в Chroma
    - снимает запись в ``rag_import_cache``
    - сбрасывает ``telemost_mail_pending`` в ``pending`` (можно снова /telemost_load)
    """
    mid = (meeting_id or "").strip()
    out: Dict[str, Any] = {
        "meeting_id": mid,
        "ok": False,
        "chunks_deleted": 0,
        "cache_deleted": False,
        "pending_reset": False,
        "pending_id": None,
        "imap_uid": None,
        "error": "",
    }
    if not mid:
        out["error"] = "пустой meeting_id"
        return out

    row = await storage.get_telemost_pending_by_meeting_id(mid)
    if not row:
        # Fallback: запись есть, конспект ещё не связан — всё равно почистим chroma по ссылке.
        chunks = _delete_chroma_for_meeting(
            material_index, meeting_id=mid, imap_uid=""
        )
        out["chunks_deleted"] = chunks
        out["ok"] = True
        out["error"] = "конспект в pending не найден; chroma по ссылке очищена" if chunks else (
            "конспект не найден, в chroma тоже пусто"
        )
        return out

    pid = UUID(str(row["id"]))
    imap_uid = str(row.get("imap_uid") or "")
    message_id = str(row.get("message_id") or "")
    out["pending_id"] = str(pid)
    out["imap_uid"] = imap_uid

    out["chunks_deleted"] = _delete_chroma_for_meeting(
        material_index, meeting_id=mid, imap_uid=imap_uid
    )

    cache_key = telemost_mail_cache_key(imap_uid=imap_uid, message_id=message_id)
    out["cache_deleted"] = await storage.rag_import_cache_delete(
        IMPORT_TELEMOST_MAIL, cache_key
    )

    out["pending_reset"] = await storage.reset_telemost_mail_pending_for_reindex(pid)
    out["ok"] = True
    return out

"""PDF юридических документов: file_id из env / media_file_ids."""

from __future__ import annotations

from typing import Literal, Optional

from bot.texts import media_file_ids as media_ids
from config import config

LegalDocKind = Literal["offer", "policy", "consent"]


def configured_legal_pdf_file_id(kind: LegalDocKind) -> Optional[str]:
    env_map = {
        "offer": config.PUBLIC_OFFER_PDF_FILE_ID,
        "policy": config.PRIVACY_POLICY_PDF_FILE_ID,
        "consent": config.PERSONAL_DATA_CONSENT_PDF_FILE_ID,
    }
    media_map = {
        "offer": getattr(media_ids, "PUBLIC_OFFER_PDF_FILE_ID", None),
        "policy": getattr(media_ids, "PRIVACY_POLICY_PDF_FILE_ID", None),
        "consent": getattr(media_ids, "PERSONAL_DATA_CONSENT_PDF_FILE_ID", None),
    }
    fid = (env_map.get(kind) or "").strip()
    if not fid:
        fid = (media_map.get(kind) or "").strip()
    return fid or None

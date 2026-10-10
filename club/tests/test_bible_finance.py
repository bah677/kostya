"""Deep link /start finplan — план чтения «Библия и финансы»."""

from bot.features.bible_finance import (
    build_finplan_deeplink,
    configured_finplan_pdf_file_id,
)
from bot.services.attribution_touch import parse_start_payload
from bot.texts import ru_bible_finance as txt


def test_finplan_deeplink():
    assert build_finplan_deeplink("Talk_God_Bot") == (
        "https://t.me/Talk_God_Bot?start=finplan"
    )
    assert txt.START_PARAM_FINPLAN == "finplan"


def test_finplan_caption_fits_telegram():
    assert "Библия и финансы" in txt.PDF_CAPTION
    assert len(txt.PDF_CAPTION) <= 1024


def test_finplan_pdf_file_id_configured():
    fid = configured_finplan_pdf_file_id()
    assert fid.startswith("BQACAg")
    assert len(fid) > 20


def test_finplan_is_marketing_touch():
    touch = parse_start_payload("finplan")
    assert touch is not None
    assert touch.touch_key == "finplan"
    assert touch.touch_kind == "start_payload"


def test_finplan_group_post_copy():
    assert "план чтения" in txt.GROUP_POST_HTML.lower()
    assert txt.BTN_GET_PLAN == "Получить план чтения"
    assert txt.GROUP_TOPIC_ID == 1503

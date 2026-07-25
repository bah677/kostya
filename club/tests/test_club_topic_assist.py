"""Тесты батч topic-assist."""

from datetime import datetime, timedelta, timezone

from bot.services.club_topic_assist_pipeline import (
    batch_windows,
    format_transcript,
    parse_triage,
)


def test_parse_triage_items(monkeypatch):
    import bot.services.club_topic_assist_pipeline as mod

    class C:
        CLUB_TOPIC_ASSIST_PUBLIC_ENABLED = False

    monkeypatch.setattr(mod, "config", C)
    items = parse_triage(
        '{"items":[{"reply_to_message_id":10,"user_id":1,'
        '"visibility":"public","question_summary":"when","reason":"org"}]}'
    )
    assert len(items) == 1
    assert items[0].reply_to_message_id == 10
    assert items[0].visibility == "ephemeral"  # public disabled


def test_parse_triage_empty():
    assert parse_triage('{"items":[]}') == []
    assert parse_triage("nope") == []


def test_format_transcript_marks_candidates():
    rows = [
        {
            "telegram_message_id": 1,
            "user_id": 5,
            "username": "a",
            "first_name": "",
            "content": "hello",
            "created_at": datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc),
        }
    ]
    blob = format_transcript(rows, candidate_ids={1})
    assert "CANDIDATE" in blob
    assert "msg_id=1" in blob


def test_batch_windows_order(monkeypatch):
    import bot.services.club_topic_assist_pipeline as mod

    class C:
        CLUB_TOPIC_ASSIST_LAG_MINUTES = 2
        CLUB_TOPIC_ASSIST_WINDOW_MINUTES = 12
        CLUB_TOPIC_ASSIST_CONTEXT_EXTRA_MINUTES = 20

    monkeypatch.setattr(mod, "config", C)
    now = datetime(2026, 7, 25, 15, 0, tzinfo=timezone.utc)
    ctx_start, cand_start, cand_end = batch_windows(now)
    assert ctx_start < cand_start < cand_end < now
    assert cand_end == now - timedelta(minutes=2)
    assert cand_start == cand_end - timedelta(minutes=12)
    assert ctx_start == cand_start - timedelta(minutes=20)

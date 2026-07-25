"""Тесты topic-assist: classify parse, context buffer, public gate."""

from bot.services.club_topic_assist_context import TopicAssistContextBuffer
from bot.services.club_topic_assist_pipeline import _parse_classify


def test_parse_classify_intervene_ephemeral():
    r = _parse_classify(
        '{"intervene": true, "visibility": "ephemeral", "reason": "when stream"}'
    )
    assert r.intervene is True
    assert r.visibility == "ephemeral"
    assert "stream" in r.reason


def test_parse_classify_bad_json():
    r = _parse_classify("not-json")
    assert r.intervene is False


def test_context_buffer_tail():
    buf = TopicAssistContextBuffer(maxlen=3)
    buf.push(1, 10, "a", 1)
    buf.push(1, 10, "b", 2)
    buf.push(1, 10, "c", 3)
    buf.push(1, 10, "d", 4)
    texts = [m.text for m in buf.tail(1, 10)]
    assert texts == ["b", "c", "d"]
    assert "b" in buf.format_tail(1, 10)

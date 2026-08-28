"""Ежедневные молитвенные ролики (тренды → аудио → сток → TG / YouTube)."""

__all__ = ["run_daily_youtube_prayer_pipeline"]


def run_daily_youtube_prayer_pipeline(*args, **kwargs):
    from youtube_prayer.pipeline import run_daily_youtube_prayer_pipeline as _run

    return _run(*args, **kwargs)

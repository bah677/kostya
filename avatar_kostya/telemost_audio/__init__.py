"""Мини-подкасты (до 90 сек) из аудио-записей Телемоста → голосовые в Telegram."""

from telemost_audio.pipeline import enqueue_telemost_audio, enqueue_telemost_audio_last

__all__ = ["enqueue_telemost_audio", "enqueue_telemost_audio_last"]

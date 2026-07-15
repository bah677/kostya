-- Расширение token_usage: провайдер, тип запроса, сырой usage JSON и агрегируемые детали.
-- Применить вместе с обновлением Python-кода, пишущего в таблицу.

ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'openai';
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS request_kind TEXT;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS raw_usage JSONB;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS cached_input_tokens BIGINT;
ALTER TABLE token_usage ADD COLUMN IF NOT EXISTS reasoning_output_tokens BIGINT;

COMMENT ON COLUMN token_usage.provider IS 'openai | deepseek | anthropic | ...';
COMMENT ON COLUMN token_usage.request_kind IS 'chat_completion | whisper_transcription | vision | embedding | …';
COMMENT ON COLUMN token_usage.raw_usage IS 'Сырой ответ billing API (JSON) для аудита и нестандартных полей';
COMMENT ON COLUMN token_usage.cached_input_tokens IS 'prompt cache / cached input (если провайдер отдаёт)';
COMMENT ON COLUMN token_usage.reasoning_output_tokens IS 'reasoning tokens (если есть, напр. o-series)';

CREATE INDEX IF NOT EXISTS idx_token_usage_provider_created
  ON token_usage (provider, created_date DESC);
CREATE INDEX IF NOT EXISTS idx_token_usage_prov_model_created
  ON token_usage (provider, model, created_date DESC);
CREATE INDEX IF NOT EXISTS idx_token_usage_request_kind
  ON token_usage (request_kind, created_date DESC)
  WHERE request_kind IS NOT NULL;

-- Seed agent registry (idempotent)

INSERT INTO agents (id, display_name, role, description, enabled, schedule_cron, config_json)
VALUES
(
  'bible_bot_manager',
  'Bible Bot Manager',
  'bot_manager',
  'Ежедневный менеджер Библейского бота: KPI stickiness/donations/club transitions, рекомендации, память.',
  TRUE,
  '0 3 * * *',
  '{"dialog_sample_limit": 80, "measure_after_days": 7}'::jsonb
),
(
  'club_bot_manager',
  'Club Bot Manager',
  'bot_manager',
  'Заглушка: менеджер клубного бота (будущая роль).',
  FALSE,
  NULL,
  '{}'::jsonb
),
(
  'copywriter',
  'Copywriter',
  'content',
  'Заглушка: копирайтер экосистемы.',
  FALSE,
  NULL,
  '{}'::jsonb
),
(
  'scriptwriter',
  'Scriptwriter',
  'content',
  'Заглушка: сценарист.',
  FALSE,
  NULL,
  '{}'::jsonb
),
(
  'reels_maker',
  'Reels Maker',
  'content',
  'Заглушка: рилсмейкер.',
  FALSE,
  NULL,
  '{}'::jsonb
),
(
  'producer',
  'Producer',
  'content',
  'Заглушка: продюсер.',
  FALSE,
  NULL,
  '{}'::jsonb
)
ON CONFLICT (id) DO UPDATE SET
  display_name = EXCLUDED.display_name,
  role = EXCLUDED.role,
  description = EXCLUDED.description,
  schedule_cron = COALESCE(EXCLUDED.schedule_cron, agents.schedule_cron),
  updated_at = NOW();

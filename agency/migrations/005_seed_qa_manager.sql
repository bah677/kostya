-- QA Manager agent

INSERT INTO agents (id, display_name, role, description, enabled, schedule_cron, config_json)
VALUES
(
  'qa_manager',
  'QA Manager',
  'qa',
  'Ночной разбор ERROR-логов club/biblia/avatar_kostya (все ротации) → короткие ТЗ на баги.',
  TRUE,
  '0 3 * * *',
  '{"top_clusters": 12}'::jsonb
)
ON CONFLICT (id) DO UPDATE SET
  display_name = EXCLUDED.display_name,
  role = EXCLUDED.role,
  description = EXCLUDED.description,
  enabled = EXCLUDED.enabled,
  schedule_cron = COALESCE(EXCLUDED.schedule_cron, agents.schedule_cron),
  config_json = EXCLUDED.config_json,
  updated_at = NOW();

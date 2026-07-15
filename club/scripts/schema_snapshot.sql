-- Снимок схемы public для сравнения двух БД (prod vs dev).
-- Запуск: psql -U postgres -d ИМЯ_БД -v ON_ERROR_STOP=1 -f schema_snapshot.sql
-- Все секции отсортированы стабильно для diff.

\pset tuples_only off
\pset footer off
\pset pager off

SELECT '========== META: database ==========' AS section;
SELECT current_database() AS database, current_user AS db_user, now() AS snapshot_at;

SELECT '========== EXTENSIONS ==========' AS section;
SELECT extname, extversion
FROM pg_extension
ORDER BY extname;

SELECT '========== ENUM TYPES (labels) ==========' AS section;
SELECT t.typname AS enum_type, e.enumlabel
FROM pg_type t
JOIN pg_enum e ON e.enumtypid = t.oid
JOIN pg_namespace n ON n.oid = t.typnamespace
WHERE n.nspname = 'public'
ORDER BY t.typname, e.enumsortorder;

SELECT '========== TABLES (base) ==========' AS section;
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;

SELECT '========== COLUMNS ==========' AS section;
SELECT c.table_name,
       c.ordinal_position,
       c.column_name,
       c.data_type,
       c.udt_name,
       c.character_maximum_length,
       c.numeric_precision,
       c.numeric_scale,
       c.datetime_precision,
       c.is_nullable,
       c.column_default,
       c.is_generated,
       c.generation_expression
FROM information_schema.columns c
WHERE c.table_schema = 'public'
  AND c.table_name IN (
      SELECT table_name
      FROM information_schema.tables
      WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
  )
ORDER BY c.table_name, c.ordinal_position;

SELECT '========== PRIMARY KEYS ==========' AS section;
SELECT tc.table_name, tc.constraint_name, kcu.column_name, kcu.ordinal_position
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_schema = kcu.constraint_schema
 AND tc.constraint_name = kcu.constraint_name
WHERE tc.table_schema = 'public'
  AND tc.constraint_type = 'PRIMARY KEY'
ORDER BY tc.table_name, kcu.ordinal_position;

SELECT '========== UNIQUE CONSTRAINTS ==========' AS section;
SELECT tc.table_name, tc.constraint_name, kcu.column_name, kcu.ordinal_position
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_schema = kcu.constraint_schema
 AND tc.constraint_name = kcu.constraint_name
WHERE tc.table_schema = 'public'
  AND tc.constraint_type = 'UNIQUE'
ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position;

SELECT '========== FOREIGN KEYS ==========' AS section;
SELECT tc.table_name,
       tc.constraint_name,
       kcu.column_name,
       kcu.ordinal_position,
       ccu.table_name AS foreign_table_name,
       ccu.column_name AS foreign_column_name,
       rc.update_rule,
       rc.delete_rule
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_schema = kcu.constraint_schema
 AND tc.constraint_name = kcu.constraint_name
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_schema = tc.constraint_schema
 AND ccu.constraint_name = tc.constraint_name
JOIN information_schema.referential_constraints rc
  ON rc.constraint_schema = tc.constraint_schema
 AND rc.constraint_name = tc.constraint_name
WHERE tc.table_schema = 'public'
  AND tc.constraint_type = 'FOREIGN KEY'
ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position;

SELECT '========== CHECK CONSTRAINTS (definitions) ==========' AS section;
SELECT conrelid::regclass AS table_name, conname AS constraint_name, pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE contype = 'c'
  AND connamespace = 'public'::regnamespace
ORDER BY conrelid::regclass::text, conname;

SELECT '========== INDEXES (pg_indexes) ==========' AS section;
SELECT schemaname, tablename, indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
ORDER BY tablename, indexname;

SELECT '========== SEQUENCES ==========' AS section;
SELECT sequence_schema, sequence_name, data_type,
       start_value::text, minimum_value::text, maximum_value::text, increment::text,
       cycle_option
FROM information_schema.sequences
WHERE sequence_schema = 'public'
ORDER BY sequence_name;

SELECT '========== VIEWS ==========' AS section;
SELECT table_name, view_definition
FROM information_schema.views
WHERE table_schema = 'public'
ORDER BY table_name;

SELECT '========== TRIGGERS ==========' AS section;
SELECT event_object_table AS table_name, trigger_name, event_manipulation, action_timing, action_statement
FROM information_schema.triggers
WHERE trigger_schema = 'public'
ORDER BY event_object_table, trigger_name, event_manipulation;

SELECT '========== RLS POLICIES ==========' AS section;
SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check
FROM pg_policies
WHERE schemaname = 'public'
ORDER BY tablename, policyname;

SELECT '========== END ==========' AS section;

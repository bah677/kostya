-- Убрать зашитые примеры статей, если по ним нет ни одной записи.
DELETE FROM ledger_categories c
 WHERE lower(btrim(c.name)) IN ('хостинг', 'реклама', 'подписки / api', 'прочее')
   AND NOT EXISTS (
         SELECT 1
           FROM ledger_entries e
          WHERE e.kind = 'expense'
            AND (
                  e.category_id = c.id
                  OR lower(btrim(coalesce(e.category_name, '')))
                     = lower(btrim(c.name))
                )
       );

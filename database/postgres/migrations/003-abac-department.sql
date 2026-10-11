-- Idempotent. Applied on every start after 002.
-- An empty acl_departments list means every department. A non-empty list
-- still requires a match. A missing session setting still sees no rows.
-- hr_employee no longer carries a department. Rows indexed before that
-- change are rewritten here so a re-embed is not required.
-- 004-least-privilege.sql replaces the SELECT policy below and writes
-- a missing department as "*".

DROP POLICY IF EXISTS chunks_read_by_acl ON kbs.chunks;
CREATE POLICY chunks_read_by_acl ON kbs.chunks
    FOR SELECT
    TO gateway_ro
    USING (
        coalesce(current_setting('kbs.groups', true), '') <> ''
        AND coalesce(current_setting('kbs.clearance', true), '') <> ''
        AND coalesce(current_setting('kbs.department', true), '') <> ''
        AND acl_groups && string_to_array(current_setting('kbs.groups', true), ',')
        AND (
            cardinality(acl_departments) = 0
            OR current_setting('kbs.department', true) = ANY (acl_departments)
        )
        AND min_clearance <= kbs.clearance_rank(current_setting('kbs.clearance', true))
    );

UPDATE kbs.chunks
SET acl_rules = (
    SELECT COALESCE(jsonb_agg(
        CASE
            WHEN rule->>'group' = 'employees' THEN rule - 'department'
            ELSE rule
        END
    ), '[]'::jsonb)
    FROM jsonb_array_elements(acl_rules) AS rule
)
WHERE source_id = 'hr'
  AND acl_rules @> '[{"group": "employees"}]';

UPDATE kbs.chunks
SET acl_departments = CASE
    WHEN EXISTS (
        SELECT 1
        FROM jsonb_array_elements(acl_rules) AS rule
        WHERE coalesce(rule->>'department', '') = ''
    ) THEN '{}'::text[]
    ELSE (
        SELECT COALESCE(array_agg(DISTINCT rule->>'department'), '{}'::text[])
        FROM jsonb_array_elements(acl_rules) AS rule
        WHERE coalesce(rule->>'department', '') <> ''
    )
END
WHERE source_id = 'hr';

GRANT SELECT ON kbs.sync_state TO gateway_ro;

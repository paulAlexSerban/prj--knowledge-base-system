-- Idempotent. Applied on every start after 003.
-- A row is visible only when one acl_rules element matches the session
-- group, department, and clearance together. "*" matches every department.
-- A missing session setting still sees no rows. Coarse columns stay on the
-- table for the index filter. gateway_ro inserts audit rows and cannot read them.
-- A rule that omitted department was an any-department grant. Write that as "*".
-- A rule that omitted clearance was public.

DROP POLICY IF EXISTS chunks_read_by_acl ON kbs.chunks;
CREATE POLICY chunks_read_by_acl ON kbs.chunks
    FOR SELECT
    TO gateway_ro
    USING (
        coalesce(current_setting('kbs.groups', true), '') <> ''
        AND coalesce(current_setting('kbs.clearance', true), '') <> ''
        AND coalesce(current_setting('kbs.department', true), '') <> ''
        AND EXISTS (
            SELECT 1
            FROM jsonb_array_elements(acl_rules) AS rule
            WHERE coalesce(rule->>'group', '') <> ''
              AND rule->>'group' = ANY (
                  string_to_array(current_setting('kbs.groups', true), ',')
              )
              AND (
                  rule->>'department' = '*'
                  OR rule->>'department' = current_setting('kbs.department', true)
              )
              AND kbs.clearance_rank(coalesce(rule->>'clearance', '')) >= 0
              AND kbs.clearance_rank(coalesce(rule->>'clearance', ''))
                  <= kbs.clearance_rank(current_setting('kbs.clearance', true))
        )
    );

UPDATE kbs.chunks
SET acl_rules = (
    SELECT COALESCE(jsonb_agg(
        CASE
            WHEN coalesce(rule->>'group', '') = '' THEN rule
            ELSE jsonb_set(
                jsonb_set(
                    rule,
                    '{department}',
                    to_jsonb(CASE
                        WHEN coalesce(rule->>'department', '') = '' THEN '*'
                        ELSE rule->>'department'
                    END),
                    true
                ),
                '{clearance}',
                to_jsonb(CASE
                    WHEN coalesce(rule->>'clearance', '') = '' THEN 'public'
                    ELSE rule->>'clearance'
                END),
                true
            )
        END
    ), '[]'::jsonb)
    FROM jsonb_array_elements(acl_rules) AS rule
)
WHERE EXISTS (
    SELECT 1
    FROM jsonb_array_elements(acl_rules) AS rule
    WHERE coalesce(rule->>'group', '') <> ''
      AND (
          coalesce(rule->>'department', '') = ''
          OR coalesce(rule->>'clearance', '') = ''
      )
);

UPDATE kbs.chunks
SET acl_departments = CASE
    WHEN EXISTS (
        SELECT 1
        FROM jsonb_array_elements(acl_rules) AS rule
        WHERE rule->>'department' = '*'
           OR coalesce(rule->>'department', '') = ''
    ) THEN '{}'::text[]
    ELSE (
        SELECT COALESCE(
            array_agg(DISTINCT rule->>'department' ORDER BY rule->>'department'),
            '{}'::text[]
        )
        FROM jsonb_array_elements(acl_rules) AS rule
        WHERE coalesce(rule->>'department', '') <> ''
          AND rule->>'department' <> '*'
    )
END
WHERE jsonb_typeof(acl_rules) = 'array'
  AND acl_rules <> '[]'::jsonb;

REVOKE SELECT ON kbs.audit_log FROM gateway_ro;

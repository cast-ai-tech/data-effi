-- =============================================================================
-- Norte - 011 - Memory for the copilot, and a wider (not weaker) reach.
--
-- WHAT "THE AI LEARNS" MEANS HERE, PRECISELY.
--
-- The model is NOT fine-tuned. No weights change, ever. Nothing in this file
-- trains anything. What actually makes later answers better is boring and
-- verifiable: durable facts about THIS operation, stored as rows, injected into
-- every later prompt.
--
--   * The operator says "Servientrega siempre tarda mas en Guayas" -> a row.
--   * The operator marks an answer as wrong and says why            -> a row.
--   * The data itself says the median delivery rate here is 62%     -> a row.
--
-- That is retrieval and context, not learning. Called by its real name, it is
-- also the only version of "it learns" that can be audited, corrected and
-- deleted - which matters more than the word.
--
-- Four tables:
--   raw.ai_conversation  - a thread, so "¿y en Guayas?" has an antecedent
--   raw.ai_message       - every question and every answer, kept
--   raw.ai_memory        - the durable facts described above
--   raw.ai_feedback      - helpful / not helpful, and why
--
-- Depends on: 001-010. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Conversations. A thread belongs to a tenant and (usually) to a country,
-- because "¿y en Guayas?" only means something inside one.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.ai_conversation (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    user_id         uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    country_code    char(2) REFERENCES core.country(code),
    title           text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_message_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ai_conversation_tenant
    ON raw.ai_conversation (tenant_id, last_message_at DESC);

COMMENT ON TABLE raw.ai_conversation IS
    'A copilot thread. Its last messages are replayed as context so follow-up
     questions resolve against what was already asked.';

-- -----------------------------------------------------------------------------
-- Messages. The assistant rows keep the SQL that was actually executed, which
-- is what makes an answer auditable after the fact: the prose can be re-derived
-- from the query, and a wrong answer can be traced to a wrong query.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.ai_message (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES raw.ai_conversation(id) ON DELETE CASCADE,
    tenant_id       uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    role            text NOT NULL CHECK (role IN ('user', 'assistant')),
    content         text NOT NULL,
    sql_executed    text,
    row_count       integer,
    tokens          integer NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ai_message_conversation
    ON raw.ai_message (conversation_id, created_at);
CREATE INDEX IF NOT EXISTS ix_ai_message_tenant
    ON raw.ai_message (tenant_id, created_at DESC);

COMMENT ON COLUMN raw.ai_message.sql_executed IS
    'The vetted SQL this answer was built from. NULL for user turns and for
     answers that never reached the database.';

-- -----------------------------------------------------------------------------
-- Memory. The durable part.
--
-- kind:
--   preference  - how the operator wants things said or measured
--   threshold   - a number that is normal FOR THIS OPERATION
--   context     - a fact about the business the data cannot state
--   decision    - something already decided, so the copilot stops re-proposing it
--   correction  - an answer that was wrong, and what the right shape was
--
-- source tells you who to believe: 'user' beats 'inferred' beats 'system'.
-- confidence lets an inferred fact fade next to something the operator said.
-- expires_at exists because a threshold derived from March is a lie in August.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.ai_memory (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    country_code char(2) REFERENCES core.country(code),
    kind         text NOT NULL CHECK (kind IN
                     ('preference', 'threshold', 'context', 'decision', 'correction')),
    key          text NOT NULL,
    value        text NOT NULL,
    source       text NOT NULL DEFAULT 'user'
                     CHECK (source IN ('user', 'inferred', 'system')),
    confidence   numeric(4, 3) NOT NULL DEFAULT 1.000
                     CHECK (confidence >= 0 AND confidence <= 1),
    created_by   uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz,
    -- NULLS NOT DISTINCT so a tenant-wide fact (country_code IS NULL) has ONE
    -- row and can be upserted. With the default NULLS DISTINCT, every write of
    -- a global fact would insert a duplicate instead of updating.
    CONSTRAINT ux_ai_memory_key UNIQUE NULLS NOT DISTINCT (tenant_id, country_code, key)
);
CREATE INDEX IF NOT EXISTS ix_ai_memory_recall
    ON raw.ai_memory (tenant_id, country_code, confidence DESC);

COMMENT ON TABLE raw.ai_memory IS
    'Durable facts about this operation, injected into every later prompt. This
     is what "the AI learns" means in Norte: rows, not weights. Nothing here
     trains a model - it is retrieval, and it can be read, edited and deleted.';
COMMENT ON COLUMN raw.ai_memory.confidence IS
    'How much to trust the fact. Facts the operator stated are 1.0; facts
     derived from the data carry the confidence the derivation deserved.';

DROP TRIGGER IF EXISTS tr_ai_memory_touch ON raw.ai_memory;
CREATE TRIGGER tr_ai_memory_touch BEFORE UPDATE ON raw.ai_memory
    FOR EACH ROW EXECUTE FUNCTION core.touch_updated_at();

-- -----------------------------------------------------------------------------
-- Feedback. One verdict per message: re-rating replaces, it does not stack.
-- An unhelpful verdict WITH a comment is what becomes a 'correction' memory.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.ai_feedback (
    message_id uuid PRIMARY KEY REFERENCES raw.ai_message(id) ON DELETE CASCADE,
    tenant_id  uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    helpful    boolean NOT NULL,
    comment    text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ai_feedback_tenant
    ON raw.ai_feedback (tenant_id, created_at DESC);

-- -----------------------------------------------------------------------------
-- Row-level security, the exact shape migration 007 applies everywhere else.
-- ENABLE + FORCE, one policy, fail closed with no GUC set.
-- -----------------------------------------------------------------------------
DO $do$
DECLARE
    v_table text;
    v_tables text[] := ARRAY[
        'raw.ai_conversation',
        'raw.ai_message',
        'raw.ai_memory',
        'raw.ai_feedback'
    ];
BEGIN
    FOREACH v_table IN ARRAY v_tables LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', v_table);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', v_table);

        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %s', v_table);
        EXECUTE format($sql$
            CREATE POLICY tenant_isolation ON %s
            USING (tenant_id = core.current_tenant_id() OR core.is_service_context())
            WITH CHECK (tenant_id = core.current_tenant_id() OR core.is_service_context())
        $sql$, v_table);
    END LOOP;
END;
$do$;

-- -----------------------------------------------------------------------------
-- The cache learned a fourth feature: recommendations.
-- -----------------------------------------------------------------------------
ALTER TABLE raw.ai_cache DROP CONSTRAINT IF EXISTS ai_cache_feature_check;
ALTER TABLE raw.ai_cache ADD CONSTRAINT ai_cache_feature_check
    CHECK (feature IN ('brief', 'alerts', 'ask', 'recommendations'));

-- =============================================================================
-- Reading memory: a mart view, for the application only.
-- =============================================================================
CREATE OR REPLACE VIEW mart.v_ai_memory AS
SELECT
    m.tenant_id,
    m.id            AS memory_id,
    m.country_code,
    m.kind,
    m.key,
    m.value,
    m.source,
    m.confidence,
    m.created_at,
    m.updated_at,
    m.expires_at,
    (m.expires_at IS NOT NULL AND m.expires_at <= now()) AS is_expired
FROM raw.ai_memory m
WHERE m.tenant_id = core.current_tenant_id();

COMMENT ON VIEW mart.v_ai_memory IS
    'What the copilot remembers about this operation. Application only: the
     NL->SQL role must never read it, because a generated query that can read
     the memory can also be steered by whatever was written into it.';

GRANT SELECT ON mart.v_ai_memory TO norte_app;

-- =============================================================================
-- GRANTS - and one hole closed.
--
-- Migration 007 sets ALTER DEFAULT PRIVILEGES ... GRANT SELECT ON TABLES TO
-- norte_readonly for the whole mart schema. That is convenient and it is also
-- why migration 010's comment ("deliberately NOT granted to norte_readonly")
-- was not true in practice: mart.v_source_row_archive was created afterwards by
-- the same role and picked the grant up automatically.
--
-- The validator's allow-list (layer 3) was still refusing that view, so nothing
-- leaked. But layer 1 is supposed to hold on its own. Revoked explicitly here,
-- for both the archive and the new memory view.
-- =============================================================================
REVOKE ALL ON mart.v_source_row_archive FROM norte_readonly;
REVOKE ALL ON mart.v_ai_memory          FROM norte_readonly;

-- Business aggregates from 008 and 009. "Any data in the platform" means every
-- one of these; it does not mean a single customer row.
GRANT SELECT ON
    mart.v_problem_rate,
    mart.v_cash_cycle,
    mart.v_dropshipping_margin,
    mart.v_fulfillment_sla,
    mart.v_office_rescue,
    mart.v_freight_analysis,
    mart.v_product_catalogue
TO norte_readonly;

GRANT SELECT ON
    mart.v_problem_rate,
    mart.v_cash_cycle,
    mart.v_dropshipping_margin,
    mart.v_fulfillment_sla,
    mart.v_office_rescue,
    mart.v_freight_analysis,
    mart.v_product_catalogue
TO norte_app;

GRANT SELECT, INSERT, UPDATE, DELETE ON
    raw.ai_conversation, raw.ai_message, raw.ai_memory, raw.ai_feedback
TO norte_app;

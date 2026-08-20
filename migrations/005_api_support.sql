-- =============================================================================
-- Norte - 005 - Tables the API needs: upload jobs and rate limiting.
--
-- Both live in the database rather than in process memory, so a restart does not
-- lose an in-flight upload and a second API replica shares the same rate limit.
--
-- Depends on: 001. Idempotent.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Upload jobs. One row per file the user dropped, tracked from queued to done.
-- The frontend polls these; the ingestion worker updates them.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.upload_job (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     uuid NOT NULL REFERENCES core.tenant(id) ON DELETE CASCADE,
    connection_id uuid NOT NULL REFERENCES core.connection(id) ON DELETE CASCADE,
    uploaded_by   uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    filename      text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('shipments', 'movements', 'ads', 'cs')),
    size_bytes    bigint NOT NULL,
    content_hash  char(64),
    storage_path  text NOT NULL,
    status        text NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued', 'processing', 'done', 'failed', 'duplicate')),
    batch_id      uuid REFERENCES raw.load_batch(id) ON DELETE SET NULL,
    error         text,
    queued_at     timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    finished_at   timestamptz
);
CREATE INDEX IF NOT EXISTS ix_upload_job_tenant
    ON raw.upload_job (tenant_id, queued_at DESC);
CREATE INDEX IF NOT EXISTS ix_upload_job_pending
    ON raw.upload_job (status) WHERE status IN ('queued', 'processing');

-- -----------------------------------------------------------------------------
-- Rate limiting. A fixed window per (scope, key). Cheap, honest, and shared
-- across API replicas - an in-memory counter would multiply the limit by the
-- number of processes.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.rate_limit_hit (
    scope        text NOT NULL,
    subject      text NOT NULL,      -- client IP or user id
    window_start timestamptz NOT NULL,
    hits         integer NOT NULL DEFAULT 1,
    PRIMARY KEY (scope, subject, window_start)
);
CREATE INDEX IF NOT EXISTS ix_rate_limit_window ON raw.rate_limit_hit (window_start);

CREATE OR REPLACE FUNCTION raw.register_rate_limit_hit(
    p_scope text,
    p_subject text,
    p_window_seconds integer,
    p_limit integer
)
RETURNS boolean
LANGUAGE plpgsql
AS $fn$
DECLARE
    v_window timestamptz;
    v_hits   integer;
BEGIN
    v_window := to_timestamp(
        floor(extract(EPOCH FROM clock_timestamp()) / p_window_seconds) * p_window_seconds
    );

    INSERT INTO raw.rate_limit_hit (scope, subject, window_start, hits)
    VALUES (p_scope, p_subject, v_window, 1)
    ON CONFLICT (scope, subject, window_start)
    DO UPDATE SET hits = raw.rate_limit_hit.hits + 1
    RETURNING hits INTO v_hits;

    -- Opportunistic cleanup so the table never grows without bound.
    IF random() < 0.01 THEN
        DELETE FROM raw.rate_limit_hit WHERE window_start < now() - interval '1 day';
    END IF;

    RETURN v_hits <= p_limit;
END;
$fn$;

COMMENT ON FUNCTION raw.register_rate_limit_hit IS
    'Returns true when the request is within the limit, false when it must be rejected.';

-- -----------------------------------------------------------------------------
-- Login attempt audit. Failures are what tell you an account is under attack.
-- Never stores the attempted password, not even hashed.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw.auth_event (
    id         bigserial PRIMARY KEY,
    email      text,
    tenant_id  uuid REFERENCES core.tenant(id) ON DELETE CASCADE,
    user_id    uuid REFERENCES core.app_user(id) ON DELETE SET NULL,
    event      text NOT NULL CHECK (event IN
               ('login_ok', 'login_failed', 'refresh', 'logout', 'register')),
    ip_address inet,
    user_agent text,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_auth_event_email ON raw.auth_event (email, created_at DESC);

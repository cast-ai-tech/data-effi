-- =============================================================================
-- Data Effi - 037 - The audit trail learns about password changes.
--
-- `raw.auth_event.event` has been a closed CHECK since migration 005 - login_ok,
-- login_failed, refresh, logout, register - which is the right shape for an audit
-- column: a typo becomes an error instead of a category nobody ever queries.
--
-- The account panel adds two events to that vocabulary:
--
--   password_changed          somebody changed their own password. Worth keeping
--                             because it is also the moment every session of that
--                             user was revoked, and support will be asked why.
--   password_change_refused   the current password did not match. A handful of
--                             these against one account, from one address, is
--                             somebody working through a list.
--
-- Neither ever records a password, hashed or otherwise - the table stores the
-- FACT, like raw.pii_access does for contact data.
--
-- Depends on: 001-036. Idempotent.
-- =============================================================================

DO $do$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'raw.auth_event'::regclass
          AND conname = 'auth_event_event_check'
    ) THEN
        ALTER TABLE raw.auth_event DROP CONSTRAINT auth_event_event_check;
    END IF;

    ALTER TABLE raw.auth_event
        ADD CONSTRAINT auth_event_event_check
        CHECK (event IN (
            'login_ok',
            'login_failed',
            'refresh',
            'logout',
            'register',
            'password_changed',
            'password_change_refused'
        ));
END;
$do$;

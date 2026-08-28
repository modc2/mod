-- Add auth_failure_reason to events.
--
-- The client has sent `auth_failure_reason` on `auth_failed` onboarding_step
-- events since login diagnostics landed (classify_auth_failure_message), but
-- the worker never stored it: the column filter silently dropped the field, so
-- auth failures were undiagnosable from the dashboard. Additive and nullable;
-- existing rows are untouched.
ALTER TABLE events ADD COLUMN auth_failure_reason TEXT;

-- Migration 008: Webhooks and Cycle Count Schedules

-- ── Outbound webhooks ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS webhooks (
    webhook_id      uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text        NOT NULL,
    url             text        NOT NULL,
    event_types     text[]      NOT NULL DEFAULT '{}',
                                -- 'low_stock' | 'low_battery' | 'order_confirmed'
                                -- | 'order_completed' | 'supplier_received'
    secret          text,       -- HMAC-SHA256 signing secret (optional)
    is_active       boolean     NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ── Cycle count schedules ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cycle_count_schedules (
    schedule_id     uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    component_id    uuid        REFERENCES components(component_id),
    assembly_id     uuid        REFERENCES assemblies(assembly_id),
    scheduled_date  date        NOT NULL,
    status          text        NOT NULL DEFAULT 'pending',
                                -- 'pending' | 'completed' | 'skipped'
    system_count    numeric,    -- count at time of scheduling
    confirmed_count numeric,    -- count entered by operator
    discrepancy     numeric     GENERATED ALWAYS AS (confirmed_count - system_count) STORED,
    notes           text,
    completed_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (component_id IS NOT NULL OR assembly_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_cycle_count_date_status
    ON cycle_count_schedules(scheduled_date, status);

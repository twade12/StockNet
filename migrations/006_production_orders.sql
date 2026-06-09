-- Migration 006: Production Orders, Reservations
-- Adds support for production jobs/POs with component reservations

-- ── Component enhancements ──────────────────────────────────────────
ALTER TABLE components ADD COLUMN IF NOT EXISTS lead_time_days  integer NOT NULL DEFAULT 7;
ALTER TABLE components ADD COLUMN IF NOT EXISTS min_order_qty   integer NOT NULL DEFAULT 1;

-- ── Inventory reservations ───────────────────────────────────────────
-- reserved_count tracks stock committed to confirmed production orders
-- available = current_count - reserved_count
ALTER TABLE inventory_items ADD COLUMN IF NOT EXISTS reserved_count numeric NOT NULL DEFAULT 0;

-- ── Production orders ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS production_orders (
    order_id        uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number    text        NOT NULL UNIQUE,
    order_type      text        NOT NULL DEFAULT 'production',
                                -- 'production' | 'quotation' | 'sales_order'
    status          text        NOT NULL DEFAULT 'draft',
                                -- 'draft' | 'confirmed' | 'in_progress' | 'completed' | 'cancelled'
    customer_ref    text,
    notes           text,
    due_date        date,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    confirmed_at    timestamptz,
    completed_at    timestamptz
);

-- ── Order line items ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS production_order_lines (
    line_id         uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        uuid        NOT NULL REFERENCES production_orders(order_id) ON DELETE CASCADE,
    assembly_id     uuid        NOT NULL REFERENCES assemblies(assembly_id),
    qty_ordered     numeric     NOT NULL CHECK (qty_ordered > 0),
    qty_completed   numeric     NOT NULL DEFAULT 0,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (order_id, assembly_id)
);

-- ── Component reservations (one row per component per order) ─────────
CREATE TABLE IF NOT EXISTS component_reservations (
    reservation_id  uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        uuid        NOT NULL REFERENCES production_orders(order_id) ON DELETE CASCADE,
    component_id    uuid        NOT NULL REFERENCES components(component_id),
    qty_reserved    numeric     NOT NULL,   -- locked on confirm
    qty_consumed    numeric     NOT NULL DEFAULT 0,  -- filled on complete
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (order_id, component_id)
);

CREATE INDEX IF NOT EXISTS idx_comp_res_component ON component_reservations(component_id);
CREATE INDEX IF NOT EXISTS idx_comp_res_order     ON component_reservations(order_id);
CREATE INDEX IF NOT EXISTS idx_prod_orders_status ON production_orders(status);

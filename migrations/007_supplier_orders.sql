-- Migration 007: Supplier / Purchase Orders
-- Tracks inbound material orders and receiving against inventory

CREATE TABLE IF NOT EXISTS supplier_orders (
    supplier_order_id   uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    order_number        text        UNIQUE,
    supplier_name       text,
    status              text        NOT NULL DEFAULT 'draft',
                                    -- 'draft' | 'ordered' | 'partial' | 'received' | 'cancelled'
    notes               text,
    ordered_at          timestamptz,
    expected_delivery   date,
    received_at         timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS supplier_order_lines (
    line_id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_order_id   uuid        NOT NULL REFERENCES supplier_orders(supplier_order_id) ON DELETE CASCADE,
    component_id        uuid        NOT NULL REFERENCES components(component_id),
    qty_ordered         numeric     NOT NULL CHECK (qty_ordered > 0),
    qty_received        numeric     NOT NULL DEFAULT 0,
    unit_cost           numeric,
    notes               text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_supplier_order_lines_component ON supplier_order_lines(component_id);
CREATE INDEX IF NOT EXISTS idx_supplier_orders_status ON supplier_orders(status);

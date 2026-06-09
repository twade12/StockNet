-- Migration: Fix upsert_inventory_item_from_counter_event ON CONFLICT clauses
-- The partial unique indexes on inventory_items require WHERE ... IS NOT NULL
-- in the ON CONFLICT clause. Without this, PostgreSQL cannot match the constraint
-- and the function fails with "no unique or exclusion constraint matching".

CREATE OR REPLACE FUNCTION upsert_inventory_item_from_counter_event(
    p_device_id uuid,
    p_assignment_type text,
    p_component_id uuid,
    p_assembly_id uuid,
    p_count_value numeric
)
RETURNS void AS $$
BEGIN
    IF p_assignment_type = 'component' AND p_component_id IS NOT NULL THEN
        INSERT INTO inventory_items (
            item_type, component_id, current_count, updated_from_device_id, updated_at
        )
        VALUES (
            'component', p_component_id, GREATEST(p_count_value, 0), p_device_id, now()
        )
        ON CONFLICT (component_id) WHERE component_id IS NOT NULL
        DO UPDATE SET
            current_count = EXCLUDED.current_count,
            updated_from_device_id = EXCLUDED.updated_from_device_id,
            updated_at = now();

    ELSIF p_assignment_type = 'assembly' AND p_assembly_id IS NOT NULL THEN
        INSERT INTO inventory_items (
            item_type, assembly_id, current_count, updated_from_device_id, updated_at
        )
        VALUES (
            'assembly', p_assembly_id, GREATEST(p_count_value, 0), p_device_id, now()
        )
        ON CONFLICT (assembly_id) WHERE assembly_id IS NOT NULL
        DO UPDATE SET
            current_count = EXCLUDED.current_count,
            updated_from_device_id = EXCLUDED.updated_from_device_id,
            updated_at = now();
    END IF;
END;
$$ LANGUAGE plpgsql;

-- Also fix the double-precision wrapper overload
CREATE OR REPLACE FUNCTION upsert_inventory_item_from_counter_event(
    p_device_id uuid,
    p_assignment_type text,
    p_component_id uuid,
    p_assembly_id uuid,
    p_count_value double precision
)
RETURNS void AS $$
BEGIN
    PERFORM upsert_inventory_item_from_counter_event(
        p_device_id,
        p_assignment_type,
        p_component_id,
        p_assembly_id,
        p_count_value::numeric
    );
END;
$$ LANGUAGE plpgsql;

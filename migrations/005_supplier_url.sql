-- Migration: Add supplier_url column to components table
-- Stores a link to a supplier/distributor page (e.g. Digikey, Mouser)
-- for quick access to datasheets and ordering

ALTER TABLE components ADD COLUMN IF NOT EXISTS supplier_url text NULL;

COMMENT ON COLUMN components.supplier_url IS 'URL to supplier product page (e.g. Digikey, Mouser) for datasheets and ordering';

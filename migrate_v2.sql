-- migrate_v3.sql
-- Canonicalizes phone numbers to the 10-digit national form the API now
-- stores, so numbers entered earlier as "+919743476555" / "09743476555" /
-- "91 97434 76555" still match the same person after the SMS update.
--
-- How to run it:
--   docker compose exec -T db psql -U admin -d land_records_db < migrate_v3.sql
-- (run from your project folder, while your containers are up)
--
-- Only run this if DEFAULT_COUNTRY_CODE=91. It leaves genuine international
-- numbers (anything not 10 digits after stripping a 91/0 prefix) alone.

BEGIN;

-- Helper: strip non-digits, then drop a leading 91 or 0 when what remains
-- is exactly 10 digits.
CREATE OR REPLACE FUNCTION canon_phone(raw TEXT) RETURNS TEXT AS $$
DECLARE d TEXT;
BEGIN
    IF raw IS NULL THEN RETURN NULL; END IF;
    d := regexp_replace(raw, '\D', '', 'g');
    IF length(d) = 12 AND left(d, 2) = '91' THEN d := right(d, 10); END IF;
    IF length(d) = 11 AND left(d, 1) = '0'  THEN d := right(d, 10); END IF;
    IF length(d) = 10 THEN RETURN d; END IF;
    RETURN raw;  -- leave anything else untouched
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- 1. users -- skip any row whose canonical form would collide with an
--    existing account, so the UNIQUE constraint can't blow up mid-migration.
UPDATE users u
SET phone = canon_phone(u.phone)
WHERE canon_phone(u.phone) <> u.phone
  AND NOT EXISTS (
      SELECT 1 FROM users x WHERE x.phone = canon_phone(u.phone) AND x.id <> u.id
  );

-- 2. Parcel ownership -- this is what /api/my-records matches against.
UPDATE cadastral_parcels
SET owner_phone = canon_phone(owner_phone)
WHERE owner_phone IS NOT NULL AND canon_phone(owner_phone) <> owner_phone;

-- 3. Pending OTPs -- harmless either way, they expire in 5 minutes.
UPDATE otp_codes
SET phone = canon_phone(phone)
WHERE canon_phone(phone) <> phone;

-- 4. audit_ledger is deliberately NOT rewritten. It is the immutable record
--    of what was committed at the time; rewriting it would defeat the point
--    of the hash chain. Historical entries keep whatever format was used.

-- Make sure the seeded staff account is present and canonical.
INSERT INTO users (phone, name, is_staff)
VALUES ('9743476555', 'Registry Staff Admin', TRUE)
ON CONFLICT (phone) DO UPDATE SET is_staff = TRUE;

DROP FUNCTION canon_phone(TEXT);

COMMIT;

-- Check the result:
--   SELECT phone, name, is_staff, is_policymaker FROM users ORDER BY id;
-- Enable PostGIS spatial engine
CREATE EXTENSION IF NOT EXISTS postgis;

-- 1. Citizens / staff who can log in
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(15) UNIQUE NOT NULL,
    name VARCHAR(150) NOT NULL,
    is_staff BOOLEAN DEFAULT FALSE,
    -- Read-only access to aggregate analytics only -- never sees owner names,
    -- phone numbers, or documents. Kept separate from is_staff on purpose:
    -- a policymaker/oversight user should never need PII access to see trends.
    is_policymaker BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. One-time login codes (hashed, short-lived)
CREATE TABLE IF NOT EXISTS otp_codes (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(15) NOT NULL,
    code_hash VARCHAR(64) NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    consumed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 3. Original scanned documents, kept in the database
CREATE TABLE IF NOT EXISTS documents (
    id SERIAL PRIMARY KEY,
    survey_no VARCHAR(50),
    file_name VARCHAR(255),
    mime_type VARCHAR(100),
    file_bytes BYTEA NOT NULL,
    image_hash VARCHAR(64),
    authenticity_flags JSONB,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_documents_image_hash ON documents (image_hash);

-- 4. Low-confidence / incomplete extractions waiting on a human
CREATE TABLE IF NOT EXISTS review_queue (
    id SERIAL PRIMARY KEY,
    document_id INTEGER REFERENCES documents(id),
    extracted_json JSONB NOT NULL,
    confidence NUMERIC(5,4),
    status VARCHAR(20) DEFAULT 'PENDING', -- PENDING, APPROVED, REJECTED
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 5. Master Cadastral Parcels Table
CREATE TABLE IF NOT EXISTS cadastral_parcels (
    id SERIAL PRIMARY KEY,
    survey_no VARCHAR(50) UNIQUE NOT NULL,
    owner_name VARCHAR(150) NOT NULL,
    owner_phone VARCHAR(15),
    area_hectares NUMERIC(10, 4) NOT NULL,
    parent_plot VARCHAR(50),
    document_id INTEGER REFERENCES documents(id),
    geom GEOMETRY(Polygon, 4326),
    -- DILRMP/ULPIN alignment fields. ulpin here is a LOCALLY GENERATED,
    -- ULPIN-FORMATTED placeholder derived from this parcel's own centroid
    -- coordinates -- it is not an officially issued Bhu-Aadhaar. A real
    -- ULPIN can only be issued by NIC's BhuNaksha system once a
    -- geo-referenced shapefile is surveyed and submitted through the state's
    -- DILRMP-integrated Revenue Department. Keeping the same 14-character
    -- alphanumeric shape now means less rework if/when this system is ever
    -- connected to that pipeline.
    ulpin VARCHAR(14) UNIQUE,
    state_code VARCHAR(10) DEFAULT 'UNK',
    district_code VARCHAR(10) DEFAULT 'UNK',
    tehsil_code VARCHAR(10) DEFAULT 'UNK',
    village_code VARCHAR(10) DEFAULT 'UNK',
    -- Dispute lifecycle, surfaced in the policy analytics dashboard.
    dispute_status VARCHAR(20) DEFAULT 'NONE', -- NONE, DISPUTED, RESOLVED
    disputed_at TIMESTAMP,
    resolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cadastral_parcels_dispute_status ON cadastral_parcels (dispute_status);

-- 6. Immutable Cryptographic Audit Ledger
-- action values: GENESIS, REGISTER (first commit of a survey number),
-- MUTATION (a later commit that changes an existing parcel -- e.g. an
-- ownership transfer), DISPUTE_FLAGGED, DISPUTE_RESOLVED. The analytics
-- dashboard's mutation-frequency and disputed-parcels trends are both
-- computed straight off this table's timestamps, so it doubles as the
-- system's event log -- nothing here is ever updated or deleted.
CREATE TABLE IF NOT EXISTS audit_ledger (
    block_index SERIAL PRIMARY KEY,
    survey_no VARCHAR(50) NOT NULL,
    owner_name VARCHAR(150) NOT NULL,
    owner_phone VARCHAR(15),
    area_hectares NUMERIC(10, 4) NOT NULL,
    action VARCHAR(20) DEFAULT 'REGISTER',
    block_hash VARCHAR(64) NOT NULL,
    prev_hash VARCHAR(64) NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_ledger_action_timestamp ON audit_ledger (action, timestamp);

-- Seed genesis block
INSERT INTO audit_ledger (survey_no, owner_name, owner_phone, area_hectares, action, block_hash, prev_hash)
VALUES ('GENESIS', 'SYSTEM_ROOT', NULL, 0.0000, 'GENESIS', repeat('0', 64), '0')
ON CONFLICT DO NOTHING;

-- Seed one staff account so someone can log in and start ingesting documents.
-- Log in with this phone number + any name; in OTP_DEBUG_MODE the OTP is returned
-- directly by the API. Change/remove this before going anywhere near production.
INSERT INTO users (phone, name, is_staff)
VALUES ('9743476555', 'Registry Staff Admin', TRUE)
ON CONFLICT (phone) DO NOTHING;

-- Seed one read-only policymaker/oversight account. This role sees only the
-- aggregate Policy Analytics dashboard -- never owner names, phone numbers,
-- or documents. Log in with this phone number + any name.
INSERT INTO users (phone, name, is_policymaker)
VALUES ('8888888888', 'Policy Analytics Viewer', TRUE)
ON CONFLICT (phone) DO NOTHING;
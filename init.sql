-- Enable PostGIS spatial engine
CREATE EXTENSION IF NOT EXISTS postgis;

-- 1. Citizens / staff who can log in
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(15) UNIQUE NOT NULL,
    name VARCHAR(150) NOT NULL,
    is_staff BOOLEAN DEFAULT FALSE,
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

-- 4b. Documents citizens uploaded themselves, awaiting staff approval
CREATE TABLE IF NOT EXISTS citizen_submissions (
    id SERIAL PRIMARY KEY,
    submitter_phone VARCHAR(15) NOT NULL,
    submitter_name VARCHAR(150) NOT NULL,
    document_id INTEGER REFERENCES documents(id),
    claimed_survey_no VARCHAR(50),
    note TEXT,
    status VARCHAR(20) DEFAULT 'PENDING', -- PENDING, APPROVED, REJECTED
    rejection_reason TEXT,
    reviewed_by VARCHAR(15),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP
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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. Immutable Cryptographic Audit Ledger
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

-- Seed genesis block
INSERT INTO audit_ledger (survey_no, owner_name, owner_phone, area_hectares, action, block_hash, prev_hash)
VALUES ('GENESIS', 'SYSTEM_ROOT', NULL, 0.0000, 'GENESIS', repeat('0', 64), '0')
ON CONFLICT DO NOTHING;

-- Seed one staff account so someone can log in and start ingesting documents.
-- Log in with this phone number + any name; in OTP_DEBUG_MODE the OTP is returned
-- directly by the API. Change/remove this before going anywhere near production.
INSERT INTO users (phone, name, is_staff)
VALUES ('9999999999', 'Registry Staff Admin', TRUE)
ON CONFLICT (phone) DO NOTHING;
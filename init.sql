-- Enable PostGIS spatial engine
CREATE EXTENSION IF NOT EXISTS postgis;

-- 1. Citizens / staff who can log in
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(15) UNIQUE NOT NULL,
    name VARCHAR(150) NOT NULL,
    is_staff BOOLEAN DEFAULT FALSE,
    is_admin BOOLEAN DEFAULT FALSE,
    role VARCHAR(100),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;
-- NULL means this account hasn't set a password yet -- true for every
-- account created before the password-auth update (including seeded staff).
-- The login flow detects this and asks them to set one, without ever
-- re-asking for their name.
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
-- Presence: is_online is set true on login / any authenticated request,
-- false on logout. last_seen_at lets the UI treat a stale is_online=true
-- (tab closed without logging out) as offline again after a few minutes.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_online BOOLEAN DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP;

-- 2. One row per login; logout_at is filled in on logout (or stays NULL if
--    the session just went stale). This is the staff attendance history.
CREATE TABLE IF NOT EXISTS staff_sessions (
    id SERIAL PRIMARY KEY,
    phone VARCHAR(15) NOT NULL,
    login_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    logout_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_staff_sessions_phone_login ON staff_sessions (phone, login_at);

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

-- 5. Documents citizens uploaded themselves, awaiting staff approval
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
-- Which staff member this submission was auto-assigned to for review. NULL
-- means unassigned (only happens if no staff exist yet, or a staff member
-- lost access while it was still pending).
ALTER TABLE citizen_submissions ADD COLUMN IF NOT EXISTS assigned_to VARCHAR(15);
CREATE INDEX IF NOT EXISTS idx_submissions_assigned_to ON citizen_submissions (assigned_to);

-- 6. Round-robin cursor: remembers who was assigned last so new submissions
--    cycle evenly through active staff, in a stable order.
CREATE TABLE IF NOT EXISTS assignment_state (
    queue_name VARCHAR(50) PRIMARY KEY,
    last_staff_phone VARCHAR(15)
);

-- 7. Master Cadastral Parcels Table
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

-- 8. Immutable Cryptographic Audit Ledger
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

-- Seed staff/admin accounts. None of these have a password yet -- the first
-- time each logs in, the app will ask them to set one (name stays fixed,
-- exactly as already seeded here).
INSERT INTO users (phone, name, is_staff, is_admin, role)
VALUES ('9999999999', 'Registry Staff Admin', TRUE, FALSE, 'Registry Staff')
ON CONFLICT (phone) DO UPDATE SET is_admin = FALSE, role = COALESCE(users.role, EXCLUDED.role);

INSERT INTO users (phone, name, is_staff, is_admin, role)
VALUES ('9743476555', 'Charan', TRUE, TRUE, 'Registry Admin')
ON CONFLICT (phone) DO UPDATE SET is_staff = TRUE, is_admin = TRUE, role = COALESCE(users.role, EXCLUDED.role);

INSERT INTO users (phone, name, is_staff, is_admin, role)
VALUES ('7670885520', 'Bhuvana Kruthi', TRUE, TRUE, 'Approval Manager')
ON CONFLICT (phone) DO UPDATE SET is_staff = TRUE, is_admin = TRUE, role = 'Approval Manager', name = EXCLUDED.name;
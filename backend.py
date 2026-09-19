import os
import re
import json
import hashlib
import secrets
import base64
import io
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Depends, Form
from fastapi.responses import Response
from pydantic import BaseModel
from rapidfuzz import fuzz
from PIL import Image
import jwt
import bcrypt

app = FastAPI(title="Land Digitization Engine")

DB_URL = os.environ.get("DATABASE_URL", "postgresql://admin:adminpassword@localhost:5432/land_records_db")
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    # Fine for local dev (tokens just won't survive a restart); set a real
    # JWT_SECRET env var before deploying anywhere real.
    JWT_SECRET = secrets.token_hex(32)
JWT_ALGO = "HS256"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"

CONFIDENCE_REVIEW_THRESHOLD = 0.85
ONLINE_THRESHOLD_MINUTES = 5  # no heartbeat for longer than this -> shown offline even if never explicitly logged out

PHONE_RE = re.compile(r"^\+?\d{10,15}$")


def get_db_connection():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)


def run_migrations():
    """
    Idempotent schema setup, run automatically every time the API starts.
    This means schema changes apply on deploy without needing direct database
    shell access (which Render's free Postgres tier doesn't provide). Safe to
    run repeatedly -- every statement is IF NOT EXISTS / ON CONFLICT DO NOTHING.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    phone VARCHAR(15) UNIQUE NOT NULL,
                    name VARCHAR(150) NOT NULL,
                    is_staff BOOLEAN DEFAULT FALSE,
                    is_admin BOOLEAN DEFAULT FALSE,
                    role VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(100);")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE;")
            # NULL means this account hasn't set a password yet -- true for
            # every account created before this update (including seeded
            # staff). The login flow detects this and asks them to set one,
            # without ever re-asking for their name.
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;")
            # Presence: is_online is set true on login / any authenticated
            # request, false on logout. last_seen_at lets the UI treat a
            # stale is_online=true (tab closed without logging out) as
            # offline again after ONLINE_THRESHOLD_MINUTES.
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_online BOOLEAN DEFAULT FALSE;")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP;")
            # Account identity fields: every account gets a unique username;
            # email is optional and can be linked later from Profile Settings.
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username VARCHAR(50);")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(254);")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_photo BYTEA;")
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_photo_mime VARCHAR(100);")
            # Backfill usernames for accounts created before username support.
            # The id suffix guarantees uniqueness even when two users share a
            # name and the same last four phone digits.
            cur.execute("""
                UPDATE users
                SET username = LOWER(REGEXP_REPLACE(COALESCE(NULLIF(name, ''), 'user') || '_' || id, '[^a-zA-Z0-9_]+', '_', 'g'))
                WHERE username IS NULL;
            """)
            # If an older partial deployment already populated duplicate
            # usernames, keep the first one and suffix the others with id.
            cur.execute("""
                WITH ranked AS (
                    SELECT id, ROW_NUMBER() OVER (PARTITION BY LOWER(username) ORDER BY id) AS rn
                    FROM users
                    WHERE username IS NOT NULL
                )
                UPDATE users u
                SET username = LOWER(u.username) || '_' || u.id
                FROM ranked r
                WHERE u.id = r.id AND r.rn > 1;
            """)
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique ON users (LOWER(username)) WHERE username IS NOT NULL;")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique ON users (LOWER(email)) WHERE email IS NOT NULL;")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS password_reset_requests (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    phone VARCHAR(15) NOT NULL,
                    username VARCHAR(50) NOT NULL,
                    reason TEXT,
                    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
                    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_by VARCHAR(15),
                    reviewed_at TIMESTAMP,
                    rejection_reason TEXT,
                    reset_token_hash VARCHAR(64),
                    reset_token_expires_at TIMESTAMP,
                    token_used_at TIMESTAMP
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_requests_status ON password_reset_requests (status, requested_at);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_requests_user ON password_reset_requests (user_id, requested_at);")

            # One row per login; logout_at is filled in when they log out
            # (or stays NULL if the session just went stale). This is the
            # attendance history -- who logged in/out and when.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS staff_sessions (
                    id SERIAL PRIMARY KEY,
                    phone VARCHAR(15) NOT NULL,
                    login_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    logout_at TIMESTAMP
                );
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_staff_sessions_phone_login ON staff_sessions (phone, login_at);")

            cur.execute("""
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
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_documents_image_hash ON documents (image_hash);")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS review_queue (
                    id SERIAL PRIMARY KEY,
                    document_id INTEGER REFERENCES documents(id),
                    extracted_json JSONB NOT NULL,
                    confidence NUMERIC(5,4),
                    status VARCHAR(20) DEFAULT 'PENDING',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS citizen_submissions (
                    id SERIAL PRIMARY KEY,
                    submitter_phone VARCHAR(15) NOT NULL,
                    submitter_name VARCHAR(150) NOT NULL,
                    document_id INTEGER REFERENCES documents(id),
                    claimed_survey_no VARCHAR(50),
                    note TEXT,
                    status VARCHAR(20) DEFAULT 'PENDING',
                    rejection_reason TEXT,
                    reviewed_by VARCHAR(15),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMP
                );
            """)
            # Which staff member this submission was auto-assigned to for
            # review. NULL means unassigned (only happens if no staff exist
            # yet, or a staff member lost access while it was still pending).
            cur.execute("ALTER TABLE citizen_submissions ADD COLUMN IF NOT EXISTS assigned_to VARCHAR(15);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_submissions_assigned_to ON citizen_submissions (assigned_to);")

            # Round-robin cursor: remembers who was assigned last so new
            # submissions cycle evenly through active staff, in a stable
            # order, rather than piling on whoever happens to be first.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS assignment_state (
                    queue_name VARCHAR(50) PRIMARY KEY,
                    last_staff_phone VARCHAR(15)
                );
            """)

            cur.execute("""
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
            """)

            cur.execute("""
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
            """)

            cur.execute("""
                INSERT INTO audit_ledger (survey_no, owner_name, owner_phone, area_hectares, action, block_hash, prev_hash)
                VALUES ('GENESIS', 'SYSTEM_ROOT', NULL, 0.0000, 'GENESIS', repeat('0', 64), '0')
                ON CONFLICT DO NOTHING;
            """)

            cur.execute("""
                INSERT INTO users (phone, name, username, is_staff, is_admin, role)
                VALUES ('9999999999', 'Registry Staff Admin', 'registry_staff_admin', TRUE, FALSE, 'Registry Staff')
                ON CONFLICT (phone) DO UPDATE SET is_admin = FALSE, role = COALESCE(users.role, EXCLUDED.role), username = COALESCE(users.username, EXCLUDED.username);
            """)

            cur.execute("""
                INSERT INTO users (phone, name, username, is_staff, is_admin, role)
                VALUES ('9743476555', 'Charan', 'charan', TRUE, TRUE, 'Registry Admin')
                ON CONFLICT (phone) DO UPDATE SET is_staff = TRUE, is_admin = TRUE, role = COALESCE(users.role, EXCLUDED.role), username = COALESCE(users.username, EXCLUDED.username);
            """)

            cur.execute("""
                INSERT INTO users (phone, name, is_staff, is_admin, role)
                VALUES ('7670885520', 'Bhuvana Kruthi', TRUE, TRUE, 'Approval Manager')
                ON CONFLICT (phone) DO UPDATE SET is_staff = TRUE, is_admin = TRUE, role = 'Approval Manager', name = EXCLUDED.name;
            """)

            conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def on_startup():
    run_migrations()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def create_token(phone: str, name: str, is_staff: bool, is_admin: bool = False, role: Optional[str] = None) -> str:
    payload = {
        "phone": phone,
        "name": name,
        "is_staff": is_staff,
        "is_admin": is_admin,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def touch_last_seen(phone: str):
    """
    Cheap heartbeat: marks a staff member online and stamps last_seen_at,
    throttled so rapid successive requests (Streamlit reruns) don't hammer
    the database. Called on every authenticated request from a staff/admin
    token. Presence is inferred from activity -- there's no persistent
    connection to watch, so someone who leaves the tab open but idle for
    longer than ONLINE_THRESHOLD_MINUTES will show offline again.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE users SET is_online = TRUE, last_seen_at = NOW()
                WHERE phone = %s AND (last_seen_at IS NULL OR last_seen_at < NOW() - INTERVAL '10 seconds');
                """,
                (phone,),
            )
            conn.commit()
    finally:
        conn.close()


def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session token")
    if payload.get("is_staff"):
        touch_last_seen(payload["phone"])
    return payload


def require_staff(user=Depends(get_current_user)):
    if not user.get("is_staff"):
        raise HTTPException(status_code=403, detail="Staff access required")
    return user


def require_admin(user=Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


# ---------------------------------------------------------------------------
# Document fraud-risk signals
#
# None of this proves a document is genuine or forged -- only the issuing
# state's own verification portal / QR code can do that. These are heuristic
# signals that route a document to a human reviewer; they never auto-approve
# or auto-reject anything.
# ---------------------------------------------------------------------------

KNOWN_EDITORS = ["photoshop", "gimp", "pixlr", "canva", "paint.net", "affinity photo"]


def compute_perceptual_hash(image_bytes: bytes) -> Optional[str]:
    """16x16 average hash -- cheap way to spot the same photo submitted twice."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((16, 16))
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p >= avg else "0" for p in pixels)
        return f"{int(bits, 2):x}"
    except Exception:
        return None


def optimize_image(image_bytes: bytes, max_dimension: int = 1600, quality: int = 85) -> bytes:
    """
    Downscale and re-compress before sending to the vision API or storing it.
    Phone-camera photos are routinely 3-12 MB at 4000px+, which is far more
    than needed for OCR and makes every step slow: the upload itself, the
    Gemini call, the database write, and every future download. This cuts
    that dramatically while keeping more than enough resolution to read text.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = img.convert("RGB")
        img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()
    except Exception:
        return image_bytes  # fall back to the original if Pillow can't process it


def check_edit_metadata(image_bytes: bytes) -> Optional[str]:
    """Look for an EXIF Software tag naming a known photo/image editor."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        exif = img.getexif()
        software = str(exif.get(305, "")).strip()  # 305 = EXIF "Software" tag
        if software and any(editor in software.lower() for editor in KNOWN_EDITORS):
            return software
    except Exception:
        pass
    return None


QUEUE_CITIZEN_SUBMISSIONS = "citizen_submissions"


def assign_next_staff(cur, queue_name: str = QUEUE_CITIZEN_SUBMISSIONS) -> Optional[str]:
    """
    Round-robin: picks the next staff phone in a stable, fixed order
    (all active staff sorted by phone), cycling past whoever was assigned
    last time for this queue. Returns None if there is no staff at all.

    This must be called with `cur` inside the same transaction that
    inserts the row being assigned, and with the transaction's rows locked
    appropriately by the caller (a single API worker per request keeps this
    simple; if you ever run multiple workers under load, wrap the read of
    assignment_state in `SELECT ... FOR UPDATE`).
    """
    cur.execute("SELECT phone FROM users WHERE is_staff = TRUE ORDER BY phone;")
    staff_phones = [row["phone"] for row in cur.fetchall()]
    if not staff_phones:
        return None

    cur.execute("SELECT last_staff_phone FROM assignment_state WHERE queue_name = %s;", (queue_name,))
    row = cur.fetchone()
    last_phone = row["last_staff_phone"] if row else None

    if last_phone in staff_phones:
        next_index = (staff_phones.index(last_phone) + 1) % len(staff_phones)
    else:
        next_index = 0  # last-assigned staff no longer active (or first ever run) -- restart the cycle
    next_phone = staff_phones[next_index]

    cur.execute(
        """
        INSERT INTO assignment_state (queue_name, last_staff_phone) VALUES (%s, %s)
        ON CONFLICT (queue_name) DO UPDATE SET last_staff_phone = EXCLUDED.last_staff_phone;
        """,
        (queue_name, next_phone),
    )
    return next_phone


def find_duplicate_submission(cur, image_hash: str, survey_no: str) -> Optional[dict]:
    """Same photo already on file under a different survey number/owner is a strong reuse signal."""
    if not image_hash:
        return None
    cur.execute(
        """
        SELECT d.id, d.survey_no, d.uploaded_at, p.owner_name
        FROM documents d LEFT JOIN cadastral_parcels p ON p.document_id = d.id
        WHERE d.image_hash = %s AND (d.survey_no IS DISTINCT FROM %s)
        ORDER BY d.uploaded_at LIMIT 1;
        """,
        (image_hash, survey_no),
    )
    return cur.fetchone()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    phone: str
    username: str
    name: str
    password: str


class SetInitialPasswordRequest(BaseModel):
    phone: str
    password: str


class LoginRequest(BaseModel):
    phone: str
    password: str


class ForgotPasswordRequest(BaseModel):
    username: str
    phone: str
    reason: Optional[str] = None


class PasswordResetCompleteRequest(BaseModel):
    username: str
    phone: str
    reset_code: str
    new_password: str


class PasswordResetRejectRequest(BaseModel):
    reason: str


class ProfileUpdateRequest(BaseModel):
    username: str
    email: Optional[str] = None


class RecordApprovalRequest(BaseModel):
    document_id: Optional[int] = None
    survey_no: str
    owner_name: str
    owner_phone: str
    area_hectares: float
    parent_plot: Optional[str] = None
    wkt_polygon: Optional[str] = None


class SubmissionRejectRequest(BaseModel):
    reason: str


class AddStaffRequest(BaseModel):
    phone: str
    name: str
    role: Optional[str] = None


class UpdateStaffRequest(BaseModel):
    phone: str
    is_staff: bool
    is_admin: bool
    role: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

PASSWORD_MIN_LENGTH = 8
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{2,29}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _normalize_username(username: str) -> str:
    username = username.strip().lower()
    if not USERNAME_RE.match(username):
        raise HTTPException(status_code=400, detail="Username must be 3-30 characters, start with a letter, and use only letters, numbers, dots, underscores, or hyphens.")
    return username


def _normalize_email(email: Optional[str]) -> Optional[str]:
    if email is None:
        return None
    email = email.strip().lower()
    if not email:
        return None
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    return email


PASSWORD_REQUIREMENTS_MSG = (
    f"Password must be at least {PASSWORD_MIN_LENGTH} characters and include "
    "at least one uppercase letter, one number, and one special character."
)


def _validate_password_strength(password: str) -> None:
    """
    Raises HTTPException(400) unless the password meets the site's minimum
    strength policy: 8+ characters, at least one uppercase letter, one
    digit, and one special (non-alphanumeric) character. Shared by signup
    and set-initial-password so both entry points enforce the same rule.
    """
    if (
        len(password) < PASSWORD_MIN_LENGTH
        or not re.search(r"[A-Z]", password)
        or not re.search(r"[0-9]", password)
        or not re.search(r"[^A-Za-z0-9]", password)
    ):
        raise HTTPException(status_code=400, detail=PASSWORD_REQUIREMENTS_MSG)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _finalize_session(user: dict) -> dict:
    """
    Shared by signup, set-initial-password, and login: records staff
    attendance (if applicable) and issues the session token. Keeping this in
    one place means all three entry points behave identically once a session
    actually starts.
    """
    if user["is_staff"]:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO staff_sessions (phone) VALUES (%s);", (user["phone"],))
                cur.execute("UPDATE users SET is_online = TRUE, last_seen_at = NOW() WHERE phone = %s;", (user["phone"],))
                conn.commit()
        finally:
            conn.close()

    token = create_token(user["phone"], user["name"], user["is_staff"], user.get("is_admin", False), user.get("role"))
    return {
        "token": token,
        "phone": user["phone"],
        "username": user.get("username"),
        "name": user["name"],
        "email": user.get("email"),
        "is_staff": user["is_staff"],
        "is_admin": user.get("is_admin", False),
        "role": user.get("role"),
    }


@app.get("/api/auth/account-status")
def account_status(phone: str):
    """
    Public, unauthenticated lookup the frontend uses to decide which form to
    show: sign-up (brand new number), set-a-password (an account exists --
    e.g. seeded staff -- but has never set one), or a normal password login.
    Deliberately returns nothing beyond these two booleans, not the account's
    name or role, to avoid leaking more than the login flow needs.
    """
    phone = phone.strip()
    if not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number (10-15 digits)")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash, username FROM users WHERE phone = %s;", (phone,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return {"exists": False, "has_password": False, "username": None}
    return {"exists": True, "has_password": bool(row["password_hash"]), "username": row.get("username")}


@app.post("/api/auth/signup")
def signup(req: SignupRequest):
    phone = req.phone.strip()
    username = _normalize_username(req.username)
    name = req.name.strip()
    if not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number (10-15 digits)")
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    _validate_password_strength(req.password)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users WHERE phone = %s;", (phone,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="This phone number is already registered. Please log in instead.")
            cur.execute("SELECT id FROM users WHERE LOWER(username) = LOWER(%s);", (username,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="That username is already taken. Please choose another one.")
            cur.execute(
                """
                INSERT INTO users (phone, username, name, password_hash)
                VALUES (%s, %s, %s, %s)
                RETURNING phone, username, name, email, is_staff, is_admin, role;
                """,
                (phone, username, name, _hash_password(req.password)),
            )
            user = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    return _finalize_session(user)


@app.post("/api/auth/set-initial-password")
def set_initial_password(req: SetInitialPasswordRequest):
    """
    For an account that already exists (typically staff, seeded with a name
    but no password) logging in for the very first time under this system.
    Their name is never asked for here -- it's already fixed from when the
    account was created.
    """
    phone = req.phone.strip()
    _validate_password_strength(req.password)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT phone, username, name, email, is_staff, is_admin, role, password_hash FROM users WHERE phone = %s;", (phone,))
            user = cur.fetchone()
            if not user:
                raise HTTPException(status_code=404, detail="No account found for this phone number.")
            if user["password_hash"]:
                raise HTTPException(status_code=409, detail="This account already has a password set. Please log in instead.")

            cur.execute("UPDATE users SET password_hash = %s WHERE phone = %s;", (_hash_password(req.password), phone))
            conn.commit()
    finally:
        conn.close()

    return _finalize_session(user)


@app.post("/api/auth/login")
def login(req: LoginRequest):
    phone = req.phone.strip()
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT phone, username, name, email, is_staff, is_admin, role, password_hash FROM users WHERE phone = %s;", (phone,))
            user = cur.fetchone()
    finally:
        conn.close()

    if not user:
        raise HTTPException(status_code=404, detail="No account found for this phone number. Please sign up first.")
    if not user["password_hash"]:
        raise HTTPException(status_code=409, detail="This account hasn't set a password yet.")
    if not _verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect password.")

    return _finalize_session(user)



# ---------------------------------------------------------------------------
# Password reset requests -- local, staff-approved recovery with no SMS or
# email provider. A user submits username + phone, staff review the request,
# and approval generates a one-time code that the staff member shares with
# the user manually. Only a SHA-256 hash of the code is stored in PostgreSQL.
# ---------------------------------------------------------------------------

RESET_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
RESET_CODE_TTL_MINUTES = 15


def _generate_reset_code() -> str:
    part1 = "".join(secrets.choice(RESET_CODE_ALPHABET) for _ in range(4))
    part2 = "".join(secrets.choice(RESET_CODE_ALPHABET) for _ in range(4))
    return f"DBH-{part1}-{part2}"


def _hash_reset_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


@app.post("/api/auth/password-reset/request")
def request_password_reset(req: ForgotPasswordRequest):
    phone = req.phone.strip()
    username = _normalize_username(req.username)
    reason = (req.reason or "").strip()[:1000]

    if not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number (10-15 digits)")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, phone, username, name FROM users WHERE phone = %s AND LOWER(username) = LOWER(%s);",
                (phone, username),
            )
            account = cur.fetchone()
            if not account:
                # Generic response avoids account enumeration.
                return {
                    "status": "RECEIVED",
                    "message": "If the account details match, a staff member will review the reset request.",
                }

            cur.execute(
                """
                SELECT id, status
                FROM password_reset_requests
                WHERE user_id = %s
                  AND token_used_at IS NULL
                  AND (
                      status = 'PENDING'
                      OR (status = 'APPROVED' AND reset_token_expires_at > NOW())
                  )
                ORDER BY requested_at DESC
                LIMIT 1;
                """,
                (account["id"],),
            )
            existing = cur.fetchone()
            if existing:
                if existing["status"] == "PENDING":
                    return {
                        "status": "PENDING",
                        "request_id": existing["id"],
                        "message": "A password reset request is already waiting for staff approval.",
                    }
                return {
                    "status": "APPROVED",
                    "request_id": existing["id"],
                    "message": "A staff-approved reset is already active. Use the one-time code provided by staff.",
                }

            cur.execute(
                """
                INSERT INTO password_reset_requests (user_id, phone, username, reason)
                VALUES (%s, %s, %s, %s)
                RETURNING id, requested_at;
                """,
                (account["id"], account["phone"], account["username"], reason or None),
            )
            new_request = cur.fetchone()
            conn.commit()
            return {
                "status": "PENDING",
                "request_id": new_request["id"],
                "message": "Reset request submitted. A DIGIBHUMI staff member must approve it before you can set a new password.",
            }
    finally:
        conn.close()


@app.get("/api/auth/password-reset/status")
def password_reset_status(username: str, phone: str):
    phone = phone.strip()
    username = username.strip().lower()
    if not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number (10-15 digits)")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, status, requested_at, reviewed_at, rejection_reason,
                       reset_token_expires_at
                FROM password_reset_requests
                WHERE phone = %s AND LOWER(username) = LOWER(%s)
                ORDER BY requested_at DESC
                LIMIT 1;
                """,
                (phone, username),
            )
            row = cur.fetchone()
    finally:
        conn.close()

    if not row:
        return {"status": "NONE", "message": "No password reset request found."}

    result = {
        "status": row["status"],
        "request_id": row["id"],
        "requested_at": row["requested_at"],
        "reviewed_at": row["reviewed_at"],
        "rejection_reason": row["rejection_reason"],
    }
    if row["status"] == "APPROVED":
        result["reset_token_expires_at"] = row["reset_token_expires_at"]
    return result


@app.get("/api/staff/password-reset-requests")
def list_password_reset_requests(_staff=Depends(require_staff)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id AS request_id,
                       r.username,
                       r.phone,
                       u.name,
                       u.email,
                       r.reason,
                       r.status,
                       r.requested_at,
                       r.reviewed_by,
                       r.reviewed_at,
                       r.rejection_reason,
                       r.reset_token_expires_at,
                       reviewer.name AS reviewed_by_name
                FROM password_reset_requests r
                JOIN users u ON u.id = r.user_id
                LEFT JOIN users reviewer ON reviewer.phone = r.reviewed_by
                WHERE r.status IN ('PENDING', 'APPROVED', 'REJECTED')
                ORDER BY CASE WHEN r.status = 'PENDING' THEN 0 ELSE 1 END,
                         r.requested_at DESC
                LIMIT 100;
                """
            )
            return {"requests": cur.fetchall()}
    finally:
        conn.close()


@app.post("/api/staff/password-reset-requests/{request_id}/approve")
def approve_password_reset(request_id: int, staff=Depends(require_staff)):
    reset_code = _generate_reset_code()
    token_hash = _hash_reset_code(reset_code)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, username, phone, status
                FROM password_reset_requests
                WHERE id = %s
                FOR UPDATE;
                """,
                (request_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Password reset request not found.")
            if row["status"] != "PENDING":
                raise HTTPException(status_code=409, detail="This password reset request has already been processed.")

            cur.execute(
                """
                UPDATE password_reset_requests
                SET status = 'APPROVED',
                    reviewed_by = %s,
                    reviewed_at = NOW(),
                    rejection_reason = NULL,
                    reset_token_hash = %s,
                    reset_token_expires_at = NOW() + (%s || ' minutes')::interval,
                    token_used_at = NULL
                WHERE id = %s
                RETURNING id, username, phone, reset_token_expires_at;
                """,
                (staff["phone"], token_hash, RESET_CODE_TTL_MINUTES, request_id),
            )
            approved = cur.fetchone()
            conn.commit()
            return {
                "status": "APPROVED",
                "request": approved,
                "reset_code": reset_code,
                "message": f"Reset approved. Share this one-time code with the user. It expires in {RESET_CODE_TTL_MINUTES} minutes.",
            }
    finally:
        conn.close()


@app.post("/api/staff/password-reset-requests/{request_id}/reject")
def reject_password_reset(request_id: int, req: PasswordResetRejectRequest, staff=Depends(require_staff)):
    reason = req.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="A rejection reason is required.")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE password_reset_requests
                SET status = 'REJECTED',
                    reviewed_by = %s,
                    reviewed_at = NOW(),
                    rejection_reason = %s,
                    reset_token_hash = NULL,
                    reset_token_expires_at = NULL
                WHERE id = %s AND status = 'PENDING'
                RETURNING id, status, rejection_reason;
                """,
                (staff["phone"], reason[:1000], request_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Request not found or already processed.")
            conn.commit()
            return {"status": "REJECTED", "request": row}
    finally:
        conn.close()


@app.post("/api/auth/password-reset/complete")
def complete_password_reset(req: PasswordResetCompleteRequest):
    phone = req.phone.strip()
    username = _normalize_username(req.username)
    reset_code = req.reset_code.strip().upper()

    if not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number (10-15 digits)")
    if len(reset_code) < 8:
        raise HTTPException(status_code=400, detail="Enter the reset code provided by staff.")
    _validate_password_strength(req.new_password)

    token_hash = _hash_reset_code(reset_code)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.user_id, r.reset_token_hash
                FROM password_reset_requests r
                WHERE r.phone = %s
                  AND LOWER(r.username) = LOWER(%s)
                  AND r.status = 'APPROVED'
                  AND r.token_used_at IS NULL
                  AND r.reset_token_expires_at > NOW()
                ORDER BY r.requested_at DESC
                LIMIT 1
                FOR UPDATE;
                """,
                (phone, username),
            )
            row = cur.fetchone()
            if not row or not row["reset_token_hash"] or row["reset_token_hash"] != token_hash:
                raise HTTPException(status_code=401, detail="Invalid or expired reset code.")

            cur.execute(
                """
                UPDATE users
                SET password_hash = %s
                WHERE id = %s
                RETURNING phone, username, name, email, is_staff, is_admin, role;
                """,
                (_hash_password(req.new_password), row["user_id"]),
            )
            updated_user = cur.fetchone()
            cur.execute(
                """
                UPDATE password_reset_requests
                SET token_used_at = NOW(), reset_token_hash = NULL
                WHERE id = %s;
                """,
                (row["id"],),
            )
            cur.execute(
                """
                UPDATE password_reset_requests
                SET status = 'REJECTED',
                    rejection_reason = 'Superseded by a completed password reset.'
                WHERE user_id = %s AND id <> %s AND status = 'PENDING';
                """,
                (row["user_id"], row["id"]),
            )
            conn.commit()
    finally:
        conn.close()

    return {
        "status": "SUCCESS",
        "message": "Password reset successfully. You can now log in with your new password.",
        **_finalize_session(updated_user),
    }


@app.put("/api/auth/profile")
def update_profile(req: ProfileUpdateRequest, user=Depends(get_current_user)):
    username = _normalize_username(req.username)
    email = _normalize_email(req.email)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM users WHERE LOWER(username) = LOWER(%s) AND phone <> %s;",
                (username, user["phone"]),
            )
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="That username is already taken.")
            if email:
                cur.execute(
                    "SELECT id FROM users WHERE LOWER(email) = LOWER(%s) AND phone <> %s;",
                    (email, user["phone"]),
                )
                if cur.fetchone():
                    raise HTTPException(status_code=409, detail="That email address is already linked to another account.")
            cur.execute(
                """
                UPDATE users SET username = %s, email = %s
                WHERE phone = %s
                RETURNING phone, username, name, email, is_staff, is_admin, role;
                """,
                (username, email, user["phone"]),
            )
            updated = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    return {
        "phone": updated["phone"],
        "username": updated["username"],
        "name": updated["name"],
        "email": updated.get("email"),
        "is_staff": updated["is_staff"],
        "is_admin": updated.get("is_admin", False),
        "role": updated.get("role"),
    }


@app.get("/api/auth/profile")
def get_profile(user=Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT phone, username, name, email, is_staff, is_admin, role FROM users WHERE phone = %s;", (user["phone"],))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    return dict(row)


@app.post("/api/auth/profile-photo")
async def upload_profile_photo(file: UploadFile = File(...), user=Depends(get_current_user)):
    allowed = {"image/jpeg", "image/png", "image/webp"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=400, detail="Profile photo must be JPG, PNG, or WEBP.")
    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Profile photo must be 5 MB or smaller.")
    try:
        image = Image.open(io.BytesIO(raw))
        image.verify()
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image.thumbnail((1000, 1000))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=88, optimize=True)
        data = out.getvalue()
    except Exception:
        raise HTTPException(status_code=400, detail="The uploaded file is not a valid image.")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET profile_photo = %s, profile_photo_mime = 'image/jpeg' WHERE phone = %s;", (psycopg2.Binary(data), user["phone"]))
            conn.commit()
    finally:
        conn.close()
    return {"status": "SUCCESS", "message": "Profile photo updated."}


@app.get("/api/auth/profile-photo")
def get_profile_photo(user=Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT profile_photo, profile_photo_mime FROM users WHERE phone = %s;", (user["phone"],))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row or not row.get("profile_photo"):
        raise HTTPException(status_code=404, detail="No profile photo")
    return Response(content=bytes(row["profile_photo"]), media_type=row.get("profile_photo_mime") or "image/jpeg")


@app.delete("/api/auth/profile-photo")
def delete_profile_photo(user=Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET profile_photo = NULL, profile_photo_mime = NULL WHERE phone = %s;", (user["phone"],))
            conn.commit()
    finally:
        conn.close()
    return {"status": "SUCCESS", "message": "Profile photo removed."}


@app.post("/api/auth/logout")
def logout(user=Depends(get_current_user)):
    """
    Marks the caller offline and closes their most recent open attendance
    session. Safe to call even for non-staff (no-op) since citizens don't
    have attendance tracked.
    """
    if not user.get("is_staff"):
        return {"status": "SUCCESS"}

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE staff_sessions SET logout_at = NOW()
                WHERE id = (
                    SELECT id FROM staff_sessions
                    WHERE phone = %s AND logout_at IS NULL
                    ORDER BY login_at DESC LIMIT 1
                );
                """,
                (user["phone"],),
            )
            # Runs after this request's get_current_user heartbeat already
            # marked them online, so this correctly has the final word.
            cur.execute("UPDATE users SET is_online = FALSE WHERE phone = %s;", (user["phone"],))
            conn.commit()
    finally:
        conn.close()
    return {"status": "SUCCESS"}


# ---------------------------------------------------------------------------
# Extraction -- real cloud OCR, but a human always approves before commit
# ---------------------------------------------------------------------------

def call_gemini_vision(image_bytes: bytes) -> dict:
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured on the server")

    prompt = """
    Examine this scanned Indian land record document. Extract ONLY what is
    visibly printed or handwritten on it -- never guess or invent a value.

    Also visually inspect the document for signs it may have been digitally
    edited or is not an authentic government document -- for example:
    inconsistent fonts or text sizes within the same field, misaligned or
    overlapping text, a missing or malformed official seal/stamp/letterhead,
    patches with different resolution/lighting than the rest of the page, or
    a layout that doesn't match a standard revenue department format. Do not
    guess at intent -- only report what is visually observable.

    Return ONLY valid JSON, no markdown fences:
    {
      "state_or_system": "",
      "survey_no": "",
      "owner_name": "",
      "extracted_area_raw": "",
      "area_hectares": 0.0,
      "confidence_score": 0.0,
      "tamper_signs": [],
      "tamper_risk": "low"
    }
    If a field cannot be read reliably, leave it as an empty string / 0 and
    lower confidence_score to reflect that. tamper_risk must be one of
    "low", "medium", "high" based only on what is visually observable.
    """
    payload = {
        "contents": [{"parts": [
            {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode()}},
            {"text": prompt},
        ]}],
        "generationConfig": {"temperature": 0.1},
    }
    resp = requests.post(
        GEMINI_ENDPOINT,
        json=payload,
        headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_API_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    res_json = resp.json()
    raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
    raw_text = re.sub(r"^```(?:json)?", "", raw_text)
    raw_text = re.sub(r"```$", "", raw_text).strip()
    data = json.loads(raw_text)
    return {
        "state_or_system": str(data.get("state_or_system", "")).strip(),
        "survey_no": str(data.get("survey_no", "")).strip(),
        "owner_name": str(data.get("owner_name", "")).strip(),
        "extracted_area_raw": str(data.get("extracted_area_raw", "")).strip(),
        "area_hectares": float(data.get("area_hectares", 0) or 0),
        "confidence": float(data.get("confidence_score", 0) or 0),
        "tamper_signs": data.get("tamper_signs", []) or [],
        "tamper_risk": str(data.get("tamper_risk", "low")).strip().lower(),
    }


@app.post("/api/extract-and-validate")
async def extract_and_validate(file: UploadFile = File(...), _staff=Depends(require_staff)):
    original_bytes = await file.read()
    edit_software = check_edit_metadata(original_bytes)  # must run before resizing strips EXIF
    contents = optimize_image(original_bytes)

    try:
        extracted = call_gemini_vision(contents)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OCR extraction failed ({e}). Please enter the details manually.")

    image_hash = compute_perceptual_hash(contents)

    authenticity_flags = []
    if extracted["tamper_signs"]:
        authenticity_flags.extend(extracted["tamper_signs"])
    if edit_software:
        authenticity_flags.append(f"Image metadata shows it was processed with '{edit_software}'.")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            duplicate = find_duplicate_submission(cur, image_hash, extracted["survey_no"])
            if duplicate:
                authenticity_flags.append(
                    f"DUPLICATE SUBMISSION: this exact image was already submitted on "
                    f"{duplicate['uploaded_at']} for survey no. '{duplicate['survey_no']}' "
                    f"(owner on file: '{duplicate['owner_name'] or 'unassigned'}')."
                )

            needs_review = (
                extracted["confidence"] < CONFIDENCE_REVIEW_THRESHOLD
                or not extracted["survey_no"]
                or not extracted["owner_name"]
                or extracted["tamper_risk"] in ("medium", "high")
                or bool(authenticity_flags)
            )

            cur.execute(
                """
                INSERT INTO documents (survey_no, file_name, mime_type, file_bytes, image_hash, authenticity_flags)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id;
                """,
                (
                    extracted["survey_no"] or None,
                    file.filename,
                    "image/jpeg",
                    psycopg2.Binary(contents),
                    image_hash,
                    json.dumps(authenticity_flags),
                ),
            )
            document_id = cur.fetchone()["id"]

            validation_issues = []
            if extracted["survey_no"]:
                cur.execute("SELECT owner_name FROM cadastral_parcels WHERE survey_no = %s;", (extracted["survey_no"],))
                row = cur.fetchone()
                if row:
                    sim = fuzz.token_sort_ratio(row["owner_name"].lower(), extracted["owner_name"].lower())
                    if sim < 80:
                        validation_issues.append(
                            f"CRITICAL DISPUTE: Plot {extracted['survey_no']} is registered to "
                            f"'{row['owner_name']}', but this document claims '{extracted['owner_name']}'."
                        )
                    else:
                        validation_issues.append(f"Verified: matches registered owner '{row['owner_name']}'.")

            if needs_review:
                cur.execute(
                    "INSERT INTO review_queue (document_id, extracted_json, confidence) VALUES (%s, %s, %s);",
                    (document_id, json.dumps(extracted), extracted["confidence"]),
                )
            conn.commit()
    finally:
        conn.close()

    return {
        "document_id": document_id,
        "extracted": extracted,
        "needs_review": needs_review,
        "validation_issues": validation_issues,
        "authenticity_flags": authenticity_flags,
    }


@app.get("/api/review-queue")
def get_review_queue(_staff=Depends(require_staff)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT r.id, r.document_id, r.extracted_json, r.confidence, r.status, r.created_at, d.file_name
                FROM review_queue r JOIN documents d ON d.id = r.document_id
                WHERE r.status = 'PENDING'
                ORDER BY r.created_at;
                """
            )
            return {"queue": cur.fetchall()}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Citizen self-submission -- upload a document, staff must approve it before
# it ever appears on the citizen's "My Land Records" page.
# ---------------------------------------------------------------------------

@app.get("/api/staff/list")
def list_staff(_admin=Depends(require_admin)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT u.phone, u.name, u.role, u.is_admin, u.is_staff, u.created_at,
                       COALESCE((
                           SELECT COUNT(*)
                           FROM password_reset_requests r
                           WHERE r.phone = u.phone AND r.status = 'PENDING'
                       ), 0) AS pending_reset_requests
                FROM users u
                WHERE u.is_staff = TRUE
                ORDER BY u.created_at;
                """
            )
            return {"staff": cur.fetchall()}
    finally:
        conn.close()


@app.post("/api/staff/update")
def update_staff(req: UpdateStaffRequest, admin=Depends(require_admin)):
    phone = req.phone.strip()

    if phone == admin["phone"] and (not req.is_staff or not req.is_admin):
        raise HTTPException(status_code=400, detail="You can't remove your own staff or admin access -- ask another admin to do it.")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT is_admin FROM users WHERE phone = %s;", (phone,))
            target = cur.fetchone()
            if not target:
                raise HTTPException(status_code=404, detail="Staff member not found")

            if target["is_admin"] and not req.is_admin:
                cur.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin = TRUE AND phone != %s;", (phone,))
                if cur.fetchone()["n"] == 0:
                    raise HTTPException(status_code=400, detail="Can't remove the last remaining admin.")

            cur.execute(
                """
                UPDATE users SET is_staff = %s, is_admin = %s, role = %s
                WHERE phone = %s
                RETURNING phone, name, role, is_admin, is_staff;
                """,
                (req.is_staff, req.is_admin, (req.role or "").strip() or None, phone),
            )
            result = cur.fetchone()

            unassigned_count = 0
            if not req.is_staff:
                # They're losing staff access entirely -- their pending queue
                # needs a new owner. Unassign it here; an admin then either
                # calls /api/admin/rebalance-unassigned or reassigns by hand.
                cur.execute(
                    "UPDATE citizen_submissions SET assigned_to = NULL WHERE assigned_to = %s AND status = 'PENDING';",
                    (phone,),
                )
                unassigned_count = cur.rowcount

            conn.commit()
            return {"status": "SUCCESS", "staff": result, "unassigned_pending_count": unassigned_count}
    finally:
        conn.close()


@app.post("/api/staff/add")
def add_staff(req: AddStaffRequest, _admin=Depends(require_admin)):
    phone = req.phone.strip()
    if not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number (10-15 digits)")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # New hires added this way are staff, never admin -- admin status is
            # granted separately (currently only via a direct migration/seed),
            # so day-to-day staff can never grant themselves or others that access.
            cur.execute(
                """
                INSERT INTO users (phone, name, is_staff, is_admin, role)
                VALUES (%s, %s, TRUE, FALSE, %s)
                ON CONFLICT (phone) DO UPDATE SET is_staff = TRUE, name = EXCLUDED.name, role = EXCLUDED.role
                RETURNING phone, name, role, is_admin;
                """,
                (phone, req.name.strip(), (req.role or "").strip() or None),
            )
            result = cur.fetchone()
            conn.commit()
            return {"status": "SUCCESS", "staff": result}
    finally:
        conn.close()


@app.post("/api/citizen/submit-document")
async def submit_document(
    file: UploadFile = File(...),
    claimed_survey_no: Optional[str] = Form(None),
    note: Optional[str] = Form(None),
    user=Depends(get_current_user),
):
    original_bytes = await file.read()
    edit_software = check_edit_metadata(original_bytes)  # must run before resizing strips EXIF
    contents = optimize_image(original_bytes)

    try:
        extracted = call_gemini_vision(contents)
    except Exception:
        extracted = None  # staff will read/verify the document manually if extraction fails

    image_hash = compute_perceptual_hash(contents)
    survey_hint = (claimed_survey_no or "").strip() or (extracted["survey_no"] if extracted else None)

    authenticity_flags = []
    if extracted and extracted.get("tamper_signs"):
        authenticity_flags.extend(extracted["tamper_signs"])
    if edit_software:
        authenticity_flags.append(f"Image metadata shows it was processed with '{edit_software}'.")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            duplicate = find_duplicate_submission(cur, image_hash, survey_hint)
            if duplicate:
                authenticity_flags.append(
                    f"DUPLICATE SUBMISSION: this exact image was already submitted on "
                    f"{duplicate['uploaded_at']} for survey no. '{duplicate['survey_no']}' "
                    f"(owner on file: '{duplicate['owner_name'] or 'unassigned'}')."
                )

            cur.execute(
                """
                INSERT INTO documents (survey_no, file_name, mime_type, file_bytes, image_hash, authenticity_flags)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id;
                """,
                (survey_hint, file.filename, "image/jpeg", psycopg2.Binary(contents), image_hash, json.dumps(authenticity_flags)),
            )
            document_id = cur.fetchone()["id"]

            assigned_to = assign_next_staff(cur)

            cur.execute(
                """
                INSERT INTO citizen_submissions (submitter_phone, submitter_name, document_id, claimed_survey_no, note, assigned_to)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id, status, created_at, assigned_to;
                """,
                (user["phone"], user["name"], document_id, claimed_survey_no, note, assigned_to),
            )
            submission = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    return {
        "submission_id": submission["id"],
        "status": submission["status"],
        "document_id": document_id,
        "extracted": extracted,
        "authenticity_flags": authenticity_flags,
        "assigned_to": submission["assigned_to"],
    }


@app.get("/api/citizen/my-submissions")
def my_submissions(user=Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, claimed_survey_no, note, status, rejection_reason, created_at, reviewed_at
                FROM citizen_submissions WHERE submitter_phone = %s ORDER BY created_at DESC;
                """,
                (user["phone"],),
            )
            return {"submissions": cur.fetchall()}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Staff: review and approve/reject citizen submissions
# ---------------------------------------------------------------------------

@app.get("/api/staff/submissions")
def staff_submissions(scope: str = "mine", staff=Depends(require_staff)):
    """
    scope="mine" (default): only submissions round-robin-assigned to the
    calling staff member -- this is what splits a shared queue across staff.
    scope="all": every pending submission regardless of assignee, with who
    it's assigned to -- admin-only, used for the reassignment view.
    """
    if scope not in ("mine", "all"):
        raise HTTPException(status_code=400, detail="scope must be 'mine' or 'all'")
    if scope == "all" and not staff.get("is_admin"):
        raise HTTPException(status_code=403, detail="Only admins can view all staff's submissions")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            base_query = """
                SELECT s.id AS submission_id, s.submitter_phone, s.submitter_name, s.claimed_survey_no,
                       s.note, s.created_at, s.assigned_to, u.name AS assigned_to_name,
                       d.id AS document_id, d.file_name, d.authenticity_flags
                FROM citizen_submissions s
                JOIN documents d ON d.id = s.document_id
                LEFT JOIN users u ON u.phone = s.assigned_to
                WHERE s.status = 'PENDING'
            """
            if scope == "mine":
                cur.execute(base_query + " AND s.assigned_to = %s ORDER BY s.created_at;", (staff["phone"],))
            else:
                cur.execute(base_query + " ORDER BY s.created_at;")
            return {"submissions": cur.fetchall()}
    finally:
        conn.close()


@app.get("/api/staff/list-active")
def list_active_staff(_staff=Depends(require_staff)):
    """Lightweight staff roster (phone + name only) for populating a reassignment dropdown."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT phone, name, role FROM users WHERE is_staff = TRUE ORDER BY name;")
            return {"staff": cur.fetchall()}
    finally:
        conn.close()


class ReassignRequest(BaseModel):
    assigned_to: str


@app.post("/api/staff/submissions/{submission_id}/reassign")
def reassign_submission(submission_id: int, req: ReassignRequest, admin=Depends(require_admin)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT is_staff FROM users WHERE phone = %s;", (req.assigned_to.strip(),))
            target = cur.fetchone()
            if not target or not target["is_staff"]:
                raise HTTPException(status_code=400, detail="Target must be an existing staff member")

            cur.execute(
                """
                UPDATE citizen_submissions SET assigned_to = %s
                WHERE id = %s AND status = 'PENDING'
                RETURNING id, assigned_to;
                """,
                (req.assigned_to.strip(), submission_id),
            )
            result = cur.fetchone()
            if not result:
                raise HTTPException(status_code=404, detail="Submission not found or already reviewed")
            conn.commit()
            return {"status": "SUCCESS", "submission": result}
    finally:
        conn.close()


@app.post("/api/admin/rebalance-unassigned")
def rebalance_unassigned(_admin=Depends(require_admin)):
    """
    Round-robin-assigns any pending submission that currently has no
    assignee -- covers submissions that predate this feature, or that lost
    their assignee because that staff member's access was removed.
    """
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM citizen_submissions WHERE status = 'PENDING' AND assigned_to IS NULL ORDER BY created_at;")
            unassigned_ids = [row["id"] for row in cur.fetchall()]
            for sub_id in unassigned_ids:
                next_staff = assign_next_staff(cur)
                if not next_staff:
                    break  # no staff exist at all; nothing more we can do
                cur.execute("UPDATE citizen_submissions SET assigned_to = %s WHERE id = %s;", (next_staff, sub_id))
            conn.commit()
            return {"status": "SUCCESS", "reassigned_count": len(unassigned_ids)}
    finally:
        conn.close()


@app.get("/api/admin/staff-attendance")
def staff_attendance(date: Optional[str] = None, _admin=Depends(require_admin)):
    """
    Live online/offline status for every staff member, plus their
    login/logout history for one calendar day (server date by default).
    """
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format")
    else:
        date = datetime.now(timezone.utc).date().isoformat()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT phone, name, role, is_online, last_seen_at,
                       (is_online AND last_seen_at >= NOW() - INTERVAL '%s minutes') AS effective_online
                FROM users WHERE is_staff = TRUE ORDER BY name;
                """
                % ONLINE_THRESHOLD_MINUTES
            )
            staff_rows = cur.fetchall()

            cur.execute(
                "SELECT phone, login_at, logout_at FROM staff_sessions WHERE login_at::date = %s ORDER BY login_at;",
                (date,),
            )
            sessions_by_phone = {}
            for row in cur.fetchall():
                sessions_by_phone.setdefault(row["phone"], []).append(
                    {"login_at": row["login_at"], "logout_at": row["logout_at"]}
                )

            for s in staff_rows:
                s["sessions"] = sessions_by_phone.get(s["phone"], [])

            return {"date": date, "online_threshold_minutes": ONLINE_THRESHOLD_MINUTES, "staff": staff_rows}
    finally:
        conn.close()


@app.get("/api/admin/staff-progress")
def staff_progress(date: Optional[str] = None, _admin=Depends(require_admin)):
    """
    Per-staff approval/rejection counts for one calendar day (server date by
    default), plus their current pending backlog (which isn't date-bound --
    a pending item just sits there until it's actioned).
    """
    if date:
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be in YYYY-MM-DD format")
    else:
        date = datetime.now(timezone.utc).date().isoformat()

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    u.phone,
                    u.name,
                    u.role,
                    COALESCE(SUM(CASE WHEN s.status = 'APPROVED' AND s.reviewed_by = u.phone
                                       AND s.reviewed_at::date = %(date)s THEN 1 ELSE 0 END), 0) AS approved_today,
                    COALESCE(SUM(CASE WHEN s.status = 'REJECTED' AND s.reviewed_by = u.phone
                                       AND s.reviewed_at::date = %(date)s THEN 1 ELSE 0 END), 0) AS rejected_today,
                    (SELECT COUNT(*) FROM citizen_submissions p
                     WHERE p.assigned_to = u.phone AND p.status = 'PENDING') AS pending_now
                FROM users u
                LEFT JOIN citizen_submissions s ON s.reviewed_by = u.phone
                WHERE u.is_staff = TRUE
                GROUP BY u.phone, u.name, u.role
                ORDER BY u.name;
                """,
                {"date": date},
            )
            return {"date": date, "staff": cur.fetchall()}
    finally:
        conn.close()


@app.post("/api/staff/submissions/{submission_id}/approve")
def approve_submission(submission_id: int, req: RecordApprovalRequest, staff=Depends(require_staff)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT document_id, status FROM citizen_submissions WHERE id = %s;", (submission_id,))
            sub = cur.fetchone()
    finally:
        conn.close()

    if not sub:
        raise HTTPException(status_code=404, detail="Submission not found")
    if sub["status"] != "PENDING":
        raise HTTPException(status_code=409, detail=f"Submission already {sub['status'].lower()}")

    req.document_id = req.document_id or sub["document_id"]
    result = _do_commit(req, staff["phone"])

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE citizen_submissions SET status = 'APPROVED', reviewed_by = %s, reviewed_at = NOW() WHERE id = %s;",
                (staff["phone"], submission_id),
            )
            conn.commit()
    finally:
        conn.close()

    return result


@app.post("/api/staff/submissions/{submission_id}/reject")
def reject_submission(submission_id: int, req: SubmissionRejectRequest, staff=Depends(require_staff)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE citizen_submissions SET status = 'REJECTED', rejection_reason = %s,
                       reviewed_by = %s, reviewed_at = NOW()
                WHERE id = %s AND status = 'PENDING';
                """,
                (req.reason, staff["phone"], submission_id),
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Submission not found or already reviewed")
            conn.commit()
    finally:
        conn.close()
    return {"status": "REJECTED"}


# ---------------------------------------------------------------------------
# Commit -- staff only, always a deliberate human action
# ---------------------------------------------------------------------------

def _do_commit(req: RecordApprovalRequest, staff_phone: str) -> dict:
    owner_phone = req.owner_phone.strip()
    if not PHONE_RE.match(owner_phone):
        raise HTTPException(status_code=400, detail="owner_phone must be a valid phone number -- it's how the owner logs in to see this record")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_name FROM cadastral_parcels WHERE survey_no = %s;", (req.survey_no,))
            existing = cur.fetchone()
            if existing and fuzz.token_sort_ratio(existing["owner_name"].lower(), req.owner_name.lower()) < 70:
                raise HTTPException(status_code=409, detail=f"Ownership dispute on plot {req.survey_no}: currently registered to '{existing['owner_name']}'")

            cur.execute("SELECT block_hash FROM audit_ledger ORDER BY block_index DESC LIMIT 1;")
            row = cur.fetchone()
            prev_hash = row["block_hash"] if row else "0" * 64

            block_data = f"{req.survey_no}:{req.owner_name}:{owner_phone}:{req.area_hectares}:{prev_hash}:{staff_phone}"
            block_hash = hashlib.sha256(block_data.encode()).hexdigest()

            cur.execute(
                """
                INSERT INTO audit_ledger (survey_no, owner_name, owner_phone, area_hectares, action, block_hash, prev_hash)
                VALUES (%s, %s, %s, %s, 'REGISTER', %s, %s);
                """,
                (req.survey_no, req.owner_name, owner_phone, req.area_hectares, block_hash, prev_hash),
            )

            wkt = req.wkt_polygon or 'POLYGON((77.5950 12.9700, 77.5970 12.9700, 77.5970 12.9720, 77.5950 12.9720, 77.5950 12.9700))'
            cur.execute(
                """
                INSERT INTO cadastral_parcels (survey_no, owner_name, owner_phone, area_hectares, parent_plot, document_id, geom)
                VALUES (%s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326))
                ON CONFLICT (survey_no) DO UPDATE
                SET owner_name = EXCLUDED.owner_name,
                    owner_phone = EXCLUDED.owner_phone,
                    area_hectares = EXCLUDED.area_hectares,
                    document_id = EXCLUDED.document_id;
                """,
                (req.survey_no, req.owner_name, owner_phone, req.area_hectares, req.parent_plot, req.document_id, wkt),
            )

            if req.document_id:
                cur.execute("UPDATE review_queue SET status = 'APPROVED' WHERE document_id = %s AND status = 'PENDING';", (req.document_id,))
                cur.execute("UPDATE documents SET survey_no = %s WHERE id = %s;", (req.survey_no, req.document_id))

            conn.commit()
            return {"status": "SUCCESS", "block_hash": block_hash}
    finally:
        conn.close()


@app.post("/api/commit-record")
def commit_record(req: RecordApprovalRequest, staff=Depends(require_staff)):
    return _do_commit(req, staff["phone"])


# ---------------------------------------------------------------------------
# Staff: full registry view
# ---------------------------------------------------------------------------

@app.get("/api/records")
def get_records(_staff=Depends(require_staff)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, survey_no, owner_name, owner_phone, area_hectares, parent_plot, document_id, "
                "ST_AsGeoJSON(geom) as geojson FROM cadastral_parcels;"
            )
            parcels = cur.fetchall()
            cur.execute("SELECT * FROM audit_ledger ORDER BY block_index DESC;")
            ledger = cur.fetchall()
            return {"parcels": parcels, "ledger": ledger}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Citizen: only their own records
# ---------------------------------------------------------------------------

@app.get("/api/my-records")
def my_records(user=Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, survey_no, owner_name, area_hectares, parent_plot, document_id, "
                "ST_AsGeoJSON(geom) as geojson FROM cadastral_parcels WHERE owner_phone = %s;",
                (user["phone"],),
            )
            return {"parcels": cur.fetchall()}
    finally:
        conn.close()


@app.get("/api/documents/{document_id}")
def get_document(document_id: int, user=Depends(get_current_user)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT d.file_name, d.mime_type, d.file_bytes,
                       p.owner_phone AS parcel_owner_phone,
                       s.submitter_phone AS submission_owner_phone
                FROM documents d
                LEFT JOIN cadastral_parcels p ON p.document_id = d.id
                LEFT JOIN citizen_submissions s ON s.document_id = d.id
                WHERE d.id = %s;
                """,
                (document_id,),
            )
            doc = cur.fetchone()
    finally:
        conn.close()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    is_owner = user["phone"] in (doc["parcel_owner_phone"], doc["submission_owner_phone"])
    if not user.get("is_staff") and not is_owner:
        raise HTTPException(status_code=403, detail="You are not the registered owner of this document")

    return Response(content=bytes(doc["file_bytes"]), media_type=doc["mime_type"] or "application/octet-stream")
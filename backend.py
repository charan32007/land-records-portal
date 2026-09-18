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

OTP_DEBUG_MODE = os.environ.get("OTP_DEBUG_MODE", "true").lower() == "true"
OTP_TTL_MINUTES = 5
CONFIDENCE_REVIEW_THRESHOLD = 0.85

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
                    role VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(100);")

            cur.execute("""
                CREATE TABLE IF NOT EXISTS otp_codes (
                    id SERIAL PRIMARY KEY,
                    phone VARCHAR(15) NOT NULL,
                    code_hash VARCHAR(64) NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    consumed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

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
                INSERT INTO users (phone, name, is_staff, role)
                VALUES ('9999999999', 'Registry Staff Admin', TRUE, 'Registry Admin')
                ON CONFLICT (phone) DO UPDATE SET role = COALESCE(users.role, EXCLUDED.role);
            """)

            cur.execute("""
                INSERT INTO users (phone, name, is_staff, role)
                VALUES ('7670885520', 'Bhuvana Kruthi', TRUE, 'Approval Manager')
                ON CONFLICT (phone) DO UPDATE SET is_staff = TRUE, role = 'Approval Manager', name = EXCLUDED.name;
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

def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def create_token(phone: str, name: str, is_staff: bool, role: Optional[str] = None) -> str:
    payload = {
        "phone": phone,
        "name": name,
        "is_staff": is_staff,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def get_current_user(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ", 1)[1]
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid session token")


def require_staff(user=Depends(get_current_user)):
    if not user.get("is_staff"):
        raise HTTPException(status_code=403, detail="Staff access required")
    return user


def send_sms(phone: str, message: str):
    """
    Plug a real SMS/OTP provider in here (Twilio, MSG91, etc.) using their API
    and credentials from environment variables. While OTP_DEBUG_MODE is true,
    no SMS is sent -- the code is returned directly in the API response
    instead, for local testing only. Do not ship OTP_DEBUG_MODE=true.
    """
    if OTP_DEBUG_MODE:
        return
    raise HTTPException(
        status_code=501,
        detail="No SMS provider is wired up. Implement send_sms() before setting OTP_DEBUG_MODE=false.",
    )


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

class OtpRequest(BaseModel):
    phone: str
    name: str


class OtpVerify(BaseModel):
    phone: str
    code: str
    name: str


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


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/request-otp")
def request_otp(req: OtpRequest):
    phone = req.phone.strip()
    if not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number (10-15 digits)")

    code = f"{secrets.randbelow(1000000):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO otp_codes (phone, code_hash, expires_at) VALUES (%s, %s, %s);",
                (phone, hash_code(code), expires_at),
            )
            conn.commit()
    finally:
        conn.close()

    send_sms(phone, f"Your Land Registry OTP is {code}. Valid for {OTP_TTL_MINUTES} minutes.")

    response = {"status": "OTP_SENT", "expires_in_minutes": OTP_TTL_MINUTES}
    if OTP_DEBUG_MODE:
        response["debug_otp"] = code  # DEV ONLY -- remove once a real SMS provider is wired in
    return response


@app.post("/api/auth/verify-otp")
def verify_otp(req: OtpVerify):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM otp_codes
                WHERE phone = %s AND code_hash = %s AND consumed = FALSE AND expires_at > NOW()
                ORDER BY id DESC LIMIT 1;
                """,
                (req.phone.strip(), hash_code(req.code.strip())),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=401, detail="Invalid or expired OTP")

            cur.execute("UPDATE otp_codes SET consumed = TRUE WHERE id = %s;", (row["id"],))

            cur.execute(
                """
                INSERT INTO users (phone, name) VALUES (%s, %s)
                ON CONFLICT (phone) DO UPDATE SET name = EXCLUDED.name
                RETURNING phone, name, is_staff, role;
                """,
                (req.phone.strip(), req.name.strip()),
            )
            user = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    token = create_token(user["phone"], user["name"], user["is_staff"], user.get("role"))
    return {"token": token, "phone": user["phone"], "name": user["name"], "is_staff": user["is_staff"], "role": user.get("role")}


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
def list_staff(_staff=Depends(require_staff)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT phone, name, role, created_at FROM users WHERE is_staff = TRUE ORDER BY created_at;")
            return {"staff": cur.fetchall()}
    finally:
        conn.close()


@app.post("/api/staff/add")
def add_staff(req: AddStaffRequest, _staff=Depends(require_staff)):
    phone = req.phone.strip()
    if not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number (10-15 digits)")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (phone, name, is_staff, role)
                VALUES (%s, %s, TRUE, %s)
                ON CONFLICT (phone) DO UPDATE SET is_staff = TRUE, name = EXCLUDED.name, role = EXCLUDED.role
                RETURNING phone, name, role;
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

            cur.execute(
                """
                INSERT INTO citizen_submissions (submitter_phone, submitter_name, document_id, claimed_survey_no, note)
                VALUES (%s, %s, %s, %s, %s) RETURNING id, status, created_at;
                """,
                (user["phone"], user["name"], document_id, claimed_survey_no, note),
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
def staff_submissions(_staff=Depends(require_staff)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT s.id AS submission_id, s.submitter_phone, s.submitter_name, s.claimed_survey_no,
                       s.note, s.created_at, d.id AS document_id, d.file_name, d.authenticity_flags
                FROM citizen_submissions s JOIN documents d ON d.id = s.document_id
                WHERE s.status = 'PENDING'
                ORDER BY s.created_at;
                """
            )
            return {"submissions": cur.fetchall()}
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
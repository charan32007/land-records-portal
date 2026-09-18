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
from fastapi import FastAPI, UploadFile, File, HTTPException, Header, Depends
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

OTP_TTL_MINUTES = 5
CONFIDENCE_REVIEW_THRESHOLD = 0.85

# ---------------------------------------------------------------------------
# SMS / OTP delivery configuration
#
# SMS_PROVIDER picks how OTPs reach a real phone:
#   none    -> no SMS is sent (local dev only; see OTP_DEBUG_MODE below)
#   twilio  -> Twilio Programmable Messaging
#   msg91   -> MSG91 (India; needs a DLT-registered sender + template)
#   fast2sms-> Fast2SMS (India, OTP route)
#
# OTP_DEBUG_MODE only has any effect while SMS_PROVIDER=none. As soon as a
# real provider is configured, the OTP is NEVER returned in the API response,
# regardless of what OTP_DEBUG_MODE is set to. That is deliberate: a flag left
# on by accident should not be able to hand out login codes over HTTP.
# ---------------------------------------------------------------------------
SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "none").strip().lower()

# Country code assumed when a user types a plain national number (91 = India).
DEFAULT_COUNTRY_CODE = os.environ.get("DEFAULT_COUNTRY_CODE", "91").strip().lstrip("+")

_OTP_DEBUG_REQUESTED = os.environ.get("OTP_DEBUG_MODE", "true").lower() == "true"
OTP_DEBUG_MODE = _OTP_DEBUG_REQUESTED and SMS_PROVIDER == "none"

# Twilio
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")

# MSG91
MSG91_AUTH_KEY = os.environ.get("MSG91_AUTH_KEY", "")
MSG91_SENDER_ID = os.environ.get("MSG91_SENDER_ID", "")
MSG91_TEMPLATE_ID = os.environ.get("MSG91_TEMPLATE_ID", "")

# Fast2SMS
FAST2SMS_API_KEY = os.environ.get("FAST2SMS_API_KEY", "")

SMS_TIMEOUT_SECONDS = 15

# Abuse limits on the OTP endpoints. These are in-process counters, so they
# only hold with a single API worker -- with multiple workers or replicas,
# move this to Redis or enforce it at the reverse proxy. Real SMS costs money
# per message, so do not run without some limit in front of these endpoints.
OTP_REQUESTS_PER_WINDOW = int(os.environ.get("OTP_REQUESTS_PER_WINDOW", "3"))
OTP_VERIFY_ATTEMPTS_PER_WINDOW = int(os.environ.get("OTP_VERIFY_ATTEMPTS_PER_WINDOW", "5"))
OTP_RATE_WINDOW_MINUTES = int(os.environ.get("OTP_RATE_WINDOW_MINUTES", "15"))

_otp_request_log: dict = {}
_otp_verify_log: dict = {}

PHONE_RE = re.compile(r"^\+?\d{10,15}$")


def get_db_connection():
    return psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def create_token(phone: str, name: str, is_staff: bool, is_policymaker: bool = False) -> str:
    payload = {
        "phone": phone,
        "name": name,
        "is_staff": is_staff,
        "is_policymaker": is_policymaker,
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


def require_analytics_access(user=Depends(get_current_user)):
    """Staff and policymaker roles can both view aggregate analytics.
    The endpoints behind this dependency must never return owner names,
    phone numbers, or document contents -- only counts and trends."""
    if not (user.get("is_staff") or user.get("is_policymaker")):
        raise HTTPException(status_code=403, detail="Staff or policymaker access required")
    return user


# ---------------------------------------------------------------------------
# Phone number handling
#
# One canonical string per person is what makes the rest of the system work:
# `users.phone` is UNIQUE, parcels are matched to citizens by owner_phone, and
# verify-otp looks up the code by exact phone match. If the same person can
# arrive as "9743476555", "+919743476555" and "09743476555", they become three
# separate accounts and the seeded staff row stops matching.
#
# Canonical form = the national 10-digit number when the country code is the
# configured default (so existing rows like '9743476555' keep working), and
# +E.164 for anything else. to_e164() is used only when handing the number to
# an SMS provider, which always wants the full international form.
# ---------------------------------------------------------------------------

def normalize_phone(raw: str) -> str:
    """Return the canonical stored form of a phone number, or '' if unusable."""
    if not raw:
        return ""
    s = raw.strip()
    had_plus = s.startswith("+") or s.startswith("00")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""

    if s.startswith("00"):
        digits = digits[2:]

    cc = DEFAULT_COUNTRY_CODE

    # Trunk prefix: a leading 0 on a national number (e.g. 09743476555).
    if not had_plus and len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    # Default-country number given with its country code.
    if digits.startswith(cc) and len(digits) == len(cc) + 10:
        return digits[len(cc):]

    # Plain national number.
    if len(digits) == 10:
        return digits

    # Anything else is treated as an international number.
    if 8 <= len(digits) <= 15:
        return "+" + digits

    return ""


def to_e164(canonical: str) -> str:
    """Full international form, for handing to an SMS provider."""
    if canonical.startswith("+"):
        return canonical
    return f"+{DEFAULT_COUNTRY_CODE}{canonical}"


# ---------------------------------------------------------------------------
# Rate limiting (in-process -- see note at OTP_REQUESTS_PER_WINDOW)
# ---------------------------------------------------------------------------

def _rate_limit(log: dict, key: str, limit: int, detail: str):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=OTP_RATE_WINDOW_MINUTES)
    hits = [t for t in log.get(key, []) if t > cutoff]
    if len(hits) >= limit:
        log[key] = hits
        raise HTTPException(status_code=429, detail=detail)
    hits.append(now)
    log[key] = hits

    # Opportunistic cleanup so the dicts don't grow without bound.
    if len(log) > 5000:
        for k in [k for k, v in log.items() if not any(t > cutoff for t in v)]:
            log.pop(k, None)


# ---------------------------------------------------------------------------
# SMS delivery
# ---------------------------------------------------------------------------

def _send_via_twilio(phone_e164: str, message: str):
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER):
        raise HTTPException(
            status_code=500,
            detail="SMS_PROVIDER=twilio but TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER are not all set.",
        )
    resp = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        data={"To": phone_e164, "From": TWILIO_FROM_NUMBER, "Body": message},
        timeout=SMS_TIMEOUT_SECONDS,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Twilio returned {resp.status_code}: {resp.text[:300]}")


def _send_via_msg91(phone_e164: str, message: str, otp_code: str):
    if not (MSG91_AUTH_KEY and MSG91_TEMPLATE_ID):
        raise HTTPException(
            status_code=500,
            detail="SMS_PROVIDER=msg91 but MSG91_AUTH_KEY / MSG91_TEMPLATE_ID are not set.",
        )
    # MSG91's OTP endpoint sends the code through a pre-approved DLT template.
    # The template must contain an ##OTP## variable; the body text above is not
    # what gets delivered -- the registered template is.
    payload = {
        "template_id": MSG91_TEMPLATE_ID,
        "mobile": phone_e164.lstrip("+"),
        "otp": otp_code,
    }
    if MSG91_SENDER_ID:
        payload["sender"] = MSG91_SENDER_ID
    resp = requests.post(
        "https://control.msg91.com/api/v5/otp",
        headers={"authkey": MSG91_AUTH_KEY, "Content-Type": "application/json"},
        json=payload,
        timeout=SMS_TIMEOUT_SECONDS,
    )
    body = resp.text[:300]
    if resp.status_code >= 400 or '"type":"error"' in body.replace(" ", ""):
        raise RuntimeError(f"MSG91 returned {resp.status_code}: {body}")


def _send_via_fast2sms(phone_e164: str, otp_code: str):
    if not FAST2SMS_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="SMS_PROVIDER=fast2sms but FAST2SMS_API_KEY is not set.",
        )
    national = phone_e164.lstrip("+")
    if national.startswith("91") and len(national) == 12:
        national = national[2:]
    if len(national) != 10:
        raise HTTPException(status_code=400, detail="Fast2SMS only delivers to Indian 10-digit numbers.")
    resp = requests.get(
        "https://www.fast2sms.com/dev/bulkV2",
        headers={"authorization": FAST2SMS_API_KEY},
        params={"variables_values": otp_code, "route": "otp", "numbers": national},
        timeout=SMS_TIMEOUT_SECONDS,
    )
    if resp.status_code >= 400 or '"return":false' in resp.text.replace(" ", ""):
        raise RuntimeError(f"Fast2SMS returned {resp.status_code}: {resp.text[:300]}")


def send_sms(phone: str, message: str, otp_code: str = ""):
    """
    Deliver an OTP to a real phone number.

    With SMS_PROVIDER=none this is a no-op and the code is surfaced in the API
    response instead (local dev only). With a provider configured, a delivery
    failure raises -- the caller must not pretend the code was sent.
    """
    if SMS_PROVIDER == "none":
        return

    phone_e164 = to_e164(phone)
    try:
        if SMS_PROVIDER == "twilio":
            _send_via_twilio(phone_e164, message)
        elif SMS_PROVIDER == "msg91":
            _send_via_msg91(phone_e164, message, otp_code)
        elif SMS_PROVIDER == "fast2sms":
            _send_via_fast2sms(phone_e164, otp_code)
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Unknown SMS_PROVIDER '{SMS_PROVIDER}'. Use none, twilio, msg91 or fast2sms.",
            )
    except HTTPException:
        raise
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach the SMS provider: {exc}")
    except RuntimeError as exc:
        # Provider-side rejection (bad credentials, unverified recipient on a
        # Twilio trial account, DLT template mismatch, no balance...).
        raise HTTPException(status_code=502, detail=f"SMS provider rejected the message: {exc}")


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
# DILRMP / ULPIN alignment
#
# The real Unique Land Parcel Identification Number (ULPIN / "Bhu-Aadhaar")
# is a 14-digit alphanumeric ID issued by NIC's BhuNaksha system from a
# surveyed, geo-referenced shapefile, under the Dept. of Land Resources'
# Digital India Land Records Modernisation Programme (DILRMP). This system
# has no access to that pipeline, so it cannot issue a real one.
#
# What we do instead: generate a 14-character alphanumeric ID in the same
# shape, deterministically derived from this parcel's own centroid
# coordinates (which we already store as a PostGIS polygon), so records are
# structured the way a DILRMP-integrated system expects. If this project is
# ever connected to a real state Bhu-Naksha/NGDRS integration, the true
# ULPIN can simply overwrite this placeholder without changing the schema
# or any code that reads `ulpin`.
# ---------------------------------------------------------------------------

def generate_ulpin_placeholder(lon: float, lat: float, survey_no: str) -> str:
    """14-char alphanumeric, geo-derived, NOT an officially issued ULPIN."""
    geo_part = f"{round(lat, 5)}{round(lon, 5)}"
    digest = hashlib.sha256(f"{geo_part}:{survey_no}".encode()).hexdigest().upper()
    # Keep it visibly a placeholder: prefix "XX" (no real state ever issues
    # this prefix) + 12 chars from the geo-derived hash.
    return f"XX{digest[:12]}"


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
    # DILRMP administrative hierarchy (state/district/tehsil/village LGD-style
    # codes). Optional -- defaults to 'UNK' so this doesn't block existing
    # ingestion flows for staff who don't have these codes handy yet.
    state_code: Optional[str] = None
    district_code: Optional[str] = None
    tehsil_code: Optional[str] = None
    village_code: Optional[str] = None


class DisputeAction(BaseModel):
    reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/api/auth/request-otp")
def request_otp(req: OtpRequest):
    phone = normalize_phone(req.phone)
    if not phone or not PHONE_RE.match(phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number (10-15 digits)")

    _rate_limit(
        _otp_request_log,
        phone,
        OTP_REQUESTS_PER_WINDOW,
        f"Too many OTP requests for this number. Try again in {OTP_RATE_WINDOW_MINUTES} minutes.",
    )

    code = f"{secrets.randbelow(1000000):06d}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=OTP_TTL_MINUTES)

    # Send first, store second. If delivery fails the user gets a clear error
    # and no unusable code is left sitting in the table.
    send_sms(
        phone,
        f"Your Land Registry OTP is {code}. Valid for {OTP_TTL_MINUTES} minutes. Do not share it with anyone.",
        otp_code=code,
    )

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Invalidate any earlier unused codes for this number, so only the
            # most recently texted code works.
            cur.execute(
                "UPDATE otp_codes SET consumed = TRUE WHERE phone = %s AND consumed = FALSE;",
                (phone,),
            )
            cur.execute(
                "INSERT INTO otp_codes (phone, code_hash, expires_at) VALUES (%s, %s, %s);",
                (phone, hash_code(code), expires_at),
            )
            conn.commit()
    finally:
        conn.close()

    response = {
        "status": "OTP_SENT",
        "expires_in_minutes": OTP_TTL_MINUTES,
        "phone": phone,
        "delivery": "sms" if SMS_PROVIDER != "none" else "debug",
    }
    if OTP_DEBUG_MODE:
        # Only reachable while SMS_PROVIDER=none -- see the config block up top.
        response["debug_otp"] = code
    return response


@app.post("/api/auth/verify-otp")
def verify_otp(req: OtpVerify):
    phone = normalize_phone(req.phone)
    if not phone:
        raise HTTPException(status_code=400, detail="Enter a valid phone number")

    _rate_limit(
        _otp_verify_log,
        phone,
        OTP_VERIFY_ATTEMPTS_PER_WINDOW,
        f"Too many incorrect attempts. Request a new OTP in {OTP_RATE_WINDOW_MINUTES} minutes.",
    )

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id FROM otp_codes
                WHERE phone = %s AND code_hash = %s AND consumed = FALSE AND expires_at > NOW()
                ORDER BY id DESC LIMIT 1;
                """,
                (phone, hash_code(req.code.strip())),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=401, detail="Invalid or expired OTP")

            cur.execute("UPDATE otp_codes SET consumed = TRUE WHERE id = %s;", (row["id"],))

            cur.execute(
                """
                INSERT INTO users (phone, name) VALUES (%s, %s)
                ON CONFLICT (phone) DO UPDATE SET name = EXCLUDED.name
                RETURNING phone, name, is_staff, is_policymaker;
                """,
                (phone, req.name.strip()),
            )
            user = cur.fetchone()
            conn.commit()
    finally:
        conn.close()

    # Successful login clears the failed-attempt counter for this number.
    _otp_verify_log.pop(phone, None)

    token = create_token(user["phone"], user["name"], user["is_staff"], user["is_policymaker"])
    return {
        "token": token,
        "phone": user["phone"],
        "name": user["name"],
        "is_staff": user["is_staff"],
        "is_policymaker": user["is_policymaker"],
    }


# ---------------------------------------------------------------------------
# Extraction -- real cloud OCR, but a human always approves before commit
# ---------------------------------------------------------------------------

# A non-exhaustive list of printed field-label text (English + common
# Hindi/Marathi land-record terms) that sometimes gets returned by OCR/vision
# models in place of the actual filled-in answer. This is a safety net, not
# a replacement for the prompt instruction above -- it catches the failure
# even when the model doesn't follow instructions perfectly. Extend this list
# as you encounter more state-specific form labels in practice.
KNOWN_FORM_LABELS = {
    "name", "owner", "owner name", "name of occupant", "occupant name",
    "occupant's name", "name of owner", "father's name", "husband's name",
    "survey no", "survey number", "survey no.", "sr no", "sr. no.", "sr.no",
    "khasra no", "khasra number", "khasra no.", "gat no", "gat number",
    "village", "taluka", "tehsil", "district",
    "नाव", "भोगवटदाराचे नाव", "भोगवटदार", "मालकाचे नाव", "मालक",
    "सर्वे नं", "सर्वे नंबर", "सर्वे क्रमांक", "गट नं", "गट नंबर",
    "गाव", "तालुका", "जिल्हा", "खाते नं", "खाते नंबर",
}


def looks_like_form_label(value: str) -> bool:
    """True if `value` is (or closely matches) a known printed field label
    rather than an actual filled-in answer."""
    if not value:
        return False
    normalized = re.sub(r"[.:\-–—]", "", value).strip().lower()
    if normalized in KNOWN_FORM_LABELS:
        return True
    return any(fuzz.ratio(normalized, label) > 88 for label in KNOWN_FORM_LABELS)


def call_gemini_vision(image_bytes: bytes) -> dict:
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is not configured on the server")

    prompt = """
    Examine this scanned Indian land record document. Extract ONLY what is
    visibly printed or handwritten on it -- never guess or invent a value.

    CRITICAL: Many land record forms print a LABEL for each field (e.g. "Name
    of occupant", "भोगवटदाराचे नाव", "Survey No.", "सर्वे नं.") right next to
    or above the space where the actual answer is written or typed. You must
    return the ANSWER that was filled into that field -- a person's actual
    name, an actual survey number -- never the label text itself. If a field
    looks blank, illegible, or you cannot tell the label apart from the
    filled-in value with confidence, leave that field as an empty string and
    lower confidence_score accordingly. Returning a label as if it were the
    answer is a serious error -- when in doubt, prefer an empty field over a
    guess.

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
    contents = await file.read()

    try:
        extracted = call_gemini_vision(contents)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"OCR extraction failed ({e}). Please enter the details manually.")

    image_hash = compute_perceptual_hash(contents)
    edit_software = check_edit_metadata(contents)

    extraction_warnings = []
    if looks_like_form_label(extracted["owner_name"]):
        extraction_warnings.append(
            f"Extracted owner name ('{extracted['owner_name']}') looks like a printed form "
            f"label, not a filled-in value. This is likely an extraction error -- verify "
            f"against the original document before approving."
        )
    if looks_like_form_label(extracted["survey_no"]):
        extraction_warnings.append(
            f"Extracted survey number ('{extracted['survey_no']}') looks like a printed form "
            f"label, not a filled-in value. This is likely an extraction error -- verify "
            f"against the original document before approving."
        )

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
                or bool(extraction_warnings)
            )

            cur.execute(
                """
                INSERT INTO documents (survey_no, file_name, mime_type, file_bytes, image_hash, authenticity_flags)
                VALUES (%s, %s, %s, %s, %s, %s) RETURNING id;
                """,
                (
                    extracted["survey_no"] or None,
                    file.filename,
                    file.content_type,
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
        "extraction_warnings": extraction_warnings,
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
# Commit -- staff only, always a deliberate human action
# ---------------------------------------------------------------------------

@app.post("/api/commit-record")
def commit_record(req: RecordApprovalRequest, staff=Depends(require_staff)):
    # Same canonical form as login, otherwise the owner logs in as
    # "9743476555" and never sees a parcel filed under "+91 97434 76555".
    owner_phone = normalize_phone(req.owner_phone)
    if not owner_phone or not PHONE_RE.match(owner_phone):
        raise HTTPException(status_code=400, detail="owner_phone must be a valid phone number -- it's how the owner logs in to see this record")

    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_name, ulpin FROM cadastral_parcels WHERE survey_no = %s;", (req.survey_no,))
            existing = cur.fetchone()
            if existing and fuzz.token_sort_ratio(existing["owner_name"].lower(), req.owner_name.lower()) < 70:
                raise HTTPException(status_code=409, detail=f"Ownership dispute on plot {req.survey_no}: currently registered to '{existing['owner_name']}'")

            # A commit against a survey number that already exists is an
            # ownership/detail change to a live parcel -- a MUTATION, in
            # DILRMP terms -- not a fresh REGISTER. This distinction is what
            # the analytics dashboard's "mutation frequency" trend counts.
            action = "MUTATION" if existing else "REGISTER"

            cur.execute("SELECT block_hash FROM audit_ledger ORDER BY block_index DESC LIMIT 1;")
            row = cur.fetchone()
            prev_hash = row["block_hash"] if row else "0" * 64

            block_data = f"{req.survey_no}:{req.owner_name}:{owner_phone}:{req.area_hectares}:{prev_hash}:{staff['phone']}"
            block_hash = hashlib.sha256(block_data.encode()).hexdigest()

            cur.execute(
                """
                INSERT INTO audit_ledger (survey_no, owner_name, owner_phone, area_hectares, action, block_hash, prev_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s);
                """,
                (req.survey_no, req.owner_name, owner_phone, req.area_hectares, action, block_hash, prev_hash),
            )

            wkt = req.wkt_polygon or 'POLYGON((77.5950 12.9700, 77.5970 12.9700, 77.5970 12.9720, 77.5950 12.9720, 77.5950 12.9700))'

            # Preserve an existing ULPIN across mutations (a parcel's identity
            # doesn't change just because it changed hands); generate one only
            # the first time this survey number is committed.
            if existing and existing.get("ulpin"):
                ulpin = existing["ulpin"]
            else:
                cur.execute("SELECT ST_X(ST_Centroid(ST_GeomFromText(%s, 4326))) AS lon, ST_Y(ST_Centroid(ST_GeomFromText(%s, 4326))) AS lat;", (wkt, wkt))
                centroid = cur.fetchone()
                ulpin = generate_ulpin_placeholder(centroid["lon"], centroid["lat"], req.survey_no)

            cur.execute(
                """
                INSERT INTO cadastral_parcels
                    (survey_no, owner_name, owner_phone, area_hectares, parent_plot, document_id, geom,
                     ulpin, state_code, district_code, tehsil_code, village_code)
                VALUES (%s, %s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s, %s, %s, %s, %s)
                ON CONFLICT (survey_no) DO UPDATE
                SET owner_name = EXCLUDED.owner_name,
                    owner_phone = EXCLUDED.owner_phone,
                    area_hectares = EXCLUDED.area_hectares,
                    document_id = EXCLUDED.document_id,
                    ulpin = COALESCE(cadastral_parcels.ulpin, EXCLUDED.ulpin);
                """,
                (
                    req.survey_no, req.owner_name, owner_phone, req.area_hectares, req.parent_plot, req.document_id, wkt,
                    ulpin,
                    (req.state_code or "UNK").strip() or "UNK",
                    (req.district_code or "UNK").strip() or "UNK",
                    (req.tehsil_code or "UNK").strip() or "UNK",
                    (req.village_code or "UNK").strip() or "UNK",
                ),
            )

            if req.document_id:
                cur.execute("UPDATE review_queue SET status = 'APPROVED' WHERE document_id = %s AND status = 'PENDING';", (req.document_id,))
                cur.execute("UPDATE documents SET survey_no = %s WHERE id = %s;", (req.survey_no, req.document_id))

            conn.commit()
            return {"status": "SUCCESS", "block_hash": block_hash, "action": action, "ulpin": ulpin}
    finally:
        conn.close()


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
                "ulpin, dispute_status, state_code, district_code, tehsil_code, village_code, "
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
                "ulpin, dispute_status, "
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
                SELECT d.file_name, d.mime_type, d.file_bytes, p.owner_phone
                FROM documents d LEFT JOIN cadastral_parcels p ON p.document_id = d.id
                WHERE d.id = %s;
                """,
                (document_id,),
            )
            doc = cur.fetchone()
    finally:
        conn.close()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if not user.get("is_staff") and doc["owner_phone"] != user["phone"]:
        raise HTTPException(status_code=403, detail="You are not the registered owner of this document")

    return Response(content=bytes(doc["file_bytes"]), media_type=doc["mime_type"] or "application/octet-stream")


# ---------------------------------------------------------------------------
# Dispute lifecycle -- staff only. Flagging/resolving both write to the
# immutable audit_ledger so the analytics dashboard's disputed-parcels trend
# has a real event timeline to draw from.
# ---------------------------------------------------------------------------

@app.post("/api/parcels/{survey_no}/flag-dispute")
def flag_dispute(survey_no: str, body: DisputeAction, staff=Depends(require_staff)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_name, owner_phone, area_hectares FROM cadastral_parcels WHERE survey_no = %s;", (survey_no,))
            parcel = cur.fetchone()
            if not parcel:
                raise HTTPException(status_code=404, detail=f"No parcel found for survey number {survey_no}")

            cur.execute(
                "UPDATE cadastral_parcels SET dispute_status = 'DISPUTED', disputed_at = NOW(), resolved_at = NULL WHERE survey_no = %s;",
                (survey_no,),
            )

            cur.execute("SELECT block_hash FROM audit_ledger ORDER BY block_index DESC LIMIT 1;")
            prev_hash = cur.fetchone()["block_hash"]
            block_data = f"{survey_no}:DISPUTE_FLAGGED:{body.reason or ''}:{prev_hash}:{staff['phone']}"
            block_hash = hashlib.sha256(block_data.encode()).hexdigest()
            cur.execute(
                """
                INSERT INTO audit_ledger (survey_no, owner_name, owner_phone, area_hectares, action, block_hash, prev_hash)
                VALUES (%s, %s, %s, %s, 'DISPUTE_FLAGGED', %s, %s);
                """,
                (survey_no, parcel["owner_name"], parcel["owner_phone"], parcel["area_hectares"], block_hash, prev_hash),
            )
            conn.commit()
            return {"status": "DISPUTED", "survey_no": survey_no}
    finally:
        conn.close()


@app.post("/api/parcels/{survey_no}/resolve-dispute")
def resolve_dispute(survey_no: str, body: DisputeAction, staff=Depends(require_staff)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT owner_name, owner_phone, area_hectares, dispute_status FROM cadastral_parcels WHERE survey_no = %s;", (survey_no,))
            parcel = cur.fetchone()
            if not parcel:
                raise HTTPException(status_code=404, detail=f"No parcel found for survey number {survey_no}")
            if parcel["dispute_status"] != "DISPUTED":
                raise HTTPException(status_code=409, detail=f"Plot {survey_no} is not currently marked as disputed")

            cur.execute(
                "UPDATE cadastral_parcels SET dispute_status = 'RESOLVED', resolved_at = NOW() WHERE survey_no = %s;",
                (survey_no,),
            )

            cur.execute("SELECT block_hash FROM audit_ledger ORDER BY block_index DESC LIMIT 1;")
            prev_hash = cur.fetchone()["block_hash"]
            block_data = f"{survey_no}:DISPUTE_RESOLVED:{body.reason or ''}:{prev_hash}:{staff['phone']}"
            block_hash = hashlib.sha256(block_data.encode()).hexdigest()
            cur.execute(
                """
                INSERT INTO audit_ledger (survey_no, owner_name, owner_phone, area_hectares, action, block_hash, prev_hash)
                VALUES (%s, %s, %s, %s, 'DISPUTE_RESOLVED', %s, %s);
                """,
                (survey_no, parcel["owner_name"], parcel["owner_phone"], parcel["area_hectares"], block_hash, prev_hash),
            )
            conn.commit()
            return {"status": "RESOLVED", "survey_no": survey_no}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# DILRMP-aligned export -- staff only.
#
# This is NOT a live submission to any government system (no such public API
# exists for a third party to call -- state Bhu-Naksha/NGDRS systems are only
# reachable by the Revenue/Registration departments that run them). What
# this gives you: a JSON payload shaped around DILRMP's actual data model
# (ULPIN, khasra/survey number, administrative hierarchy, ownership,
# geo-coordinates, mutation history) so that if this project is ever piloted
# with a state Revenue Department, the fields it hands over already line up
# with what they expect instead of needing a translation layer built later.
# ---------------------------------------------------------------------------

@app.get("/api/parcels/{survey_no}/dilrmp-export")
def dilrmp_export(survey_no: str, staff=Depends(require_staff)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT survey_no, owner_name, owner_phone, area_hectares, parent_plot, ulpin,
                       state_code, district_code, tehsil_code, village_code, dispute_status,
                       ST_X(ST_Centroid(geom)) AS centroid_lon, ST_Y(ST_Centroid(geom)) AS centroid_lat,
                       ST_AsGeoJSON(geom) AS boundary_geojson, created_at
                FROM cadastral_parcels WHERE survey_no = %s;
                """,
                (survey_no,),
            )
            parcel = cur.fetchone()
            if not parcel:
                raise HTTPException(status_code=404, detail=f"No parcel found for survey number {survey_no}")

            cur.execute(
                "SELECT action, owner_name, area_hectares, block_hash, timestamp FROM audit_ledger "
                "WHERE survey_no = %s ORDER BY timestamp;",
                (survey_no,),
            )
            history = cur.fetchall()
    finally:
        conn.close()

    return {
        "_notice": (
            "DILRMP-aligned export format. The 'ulpin' field is a locally "
            "generated placeholder, not an officially issued Bhu-Aadhaar -- "
            "see code comments in generate_ulpin_placeholder(). This is not "
            "a live submission to any government system."
        ),
        "ulpin": parcel["ulpin"],
        "khasra_survey_no": parcel["survey_no"],
        "administrative_hierarchy": {
            "state_code": parcel["state_code"],
            "district_code": parcel["district_code"],
            "tehsil_code": parcel["tehsil_code"],
            "village_code": parcel["village_code"],
        },
        "ownership": {
            "owner_name": parcel["owner_name"],
            "owner_phone": parcel["owner_phone"],
            "parent_plot": parcel["parent_plot"],
        },
        "area_hectares": float(parcel["area_hectares"]),
        "dispute_status": parcel["dispute_status"],
        "geo": {
            "centroid_lon": parcel["centroid_lon"],
            "centroid_lat": parcel["centroid_lat"],
            "boundary_geojson": json.loads(parcel["boundary_geojson"]) if parcel["boundary_geojson"] else None,
        },
        "mutation_history": history,
        "record_created_at": parcel["created_at"].isoformat() if parcel["created_at"] else None,
    }


# ---------------------------------------------------------------------------
# Policy Analytics -- staff or policymaker. Aggregate counts and trends only;
# never returns owner names, phone numbers, or documents, so a policymaker
# account with no staff access still can't see any citizen's PII.
# ---------------------------------------------------------------------------

@app.get("/api/analytics/overview")
def analytics_overview(_user=Depends(require_analytics_access)):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM cadastral_parcels;")
            total_parcels = cur.fetchone()["n"]

            cur.execute("SELECT COUNT(*) AS n FROM audit_ledger WHERE action = 'REGISTER';")
            total_registrations = cur.fetchone()["n"]

            cur.execute("SELECT COUNT(*) AS n FROM audit_ledger WHERE action = 'MUTATION';")
            total_mutations = cur.fetchone()["n"]

            cur.execute("SELECT COUNT(*) AS n FROM cadastral_parcels WHERE dispute_status = 'DISPUTED';")
            currently_disputed = cur.fetchone()["n"]

            cur.execute("SELECT COUNT(*) AS n FROM cadastral_parcels WHERE dispute_status = 'RESOLVED';")
            resolved_disputes = cur.fetchone()["n"]

            cur.execute(
                "SELECT AVG(EXTRACT(EPOCH FROM (resolved_at - disputed_at)) / 86400.0) AS avg_days "
                "FROM cadastral_parcels WHERE dispute_status = 'RESOLVED' AND resolved_at IS NOT NULL AND disputed_at IS NOT NULL;"
            )
            avg_row = cur.fetchone()
            avg_resolution_days = round(float(avg_row["avg_days"]), 1) if avg_row["avg_days"] is not None else None

            cur.execute("SELECT COUNT(*) AS n FROM review_queue WHERE status = 'PENDING';")
            pending_review = cur.fetchone()["n"]

            return {
                "total_parcels": total_parcels,
                "total_registrations": total_registrations,
                "total_mutations": total_mutations,
                "currently_disputed": currently_disputed,
                "resolved_disputes": resolved_disputes,
                "avg_dispute_resolution_days": avg_resolution_days,
                "pending_review": pending_review,
            }
    finally:
        conn.close()


@app.get("/api/analytics/mutation-trend")
def analytics_mutation_trend(months: int = 12, _user=Depends(require_analytics_access)):
    months = max(1, min(months, 60))
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_char(date_trunc('month', timestamp), 'YYYY-MM') AS month,
                       COUNT(*) FILTER (WHERE action = 'REGISTER') AS registrations,
                       COUNT(*) FILTER (WHERE action = 'MUTATION') AS mutations
                FROM audit_ledger
                WHERE timestamp >= date_trunc('month', NOW()) - (%s || ' months')::interval
                  AND action IN ('REGISTER', 'MUTATION')
                GROUP BY 1 ORDER BY 1;
                """,
                (months,),
            )
            return {"trend": cur.fetchall()}
    finally:
        conn.close()


@app.get("/api/analytics/dispute-trend")
def analytics_dispute_trend(months: int = 12, _user=Depends(require_analytics_access)):
    months = max(1, min(months, 60))
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_char(date_trunc('month', timestamp), 'YYYY-MM') AS month,
                       COUNT(*) FILTER (WHERE action = 'DISPUTE_FLAGGED') AS flagged,
                       COUNT(*) FILTER (WHERE action = 'DISPUTE_RESOLVED') AS resolved
                FROM audit_ledger
                WHERE timestamp >= date_trunc('month', NOW()) - (%s || ' months')::interval
                  AND action IN ('DISPUTE_FLAGGED', 'DISPUTE_RESOLVED')
                GROUP BY 1 ORDER BY 1;
                """,
                (months,),
            )
            return {"trend": cur.fetchall()}
    finally:
        conn.close()


@app.get("/api/analytics/administrative-breakdown")
def analytics_administrative_breakdown(_user=Depends(require_analytics_access)):
    """Parcel and dispute counts grouped by state/district code -- lets a
    policymaker spot where disputes are concentrated without seeing any
    individual owner's details."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT state_code, district_code, COUNT(*) AS total_parcels,
                       COUNT(*) FILTER (WHERE dispute_status = 'DISPUTED') AS disputed_parcels
                FROM cadastral_parcels
                GROUP BY state_code, district_code
                ORDER BY total_parcels DESC;
                """
            )
            return {"breakdown": cur.fetchall()}
    finally:
        conn.close()
# Land Digitization Engine

## Policy Analytics & DILRMP alignment (new)

- **Policy Analytics dashboard.** A new read-only tab (staff see it alongside
  their existing tools; a new `is_policymaker` role sees *only* this tab)
  shows: total parcels, registrations vs. mutations, currently-disputed
  count, average dispute-resolution time, a monthly mutation-frequency
  chart, a monthly disputed-parcels trend, and a breakdown by administrative
  area. It never exposes owner names, phone numbers, or documents -- a
  policymaker account has no way to see individual citizens' data, only
  aggregates. Log in as the seeded policymaker account: phone `8888888888`,
  any name.
- **Mutations are now tracked as their own event type.** Committing a record
  against a survey number that already exists is logged to the audit ledger
  as `MUTATION` rather than `REGISTER` -- this is what the mutation-frequency
  chart counts.
- **Disputes have a lifecycle.** Staff can flag a survey number as disputed
  and later mark it resolved from the "Full Registry" tab; both actions are
  logged to the immutable audit ledger with timestamps, which is what the
  disputed-parcels trend is built from.
- **DILRMP/ULPIN alignment.** Every parcel now carries a 14-character
  alphanumeric ID in the same shape as a real ULPIN ("Bhu-Aadhaar"), plus
  state/district/tehsil/village administrative codes. **Important:** the
  ULPIN here is a locally generated placeholder derived from the parcel's
  own centroid coordinates -- it is *not* an officially issued ULPIN. Only
  NIC's BhuNaksha system, fed by a licensed survey, can issue a real one.
  What this buys you: if a state Revenue Department ever pilots this
  project, the schema and the `/api/parcels/{survey_no}/dilrmp-export`
  endpoint already speak DILRMP's data model (ULPIN, khasra/survey number,
  administrative hierarchy, ownership, geo-coordinates, mutation history),
  so there's no separate translation layer to build later. There is
  currently no public API for a third party to submit records into a real
  state Bhu-Naksha/NGDRS system -- that requires a formal MOU with the
  state, which is outside the scope of what any software project alone can
  do.
- **Already have a running database?** Run `migrate_v2.sql` against it
  instead of wiping your data -- see the instructions at the top of that
  file. Starting fresh instead is also fine: `docker compose down -v` then
  `docker compose up --build` picks up the new `init.sql` automatically.

## What changed from the previous version

- **No more fake extraction results.** The old `frontend.py` returned hardcoded
  fictional owners ("Parashuram Laxman Patil", etc.) whenever OCR failed or as
  a "synthetic test" fallback baked into the real pipeline. That's gone. If
  extraction fails now, the API returns an error and asks for manual entry —
  it never invents a name.
- **Backend and frontend are now actually connected.** Previously
  `requirements.txt` never installed `fastapi`/`uvicorn`, so `backend.py`
  could not run at all — only the Streamlit app was running, against its own
  separate SQLite file. Now there are two services: `api` (FastAPI + Postgres)
  and `app` (Streamlit), talking over HTTP.
- **Phone number + password login**, not OTP. The first time a phone number is used, the app asks for a name and a password (the name is locked in permanently at that point — it's never asked again on later logins). Seeded staff/admin accounts work the same way: they're pre-created with a name and role, and the very first login just asks them to set a password, never to re-enter their name. Backed by JWT sessions as before. Citizens see only parcels registered to their own phone number.
- **No hardcoded secrets.** The Gemini key that was hardcoded in the old
  `frontend.py` must be treated as compromised — revoke it in Google AI
  Studio and generate a fresh one for the `.env` file.

## Document fraud-risk signals (not forgery detection)

Every upload now gets checked for:
- **Duplicate submission** — the exact same photo already on file under a different survey number or owner (a common reuse-fraud pattern).
- **Edited-image metadata** — EXIF data showing the file passed through Photoshop, GIMP, or similar.
- **Visual tamper signs** — the vision model is asked to flag inconsistent fonts, misalignment, a missing/malformed seal, or patched-looking regions.

Any of these forces the document into the review queue regardless of OCR confidence, and staff see the flags directly. **This is not forgery detection and never produces a "real/fake" verdict** — no scanned-image analysis can certify a government document's authenticity; only the issuing state's own verification portal (survey-number lookup, QR code, etc.) can do that. Treat these flags as "look closer here," not as a finding.

## Human review is load-bearing, not optional

OCR confidence never auto-commits a change to who owns land. Every extraction
either goes into a review queue (if confidence is low or fields are missing)
or is shown to staff for confirmation regardless. A staff member always
explicitly reviews and submits `/api/commit-record`. This is intentional:
no OCR system is reliable enough to be the final word on a legal document,
and claiming otherwise would be the most dangerous part of this project.

## Running it

```bash
cp .env.example .env
# edit .env: set JWT_SECRET and GEMINI_API_KEY
docker compose up --build
```

- Streamlit UI: http://localhost:8501
- API docs (Swagger): http://localhost:8000/docs

A seed staff account is created by `init.sql`: phone `9743476555` (admin,
name "Charan") and `7670885520` (admin, "Bhuvana Kruthi"), plus `9999999999`
(regular staff, non-admin). None of these have a password yet — the first
time each logs in, the app asks them to set one. Their name is already
fixed from seeding and is never asked again.

## What's still a stand-in, and what real deployment needs

1. **Password auth has no recovery flow yet.** There's no "forgot password"
   — if someone forgets theirs, an admin currently has no built-in way to
   reset it for them either. That's a real gap for anything beyond a demo;
   add a password-reset endpoint (e.g. admin-triggered reset, or an
   email/SMS-based flow) before relying on this for real users.
2. **OCR accuracy.** Gemini Vision is a real model, not a fake, but no OCR
   system reliably hits 90-99% across every state's document format,
   handwriting, and scan quality. The review-queue threshold
   (`CONFIDENCE_REVIEW_THRESHOLD = 0.85` in `backend.py`) is a starting
   point — expect to tune it against real documents, and expect staff
   review volume to be meaningful, not an edge case.
3. **Staff onboarding/roles.** Right now `is_staff` is a single boolean on
   a user row, set by seeding the database directly. A real deployment
   needs an actual admin flow for granting/revoking staff access, plus an
   audit trail of who reviewed what (the schema has room for this —
   `review_queue` — but there's no per-reviewer attribution wired up yet).
4. **Legal/regulatory review.** This handles real property ownership
   records. Before any real use, it needs sign-off from whoever governs
   land records in your jurisdiction, plus a proper security review
   (penetration testing, data retention policy, backups, disaster
   recovery) — this project gets you a working prototype, not a
   compliant government system.
5. **Real DILRMP/ULPIN integration.** What's here is schema and export-format
   alignment, not a live connection. Getting an actually-issued ULPIN and
   submitting mutations to a state's real land records system requires a
   formal integration/MOU with that state's Revenue Department (and
   typically routes through NGDRS or the state's own portal, e.g. Bhoomi,
   Dharani, Bhulekh). No individual or software project can call that API
   without going through that process.
6. **No rate limiting on login attempts.** `/api/auth/login` currently has
   no limit on password guesses per phone number. Add rate limiting (e.g.
   via a reverse proxy or in-app counter) before exposing this publicly.

> **Note on this document:** the "Policy Analytics & DILRMP alignment"
> section above (Policy Analytics dashboard, `is_policymaker` role, mutation
> tracking, dispute lifecycle, ULPIN export) describes features that are
> **not present in the actual `backend.py`/`frontend.py`** as of this
> update — there's no `is_policymaker` column, no analytics endpoints, no
> dispute/mutation tracking in the real code. This README had drifted ahead
> of what was actually built at some point. If those features matter to
> you, they still need to be built from scratch rather than assumed to
> exist; if they don't, it's worth deleting that section so the docs match
> reality.
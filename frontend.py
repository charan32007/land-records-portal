import os
import requests
import pandas as pd
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

# Pre-filled on the login form so registry staff don't retype it every time.
# It is only a default -- the field stays editable, and any real phone number
# can be entered. Set DEFAULT_LOGIN_PHONE="" to start with a blank field
# (which is what you want on a public citizen-facing deployment).
DEFAULT_LOGIN_PHONE = os.environ.get("DEFAULT_LOGIN_PHONE", "9743476555")
DEFAULT_LOGIN_NAME = os.environ.get("DEFAULT_LOGIN_NAME", "Registry Staff Admin")

st.set_page_config(page_title="Land Records Portal", layout="wide")

if "token" not in st.session_state:
    st.session_state.token = None
    st.session_state.user = None
if "otp_phone" not in st.session_state:
    st.session_state.otp_phone = None
    st.session_state.otp_name = None
if "last_extraction" not in st.session_state:
    st.session_state.last_extraction = None
if "last_extraction_image" not in st.session_state:
    st.session_state.last_extraction_image = None
if "debug_otp" not in st.session_state:
    st.session_state.debug_otp = None
if "otp_delivery" not in st.session_state:
    st.session_state.otp_delivery = "debug"


def auth_headers():
    return {"Authorization": f"Bearer {st.session_state.token}"}


def api_post(path, **kwargs):
    resp = requests.post(f"{API_BASE_URL}{path}", **kwargs)
    if resp.status_code >= 400:
        try:
            st.error(resp.json().get("detail", resp.text))
        except ValueError:
            st.error(resp.text)
        return None
    return resp.json()


def api_get(path, **kwargs):
    resp = requests.get(f"{API_BASE_URL}{path}", **kwargs)
    if resp.status_code >= 400:
        try:
            st.error(resp.json().get("detail", resp.text))
        except ValueError:
            st.error(resp.text)
        return None
    return resp.json()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def login_screen():
    st.title("🏛️ Land Records Portal")
    st.caption("Log in with your phone number to view land parcels registered in your name.")

    if st.session_state.otp_phone is None:
        with st.form("request_otp_form"):
            phone = st.text_input(
                "Phone Number",
                value=DEFAULT_LOGIN_PHONE,
                help="10-digit mobile number. +91 / 0 prefixes and spaces are fine.",
            )
            name = st.text_input("Full Name", value=DEFAULT_LOGIN_NAME)
            if st.form_submit_button("Send OTP") and phone and name:
                result = api_post("/api/auth/request-otp", json={"phone": phone.strip(), "name": name.strip()})
                if result:
                    # Keep the number the API normalized, not the raw typed one,
                    # so verify-otp looks up the same string that was stored.
                    st.session_state.otp_phone = result.get("phone", phone.strip())
                    st.session_state.otp_name = name.strip()
                    st.session_state.debug_otp = result.get("debug_otp")
                    st.session_state.otp_delivery = result.get("delivery", "debug")
                    st.rerun()
    else:
        if st.session_state.get("otp_delivery") == "sms":
            st.success(f"An OTP has been texted to {st.session_state.otp_phone}. It expires in 5 minutes.")
        else:
            st.info(f"Enter the OTP for {st.session_state.otp_phone}")
        if st.session_state.debug_otp:
            st.warning(
                f"DEMO MODE (no SMS provider configured): your OTP is **{st.session_state.debug_otp}**. "
                "Set SMS_PROVIDER in .env to send real texts."
            )
        with st.form("verify_otp_form"):
            code = st.text_input("OTP Code")
            col1, col2, col3 = st.columns(3)
            verify = col1.form_submit_button("Verify & Log In")
            resend = col2.form_submit_button("Resend OTP")
            back = col3.form_submit_button("Use a different number")
            if resend:
                result = api_post("/api/auth/request-otp", json={
                    "phone": st.session_state.otp_phone,
                    "name": st.session_state.otp_name,
                })
                if result:
                    st.session_state.debug_otp = result.get("debug_otp")
                    st.session_state.otp_delivery = result.get("delivery", "debug")
                    st.rerun()
            if verify and code:
                result = api_post("/api/auth/verify-otp", json={
                    "phone": st.session_state.otp_phone,
                    "code": code.strip(),
                    "name": st.session_state.otp_name,
                })
                if result:
                    st.session_state.token = result["token"]
                    st.session_state.user = result
                    st.session_state.otp_phone = None
                    st.rerun()
            if back:
                st.session_state.otp_phone = None
                st.session_state.debug_otp = None
                st.rerun()


if not st.session_state.token:
    login_screen()
    st.stop()

user = st.session_state.user
st.sidebar.success(f"Logged in as {user['name']} ({user['phone']})")
if user.get("is_staff"):
    st.sidebar.caption("Role: Registry Staff")
elif user.get("is_policymaker"):
    st.sidebar.caption("Role: Policy Analytics (read-only, no access to citizen data)")
if st.sidebar.button("Log out"):
    st.session_state.token = None
    st.session_state.user = None
    st.rerun()


# ---------------------------------------------------------------------------
# Citizen view
# ---------------------------------------------------------------------------

def render_my_records():
    st.header("📄 My Land Records")
    data = api_get("/api/my-records", headers=auth_headers())
    if not data:
        return
    parcels = data["parcels"]
    if not parcels:
        st.info("No land parcels are currently registered under your phone number. If this looks wrong, contact registry staff.")
        return
    for p in parcels:
        with st.container(border=True):
            st.subheader(f"Survey No. {p['survey_no']}")
            st.write(f"**Owner:** {p['owner_name']} | **Area:** {p['area_hectares']} hectares")
            if p.get("parent_plot"):
                st.caption(f"Parent plot: {p['parent_plot']}")
            if p.get("document_id"):
                doc_resp = requests.get(f"{API_BASE_URL}/api/documents/{p['document_id']}", headers=auth_headers())
                if doc_resp.status_code == 200:
                    st.download_button(
                        "Download original document",
                        data=doc_resp.content,
                        file_name=f"land_deed_{p['survey_no']}.jpg",
                        key=f"dl_{p['id']}",
                    )


# ---------------------------------------------------------------------------
# Staff views
# ---------------------------------------------------------------------------

def render_ingestion():
    st.header("📥 Ingest New Document")
    uploaded = st.file_uploader("Upload scanned land document", type=["png", "jpg", "jpeg"])
    if uploaded and st.button("Run Extraction"):
        files = {"file": (uploaded.name, uploaded.getvalue(), uploaded.type)}
        result = api_post("/api/extract-and-validate", files=files, headers=auth_headers())
        if result:
            st.session_state.last_extraction = result
            st.session_state.last_extraction_image = uploaded.getvalue()

    result = st.session_state.last_extraction
    if result:
        extracted = result["extracted"]
        confidence = extracted["confidence"]

        extraction_warnings = result.get("extraction_warnings", [])
        if extraction_warnings:
            st.error("⚠️ **Extraction error suspected — a form label may have been read as the actual value.**")
            for w in extraction_warnings:
                st.write(f"- {w}")
            st.caption("Double-check these fields against the original image below before editing/approving.")
            if st.session_state.get("last_extraction_image"):
                st.image(st.session_state.last_extraction_image, caption="Original uploaded document", width=500)

        flags = result.get("authenticity_flags", [])
        tamper_risk = extracted.get("tamper_risk", "low")
        if flags or tamper_risk in ("medium", "high"):
            st.error("🚩 **Authenticity signals detected — not proof of forgery, but review closely before approving.**")
            for f in flags:
                st.write(f"- {f}")
            if tamper_risk in ("medium", "high") and not extracted.get("tamper_signs"):
                st.write(f"- Visual tamper risk assessed as **{tamper_risk}** by the extraction model.")
        else:
            st.caption("✅ No fraud-risk signals detected (duplicate submission, edited-image metadata, or visible tampering). This does not confirm the document is genuine — only the issuing state's own verification portal can do that.")

        if result["needs_review"]:
            st.warning(f"⚠️ Flagged for review ({confidence * 100:.1f}% OCR confidence). Check every field carefully.")
        else:
            st.success(f"Extraction confidence: {confidence * 100:.1f}%. Please still verify — OCR informs, it never decides ownership.")
        for issue in result.get("validation_issues", []):
            (st.error if "DISPUTE" in issue else st.info)(issue)

        with st.form("commit_form"):
            survey_no = st.text_input("Survey / Khasra / Gat Number", value=extracted["survey_no"])
            owner_name = st.text_input("Owner Name", value=extracted["owner_name"])
            owner_phone = st.text_input("Owner Phone Number (required — this is how the owner logs in)")
            area = st.number_input("Area (hectares)", value=float(extracted["area_hectares"] or 0.0), format="%.4f")
            parent_plot = st.text_input("Parent Plot (optional)")
            with st.expander("DILRMP administrative codes (optional)"):
                st.caption("Used for the policy analytics breakdown and the DILRMP-aligned export. Leave blank if unknown.")
                a1, a2 = st.columns(2)
                state_code = a1.text_input("State code")
                district_code = a2.text_input("District code")
                a3, a4 = st.columns(2)
                tehsil_code = a3.text_input("Tehsil/Taluk code")
                village_code = a4.text_input("Village code")
            if st.form_submit_button("Approve & Commit to Ledger"):
                if not owner_phone.strip():
                    st.error("Owner phone number is required so the owner can log in and see this record.")
                else:
                    payload = {
                        "document_id": result["document_id"],
                        "survey_no": survey_no,
                        "owner_name": owner_name,
                        "owner_phone": owner_phone.strip(),
                        "area_hectares": area,
                        "parent_plot": parent_plot or None,
                        "state_code": state_code or None,
                        "district_code": district_code or None,
                        "tehsil_code": tehsil_code or None,
                        "village_code": village_code or None,
                    }
                    commit_result = api_post("/api/commit-record", json=payload, headers=auth_headers())
                    if commit_result:
                        st.success(
                            f"Committed as {commit_result['action']}. Block hash: {commit_result['block_hash']} | "
                            f"ULPIN (placeholder): {commit_result['ulpin']}"
                        )
                        st.session_state.last_extraction = None
                        st.session_state.last_extraction_image = None
                        st.rerun()


def render_review_queue():
    st.header("🔍 Pending Review Queue")
    data = api_get("/api/review-queue", headers=auth_headers())
    if not data:
        return
    if not data["queue"]:
        st.info("No documents pending review.")
        return
    st.dataframe(data["queue"], use_container_width=True)
    st.caption("Re-run these through 'Ingest New Document' with the same file to complete registration once the owner's details are confirmed.")


def render_registry():
    st.header("📊 Full Registry & Ledger")
    data = api_get("/api/records", headers=auth_headers())
    if not data:
        return
    tab1, tab2, tab3 = st.tabs(["Cadastral Parcels", "Audit Ledger", "Disputes & DILRMP Export"])
    with tab1:
        st.dataframe(data["parcels"], use_container_width=True)
    with tab2:
        st.dataframe(data["ledger"], use_container_width=True)
    with tab3:
        st.caption("Flag or resolve a dispute on a survey number, or pull a DILRMP-aligned export for one parcel.")
        col1, col2 = st.columns(2)
        with col1:
            with st.form("dispute_form"):
                dispute_survey_no = st.text_input("Survey Number")
                reason = st.text_area("Reason (optional)")
                flag_col, resolve_col = st.columns(2)
                do_flag = flag_col.form_submit_button("🚩 Flag as Disputed")
                do_resolve = resolve_col.form_submit_button("✅ Mark Resolved")
                if do_flag and dispute_survey_no:
                    result = api_post(f"/api/parcels/{dispute_survey_no.strip()}/flag-dispute", json={"reason": reason}, headers=auth_headers())
                    if result:
                        st.success(f"Plot {dispute_survey_no} flagged as disputed.")
                        st.rerun()
                if do_resolve and dispute_survey_no:
                    result = api_post(f"/api/parcels/{dispute_survey_no.strip()}/resolve-dispute", json={"reason": reason}, headers=auth_headers())
                    if result:
                        st.success(f"Plot {dispute_survey_no} marked resolved.")
                        st.rerun()
        with col2:
            with st.form("dilrmp_export_form"):
                export_survey_no = st.text_input("Survey Number to export")
                if st.form_submit_button("📤 Get DILRMP-aligned export") and export_survey_no:
                    export_data = api_get(f"/api/parcels/{export_survey_no.strip()}/dilrmp-export", headers=auth_headers())
                    if export_data:
                        st.json(export_data)


def render_analytics():
    st.header("📈 Policy Analytics")
    st.caption("Aggregate trends only -- no owner names, phone numbers, or documents are shown here.")

    overview = api_get("/api/analytics/overview", headers=auth_headers())
    if overview:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Parcels", overview["total_parcels"])
        c2.metric("Total Mutations", overview["total_mutations"])
        c3.metric("Currently Disputed", overview["currently_disputed"])
        avg_days = overview.get("avg_dispute_resolution_days")
        c4.metric("Avg. Dispute Resolution", f"{avg_days} days" if avg_days is not None else "N/A")
        c5, c6 = st.columns(2)
        c5.metric("Total Registrations", overview["total_registrations"])
        c6.metric("Pending Review", overview["pending_review"])

    st.divider()
    st.subheader("Mutation Frequency (Registrations vs. Ownership Mutations)")
    months = st.slider("Months of history", min_value=3, max_value=36, value=12, key="mutation_months")
    mutation_data = api_get(f"/api/analytics/mutation-trend?months={months}", headers=auth_headers())
    if mutation_data and mutation_data["trend"]:
        df = pd.DataFrame(mutation_data["trend"]).set_index("month")
        df = df.rename(columns={"registrations": "Registrations", "mutations": "Mutations"})
        st.line_chart(df)
    else:
        st.info("Not enough registration/mutation activity yet to plot a trend.")

    st.divider()
    st.subheader("Disputed Parcels Over Time")
    dispute_months = st.slider("Months of history", min_value=3, max_value=36, value=12, key="dispute_months")
    dispute_data = api_get(f"/api/analytics/dispute-trend?months={dispute_months}", headers=auth_headers())
    if dispute_data and dispute_data["trend"]:
        df2 = pd.DataFrame(dispute_data["trend"]).set_index("month")
        df2 = df2.rename(columns={"flagged": "Newly Flagged", "resolved": "Resolved"})
        st.bar_chart(df2)
    else:
        st.info("No disputes have been flagged yet.")

    st.divider()
    st.subheader("Breakdown by Administrative Area")
    st.caption("Grouped by the state/district codes recorded at ingestion (defaults to 'UNK' until staff fill these in).")
    breakdown = api_get("/api/analytics/administrative-breakdown", headers=auth_headers())
    if breakdown and breakdown["breakdown"]:
        st.dataframe(breakdown["breakdown"], use_container_width=True)
    else:
        st.info("No parcels registered yet.")


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

if user.get("is_staff"):
    tabs = st.tabs(["My Land Records", "Ingest New Document", "Review Queue", "Full Registry", "Policy Analytics"])
    with tabs[0]:
        render_my_records()
    with tabs[1]:
        render_ingestion()
    with tabs[2]:
        render_review_queue()
    with tabs[3]:
        render_registry()
    with tabs[4]:
        render_analytics()
elif user.get("is_policymaker"):
    # Read-only role: only ever sees aggregate analytics, never citizen data.
    render_analytics()
else:
    render_my_records()
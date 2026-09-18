import os
import requests
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

st.set_page_config(page_title="Land Records Portal", layout="wide")

if "token" not in st.session_state:
    st.session_state.token = None
    st.session_state.user = None
if "otp_phone" not in st.session_state:
    st.session_state.otp_phone = None
    st.session_state.otp_name = None
if "last_extraction" not in st.session_state:
    st.session_state.last_extraction = None
if "debug_otp" not in st.session_state:
    st.session_state.debug_otp = None


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
            phone = st.text_input("Phone Number")
            name = st.text_input("Full Name")
            if st.form_submit_button("Send OTP") and phone and name:
                result = api_post("/api/auth/request-otp", json={"phone": phone.strip(), "name": name.strip()})
                if result:
                    st.session_state.otp_phone = phone.strip()
                    st.session_state.otp_name = name.strip()
                    st.session_state.debug_otp = result.get("debug_otp")
                    st.rerun()
    else:
        st.info(f"Enter the OTP sent to {st.session_state.otp_phone}")
        if st.session_state.debug_otp:
            st.warning(f"DEMO MODE (no SMS provider configured): your OTP is **{st.session_state.debug_otp}**")
        with st.form("verify_otp_form"):
            code = st.text_input("OTP Code")
            col1, col2 = st.columns(2)
            verify = col1.form_submit_button("Verify & Log In")
            back = col2.form_submit_button("Use a different number")
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


def render_submit_document():
    st.header("📤 Submit a Land Document")
    st.caption("Upload a photo or scan of your land document. Registry staff will review it and approve the record before it appears under 'My Land Records'.")

    uploaded = st.file_uploader("Upload document", type=["png", "jpg", "jpeg"], key="citizen_upload")
    claimed_survey_no = st.text_input("Survey / Khasra / Gat Number (if you know it)")
    note = st.text_area("Anything staff should know (optional)")
    if uploaded and st.button("Submit for Review"):
        files = {"file": (uploaded.name, uploaded.getvalue(), uploaded.type)}
        data = {"claimed_survey_no": claimed_survey_no, "note": note}
        result = api_post("/api/citizen/submit-document", files=files, data=data, headers=auth_headers())
        if result:
            st.success("Submitted. You'll see it below once staff review it — this can take some time.")
            st.rerun()

    st.divider()
    st.subheader("Your submissions")
    data = api_get("/api/citizen/my-submissions", headers=auth_headers())
    if not data or not data["submissions"]:
        st.info("You haven't submitted any documents yet.")
        return
    status_icon = {"PENDING": "🕒", "APPROVED": "✅", "REJECTED": "❌"}
    for s in data["submissions"]:
        with st.container(border=True):
            st.write(f"{status_icon.get(s['status'], '•')} **{s['status']}** — submitted {s['created_at']}")
            if s.get("claimed_survey_no"):
                st.caption(f"Claimed survey no: {s['claimed_survey_no']}")
            if s["status"] == "REJECTED" and s.get("rejection_reason"):
                st.error(f"Reason: {s['rejection_reason']}")


# ---------------------------------------------------------------------------
# Staff views
# ---------------------------------------------------------------------------

def render_citizen_submissions():
    st.header("👤 Citizen Submissions")
    st.caption("Documents citizens uploaded themselves, waiting on your review and approval.")
    data = api_get("/api/staff/submissions", headers=auth_headers())
    if not data:
        return
    if not data["submissions"]:
        st.info("No citizen submissions pending review.")
        return

    for s in data["submissions"]:
        with st.container(border=True):
            st.subheader(f"Submitted by {s['submitter_name']} ({s['submitter_phone']})")
            st.caption(f"Uploaded {s['created_at']} — file: {s['file_name']}")
            if s.get("claimed_survey_no"):
                st.write(f"Claimed survey no: **{s['claimed_survey_no']}**")
            if s.get("note"):
                st.write(f"Note from citizen: {s['note']}")

            flags = s.get("authenticity_flags") or []
            if flags:
                st.error("🚩 Authenticity signals — review closely, not proof of forgery:")
                for f in flags:
                    st.write(f"- {f}")

            doc_resp = requests.get(f"{API_BASE_URL}/api/documents/{s['document_id']}", headers=auth_headers())
            if doc_resp.status_code == 200:
                st.image(doc_resp.content, width=400)

            with st.form(f"approve_form_{s['submission_id']}"):
                col1, col2 = st.columns(2)
                survey_no = col1.text_input("Survey Number", value=s.get("claimed_survey_no") or "", key=f"sn_{s['submission_id']}")
                owner_name = col2.text_input("Owner Name", value=s["submitter_name"], key=f"on_{s['submission_id']}")
                col3, col4 = st.columns(2)
                area = col3.number_input("Area (hectares)", value=0.0, format="%.4f", key=f"area_{s['submission_id']}")
                parent_plot = col4.text_input("Parent Plot (optional)", key=f"pp_{s['submission_id']}")
                approve = st.form_submit_button("✅ Approve & Register")

                if approve:
                    payload = {
                        "survey_no": survey_no,
                        "owner_name": owner_name,
                        "owner_phone": s["submitter_phone"],
                        "area_hectares": area,
                        "parent_plot": parent_plot or None,
                    }
                    commit_result = api_post(f"/api/staff/submissions/{s['submission_id']}/approve", json=payload, headers=auth_headers())
                    if commit_result:
                        st.success(f"Approved and registered. Block hash: {commit_result['block_hash']}")
                        st.rerun()

            with st.popover("❌ Reject this submission"):
                reason = st.text_area("Reason for rejection", key=f"reason_{s['submission_id']}")
                if st.button("Confirm Rejection", key=f"reject_btn_{s['submission_id']}"):
                    if reason.strip():
                        result = api_post(f"/api/staff/submissions/{s['submission_id']}/reject", json={"reason": reason}, headers=auth_headers())
                        if result:
                            st.rerun()
                    else:
                        st.warning("Please give a reason so the citizen understands why.")


def render_ingestion():
    st.header("📥 Ingest New Document")
    uploaded = st.file_uploader("Upload scanned land document", type=["png", "jpg", "jpeg"])
    if uploaded and st.button("Run Extraction"):
        files = {"file": (uploaded.name, uploaded.getvalue(), uploaded.type)}
        result = api_post("/api/extract-and-validate", files=files, headers=auth_headers())
        if result:
            st.session_state.last_extraction = result

    result = st.session_state.last_extraction
    if result:
        extracted = result["extracted"]
        confidence = extracted["confidence"]

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
                    }
                    commit_result = api_post("/api/commit-record", json=payload, headers=auth_headers())
                    if commit_result:
                        st.success(f"Committed. Block hash: {commit_result['block_hash']}")
                        st.session_state.last_extraction = None
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
    tab1, tab2 = st.tabs(["Cadastral Parcels", "Audit Ledger"])
    with tab1:
        st.dataframe(data["parcels"], use_container_width=True)
    with tab2:
        st.dataframe(data["ledger"], use_container_width=True)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

if user.get("is_staff"):
    tabs = st.tabs(["My Land Records", "Citizen Submissions", "Ingest New Document", "Review Queue", "Full Registry"])
    with tabs[0]:
        render_my_records()
    with tabs[1]:
        render_citizen_submissions()
    with tabs[2]:
        render_ingestion()
    with tabs[3]:
        render_review_queue()
    with tabs[4]:
        render_registry()
else:
    tabs = st.tabs(["My Land Records", "Submit a Document"])
    with tabs[0]:
        render_my_records()
    with tabs[1]:
        render_submit_document()
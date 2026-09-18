import os
from datetime import datetime
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


def time_ago(iso_str):
    """Turns an ISO timestamp string from the API into 'Xm ago' style text."""
    if not iso_str:
        return "Never"
    try:
        dt = datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return str(iso_str)
    seconds = (datetime.utcnow() - dt).total_seconds()
    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours}h ago"
    return f"{int(hours // 24)}d ago"


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
    role_label = user.get("role") or "Registry Staff"
    if user.get("is_admin"):
        role_label += " · Admin"
    st.sidebar.caption(f"Role: {role_label}")
if st.sidebar.button("Log out"):
    api_post("/api/auth/logout", json={}, headers=auth_headers())
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
        with st.spinner("Uploading and analyzing your document... this can take 10-30 seconds (longer if the server just woke up from idle)."):
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

    scope = "mine"
    if user.get("is_admin"):
        view_choice = st.radio(
            "View",
            ["My queue", "All submissions (reassign)"],
            horizontal=True,
            key="submissions_view_choice",
        )
        scope = "mine" if view_choice == "My queue" else "all"
        if scope == "all" and st.button("↻ Auto-assign any unassigned submissions"):
            result = api_post("/api/admin/rebalance-unassigned", json={}, headers=auth_headers())
            if result:
                st.success(f"Assigned {result['reassigned_count']} previously unassigned submission(s).")
                st.rerun()

    st.caption(
        "Documents citizens uploaded themselves, waiting on review and approval."
        if scope == "mine"
        else "Every pending submission across all staff — reassign as needed."
    )
    data = api_get(f"/api/staff/submissions?scope={scope}", headers=auth_headers())
    if not data:
        return
    if not data["submissions"]:
        st.info("No citizen submissions pending review." if scope == "mine" else "No pending submissions at all.")
        return

    active_staff = None
    if scope == "all":
        roster = api_get("/api/staff/list-active", headers=auth_headers())
        active_staff = roster["staff"] if roster else []

    for s in data["submissions"]:
        with st.container(border=True):
            st.subheader(f"Submitted by {s['submitter_name']} ({s['submitter_phone']})")
            st.caption(f"Uploaded {s['created_at']} — file: {s['file_name']}")
            if scope == "all":
                assigned_label = s.get("assigned_to_name") or s.get("assigned_to") or "Unassigned"
                st.caption(f"Assigned to: **{assigned_label}**")
                staff_names = [f"{st_['name']} ({st_['phone']})" for st_ in active_staff]
                staff_phones = [st_["phone"] for st_ in active_staff]
                current_idx = staff_phones.index(s["assigned_to"]) if s.get("assigned_to") in staff_phones else 0
                col_a, col_b = st.columns([3, 1])
                new_choice = col_a.selectbox(
                    "Reassign to", staff_names, index=current_idx, key=f"reassign_select_{s['submission_id']}", label_visibility="collapsed"
                )
                if col_b.button("Reassign", key=f"reassign_btn_{s['submission_id']}"):
                    new_phone = staff_phones[staff_names.index(new_choice)]
                    result = api_post(
                        f"/api/staff/submissions/{s['submission_id']}/reassign",
                        json={"assigned_to": new_phone},
                        headers=auth_headers(),
                    )
                    if result:
                        st.success("Reassigned.")
                        st.rerun()
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


def render_manage_staff():
    st.header("🧑‍💼 Manage Staff")
    st.caption("Add colleagues who need staff access (reviewing submissions, approving records).")

    with st.form("add_staff_form"):
        col1, col2 = st.columns(2)
        phone = col1.text_input("Phone Number")
        name = col2.text_input("Full Name")
        role = st.text_input("Role / Title (optional)", placeholder="e.g. Approval Manager, Registry Clerk")
        if st.form_submit_button("Add Staff Member"):
            if phone.strip() and name.strip():
                result = api_post("/api/staff/add", json={"phone": phone.strip(), "name": name.strip(), "role": role.strip() or None}, headers=auth_headers())
                if result:
                    st.success(f"Added {result['staff']['name']} ({result['staff']['phone']}) as staff.")
                    st.rerun()
            else:
                st.warning("Phone number and name are both required.")

    st.divider()
    st.subheader("Current staff")
    st.caption("Toggle Admin or Staff Access, or edit someone's role, then click Save Changes below. Unchecking 'Staff Access' removes their access entirely.")
    data = api_get("/api/staff/list", headers=auth_headers())
    if data and data["staff"]:
        original = data["staff"]
        editable_rows = [
            {
                "phone": s["phone"],
                "name": s["name"],
                "role": s.get("role") or "",
                "is_admin": s["is_admin"],
                "is_staff": s["is_staff"],
            }
            for s in original
        ]
        edited_rows = st.data_editor(
            editable_rows,
            use_container_width=True,
            hide_index=True,
            disabled=["phone", "name"],
            column_config={
                "phone": st.column_config.TextColumn("Phone"),
                "name": st.column_config.TextColumn("Name"),
                "role": st.column_config.TextColumn("Role / Title"),
                "is_admin": st.column_config.CheckboxColumn("Admin"),
                "is_staff": st.column_config.CheckboxColumn("Staff Access"),
            },
            key="staff_editor",
        )

        if st.button("Save Changes"):
            original_by_phone = {s["phone"]: s for s in original}
            changed_any = False
            for row in edited_rows:
                before = original_by_phone.get(row["phone"])
                if not before:
                    continue
                if (before["role"] or "") != row["role"] or before["is_admin"] != row["is_admin"] or before["is_staff"] != row["is_staff"]:
                    changed_any = True
                    result = api_post(
                        "/api/staff/update",
                        json={
                            "phone": row["phone"],
                            "is_staff": row["is_staff"],
                            "is_admin": row["is_admin"],
                            "role": row["role"],
                        },
                        headers=auth_headers(),
                    )
                    if result:
                        st.success(f"Updated {row['name']} ({row['phone']}).")
            if not changed_any:
                st.info("No changes to save.")
            else:
                st.rerun()


def render_staff_attendance():
    import datetime as _dt

    st.header("🟢 Staff Attendance")
    st.caption(
        "Live online/offline status, plus login and logout history. 'Online' reflects "
        "recent activity in the app — a staff member idle for a while with the tab still "
        "open will eventually show offline again."
    )

    data_now = api_get("/api/admin/staff-attendance", headers=auth_headers())
    if not data_now:
        return

    st.subheader("Live status")
    for r in data_now["staff"]:
        dot = "🟢" if r["effective_online"] else "🔴"
        status_text = "Online now" if r["effective_online"] else f"Last seen {time_ago(r['last_seen_at'])}"
        col1, col2 = st.columns([3, 2])
        col1.write(f"{dot} **{r['name']}** ({r['phone']}) — {r.get('role') or 'Staff'}")
        col2.caption(status_text)

    st.divider()
    st.subheader("Login / logout history")
    selected_date = st.date_input("Date", value=_dt.date.today(), max_value=_dt.date.today(), key="attendance_date")
    data = api_get(f"/api/admin/staff-attendance?date={selected_date.isoformat()}", headers=auth_headers())
    if not data:
        return

    history_rows = []
    for r in data["staff"]:
        if not r["sessions"]:
            history_rows.append({"Name": r["name"], "Phone": r["phone"], "Login": "—", "Logout": "—"})
        else:
            for sess in r["sessions"]:
                history_rows.append(
                    {
                        "Name": r["name"],
                        "Phone": r["phone"],
                        "Login": sess["login_at"],
                        "Logout": sess["logout_at"] or "Not logged out yet",
                    }
                )
    st.dataframe(history_rows, use_container_width=True, hide_index=True)


def render_staff_progress():
    import datetime as _dt

    st.header("📈 Staff Progress")
    st.caption("How many submissions each staff member has approved or rejected, day by day.")

    selected_date = st.date_input("Date", value=_dt.date.today(), max_value=_dt.date.today())
    data = api_get(f"/api/admin/staff-progress?date={selected_date.isoformat()}", headers=auth_headers())
    if not data:
        return
    rows = data["staff"]
    if not rows:
        st.info("No staff on record yet.")
        return

    display_rows = [
        {
            "Name": r["name"],
            "Phone": r["phone"],
            "Role": r.get("role") or "",
            "Approved": r["approved_today"],
            "Rejected": r["rejected_today"],
            "Currently pending": r["pending_now"],
        }
        for r in rows
    ]
    st.dataframe(display_rows, use_container_width=True, hide_index=True)

    totals_col1, totals_col2, totals_col3 = st.columns(3)
    totals_col1.metric("Total approved", sum(r["approved_today"] for r in rows))
    totals_col2.metric("Total rejected", sum(r["rejected_today"] for r in rows))
    totals_col3.metric("Total still pending", sum(r["pending_now"] for r in rows))

    chart_data = {r["name"]: r["approved_today"] + r["rejected_today"] for r in rows}
    if any(chart_data.values()):
        st.caption("Documents actioned per staff member on this date")
        st.bar_chart(chart_data)


def render_ingestion():
    st.header("📥 Ingest New Document")
    uploaded = st.file_uploader("Upload scanned land document", type=["png", "jpg", "jpeg"])
    if uploaded and st.button("Run Extraction"):
        files = {"file": (uploaded.name, uploaded.getvalue(), uploaded.type)}
        with st.spinner("Analyzing document... this can take 10-30 seconds (longer if the server just woke up from idle)."):
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
    tab_names = ["My Land Records", "Citizen Submissions", "Ingest New Document", "Review Queue", "Full Registry"]
    if user.get("is_admin"):
        tab_names.extend(["Staff Attendance", "Staff Progress", "Manage Staff"])
    tabs = st.tabs(tab_names)
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
    if user.get("is_admin"):
        with tabs[5]:
            render_staff_attendance()
        with tabs[6]:
            render_staff_progress()
        with tabs[7]:
            render_manage_staff()
else:
    tabs = st.tabs(["My Land Records", "Submit a Document"])
    with tabs[0]:
        render_my_records()
    with tabs[1]:
        render_submit_document()
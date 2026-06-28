"""
pages_internal/manhour_portal.py — Man-Hour & Labor Tracking (workstream §2Z)
=============================================================================
Standalone page exact-locked to {hod, admin} (mirrors the Material Estimator
lock). Tracks LABOR the way the SME tracks material.

Five tabs:
  👥 Employees        — per-site labor roster CRUD (OWN/Supply + Company)
  🕒 Daily Timesheet  — Excel upload (attendance .xlsx) + manual per-day batch
                        grid; team-SQM entry with even/by-hours distribution
  📐 Estimator        — required man-hours per Location/Equipment/System
  📊 Estimate vs Actual — variance dashboard + over-consumption reason capture
  🧑‍🔧 Employee-wise   — where each worker worked, date by date (neat)

Isolation contract: writes ONLY mh_* tables; reads sme_equipment / sme_recipe
READ-ONLY for the Equipment-Tag / Location / System-Code dropdowns. Never
touches the SME drop-in, the material ledger, or the EOD path. Site-scoped:
HOD is locked to their own site; Admin gets a sidebar site picker.
"""
from __future__ import annotations

import datetime

import pandas as pd
import streamlit as st

import database as D
from ui_components import render_brand_header, render_empty_state


# ---------------------------------------------------------------------------
# Shared dropdown data (READ-ONLY against the frozen SME tables)
# ---------------------------------------------------------------------------
def _sme_dropdowns(site_id: str):
    """Return (equipment_tags, tag→location map, system_codes) from sme_*."""
    try:
        eq = D.get_sme_equipment(site_id=site_id)
    except Exception:
        eq = pd.DataFrame()
    tags, tag_loc = [], {}
    if not eq.empty and "Equipment_Tag_No." in eq.columns:
        eq = eq.dropna(subset=["Equipment_Tag_No."])
        tags = sorted(eq["Equipment_Tag_No."].astype(str).unique())
        for _, r in eq.iterrows():
            tag_loc.setdefault(str(r["Equipment_Tag_No."]),
                               str(r.get("Location", "") or ""))
    try:
        rc = D.get_sme_recipe()
        codes = sorted(rc["Lining_System_Code"].dropna().astype(str).unique()) \
            if not rc.empty else []
    except Exception:
        codes = []
    return tags, tag_loc, codes


# ---------------------------------------------------------------------------
# Tab: 👥 Employees
# ---------------------------------------------------------------------------
def _employees_tab(user: dict, site_id: str) -> None:
    actor = user.get("username", "system")
    st.subheader(f"👥 Labor Roster · {site_id}")

    with st.form("mh_add_emp", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        code = c1.text_input("Employee Code *")
        name = c2.text_input("Name *")
        designation = c3.text_input("Designation")
        c4, c5 = st.columns(2)
        worker_type = c4.radio("Type *", ["OWN", "Supply"], horizontal=True)
        company = c5.text_input("Company", value="GI" if worker_type == "OWN" else "")
        if st.form_submit_button("➕ Add / Update Employee", type="primary"):
            ok, msg = D.upsert_mh_employee(
                site_id, code, name, designation=designation,
                worker_type=worker_type, company=company, created_by=actor)
            (st.success if ok else st.error)(msg)

    df = D.list_mh_employees(site_id)
    if df.empty:
        render_empty_state("👷", "No employees yet",
                           "Add workers above, or import an attendance file in the "
                           "Daily Timesheet tab.")
        return
    st.caption(f"{len(df)} employees ({(df['Worker_Type'] == 'Supply').sum()} Supply)")
    st.dataframe(
        df[["Employee_Code", "Name", "Designation", "Worker_Type", "Company", "status"]],
        use_container_width=True, hide_index=True)

    with st.expander("✏️ Change a worker's status"):
        opts = {f"{r.Employee_Code} — {r.Name}": int(r.id) for r in df.itertuples()}
        pick = st.selectbox("Employee", list(opts), key="mh_emp_status_pick")
        new_status = st.radio("Status", ["active", "inactive"], horizontal=True,
                              key="mh_emp_status_val")
        if st.button("Apply status"):
            if D.set_mh_employee_status(opts[pick], new_status):
                st.success("Updated."); st.rerun()


# ---------------------------------------------------------------------------
# Tab: 🕒 Daily Timesheet
# ---------------------------------------------------------------------------
def _timesheet_tab(user: dict, site_id: str) -> None:
    actor = user.get("username", "system")
    tags, tag_loc, codes = _sme_dropdowns(site_id)

    # ---- Excel upload ------------------------------------------------------
    with st.expander("📤 Upload attendance Excel (to_john_Attendance format)", expanded=False):
        up = st.file_uploader("Attendance .xlsx", type=["xlsx"], key="mh_upload")
        if up is not None:
            try:
                parsed = D.parse_attendance_workbook(up)
            except Exception as e:
                st.error(f"Could not parse the workbook: {e}")
                parsed = None
            if parsed:
                d = parsed["dates"]
                st.info(f"Parsed **{len(parsed['employees'])}** employees and "
                        f"**{len(parsed['timesheets'])}** rows"
                        + (f" · {d[0]} → {d[-1]}" if d else ""))
                prev = pd.DataFrame(parsed["timesheets"])
                if not prev.empty:
                    st.dataframe(prev.head(8), use_container_width=True, hide_index=True)
                mode = st.radio(
                    "On import", ["Replace rows for these dates", "Append"],
                    horizontal=True, key="mh_up_mode",
                    help="Replace deletes this site's existing timesheets for the "
                         "dates in the file, then inserts (predictable re-import).")
                if st.button(f"⬆️ Import to {site_id}", type="primary"):
                    emp_n, ts_n = D.import_mh_attendance(
                        site_id, parsed,
                        replace=mode.startswith("Replace"), created_by=actor)
                    st.success(f"Imported {emp_n} employees, {ts_n} timesheet rows. "
                               "Assign Location/Equipment/System below as needed.")

    st.divider()

    # ---- Manual per-day batch grid ----------------------------------------
    st.subheader("🕒 Manual entry · per-day batch")
    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.2, 0.8])
    work_date = c1.date_input("Work date", value=datetime.date.today(),
                              key="mh_ts_date").isoformat()
    tag = c2.selectbox("Equipment Tag", ["—"] + tags, key="mh_ts_tag")
    system_code = c3.selectbox("System Code", ["—"] + codes, key="mh_ts_sys")
    break_mins = c4.number_input("Break (min)", 0, 240, 60, 15, key="mh_ts_break")
    location = tag_loc.get(tag, "") if tag != "—" else ""
    if location:
        st.caption(f"📍 Location (from SME equipment): **{location}**")

    roster = D.list_mh_employees(site_id, status="active")
    if roster.empty:
        render_empty_state("👷", "No active workers",
                           "Add workers in the Employees tab first.")
    else:
        grid = pd.DataFrame({
            "Worked": False,
            "Employee_Code": roster["Employee_Code"],
            "Name": roster["Name"],
            "In_Time": "07:30",
            "Out_Time": "16:30",
        })
        edited = st.data_editor(
            grid, key="mh_ts_grid", use_container_width=True, hide_index=True,
            disabled=["Employee_Code", "Name"],
            column_config={"Worked": st.column_config.CheckboxColumn(width="small")})
        if st.button("💾 Save timesheet rows", type="primary"):
            if tag == "—" or system_code == "—":
                st.warning("Pick an Equipment Tag and System Code first.")
            else:
                saved = 0
                for r in edited.itertuples():
                    if not r.Worked:
                        continue
                    ok, _ = D.add_mh_timesheet(
                        site_id, r.Employee_Code, work_date, r.In_Time, r.Out_Time,
                        location=location, equipment_tag=tag, system_code=system_code,
                        break_mins=int(break_mins), created_by=actor)
                    saved += int(ok)
                st.success(f"Saved {saved} timesheet row(s) for {work_date}.")

        # ---- Team SQM for this date/tag/system ----------------------------
        with st.expander("📐 Record team SQM completed (auto-distribute)"):
            sc1, sc2 = st.columns(2)
            sqm = sc1.number_input("Team SQM done", 0.0, step=1.0, key="mh_sqm_val")
            method = sc2.selectbox("Distribute", ["even", "by_hours"], key="mh_sqm_method")
            if st.button("Distribute SQM"):
                if tag == "—" or system_code == "—":
                    st.warning("Pick an Equipment Tag and System Code first.")
                else:
                    ok, msg = D.set_mh_production(
                        site_id, work_date, tag, system_code, float(sqm),
                        distribution_method=method, created_by=actor)
                    (st.success if ok else st.error)(msg)

    # ---- Rows already booked for the selected date ------------------------
    st.divider()
    existing = D.list_mh_timesheets(site_id, work_date=work_date)
    st.caption(f"Timesheet rows on {work_date}: {len(existing)}")
    if not existing.empty:
        st.dataframe(
            existing[["Employee_Code", "Location", "Equipment_Tag", "System_Code",
                      "In_Time", "Out_Time", "Total_Hours", "Normal_Hours",
                      "OT_Hours", "Allocated_SQM"]],
            use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Tab: 📐 Estimator
# ---------------------------------------------------------------------------
def _estimator_tab(user: dict, site_id: str) -> None:
    actor = user.get("username", "system")
    tags, tag_loc, codes = _sme_dropdowns(site_id)
    st.subheader(f"📐 Man-Hour Estimator · {site_id}")
    st.caption("Define the REQUIRED man-hours for a scope. Optional Estimated SQM "
               "yields a man-hours-per-SQM norm.")

    with st.form("mh_add_est", clear_on_submit=True):
        c1, c2 = st.columns(2)
        tag = c1.selectbox("Equipment Tag *", ["—"] + tags)
        system_code = c2.selectbox("System Code *", ["—"] + codes)
        c3, c4 = st.columns(2)
        est_mh = c3.number_input("Estimated man-hours *", 0.0, step=1.0)
        est_sqm = c4.number_input("Estimated SQM (optional)", 0.0, step=1.0)
        basis = st.text_input("Basis / notes")
        if st.form_submit_button("💾 Save estimate", type="primary"):
            if tag == "—" or system_code == "—":
                st.warning("Pick an Equipment Tag and System Code.")
            else:
                ok, msg = D.upsert_mh_estimate(
                    site_id, tag, system_code, est_mh,
                    location=tag_loc.get(tag, ""),
                    estimated_sqm=est_sqm or None, basis=basis, created_by=actor)
                (st.success if ok else st.error)(msg)

    df = D.list_mh_estimates(site_id)
    if df.empty:
        render_empty_state("📐", "No estimates yet", "Add a required man-hour figure above.")
    else:
        st.dataframe(df[["Equipment_Tag", "System_Code", "Location",
                         "Estimated_Manhours", "Estimated_SQM", "Basis"]],
                     use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Tab: 📊 Estimate vs Actual
# ---------------------------------------------------------------------------
def _dashboard_tab(user: dict, site_id: str) -> None:
    actor = user.get("username", "system")
    st.subheader(f"📊 Estimate vs Actual · {site_id}")
    df = D.get_mh_estimate_vs_actual(site_id)
    if df.empty:
        render_empty_state("📊", "Nothing to compare yet",
                           "Add estimates (Estimator tab) and log timesheets to "
                           "populate this dashboard.")
        return

    over = df[df["Variance_Manhours"] > 0]
    k1, k2, k3 = st.columns(3)
    k1.metric("Scopes tracked", len(df))
    k2.metric("Over-consuming", len(over))
    k3.metric("Total actual man-hours", round(df["Actual_Manhours"].sum(), 1))

    def _hi(row):
        v = row.get("Variance_Pct")
        color = ""
        if pd.notna(v):
            color = ("background-color:rgba(220,53,69,0.18)" if v > 10
                     else "background-color:rgba(40,167,69,0.15)" if v <= 0 else "")
        return [color] * len(row)

    show = df[["Equipment_Tag", "System_Code", "Location", "Estimated_Manhours",
               "Actual_Manhours", "Variance_Manhours", "Variance_Pct", "SQM_Done",
               "Variance_Reason"]]
    st.dataframe(show.style.apply(_hi, axis=1), use_container_width=True, hide_index=True)

    st.markdown("**Where the most man-hours went**")
    top = df.sort_values("Actual_Manhours", ascending=False).head(5)
    st.dataframe(top[["Equipment_Tag", "System_Code", "Actual_Manhours",
                      "Estimated_Manhours", "Variance_Pct"]],
                 use_container_width=True, hide_index=True)

    with st.expander("📝 Record an over-consumption reason"):
        opts = {f"{r.Equipment_Tag} · {r.System_Code}": (r.Equipment_Tag, r.System_Code)
                for r in df.itertuples()}
        pick = st.selectbox("Scope", list(opts), key="mh_var_pick")
        reason = st.text_area("Reason", key="mh_var_reason")
        if st.button("Save reason"):
            tag, sys_c = opts[pick]
            ok, msg = D.set_mh_variance_reason(site_id, tag, sys_c, reason, entered_by=actor)
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()


# ---------------------------------------------------------------------------
# Tab: 🧑‍🔧 Employee-wise
# ---------------------------------------------------------------------------
def _employee_wise_tab(user: dict, site_id: str) -> None:
    st.subheader(f"🧑‍🔧 Employee-wise · {site_id}")
    roster = D.list_mh_employees(site_id)
    if roster.empty:
        render_empty_state("🧑‍🔧", "No employees yet", "Add workers first.")
        return

    c1, c2, c3 = st.columns([1.6, 1, 1])
    opts = {"— All employees —": None}
    opts.update({f"{r.Employee_Code} — {r.Name}": r.Employee_Code
                 for r in roster.itertuples()})
    pick = c1.selectbox("Employee", list(opts), key="mh_ew_pick")
    d_from = c2.date_input("From", value=datetime.date.today() - datetime.timedelta(days=30),
                           key="mh_ew_from").isoformat()
    d_to = c3.date_input("To", value=datetime.date.today(), key="mh_ew_to").isoformat()

    tl = D.get_mh_employee_timeline(site_id, employee_code=opts[pick],
                                    date_from=d_from, date_to=d_to)
    if tl.empty:
        render_empty_state("🗓️", "No work logged in this window", "")
        return
    st.caption(f"{len(tl)} rows · {round(tl['Total_Hours'].sum(), 1)} man-hours")
    st.dataframe(
        tl[["Employee_Code", "Name", "Work_Date", "Location", "Equipment_Tag",
            "System_Code", "Total_Hours", "OT_Hours", "Allocated_SQM"]],
        use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Page entry point
# ---------------------------------------------------------------------------
def page_manhour_portal(user: dict) -> None:
    """Man-Hours portal. Exact-locked to {hod, admin} in main.py."""
    role = (user.get("role") or "").lower()
    if role == "admin":
        sites = D.get_sites() or ["HQ"]
        site_id = st.sidebar.selectbox("🕒 Man-Hours site", sites, key="_mh_admin_site")
    else:
        site_id = user.get("site_id") or "HQ"

    render_brand_header("🕒 Man-Hours & Labor Tracking")
    tabs = st.tabs(["👥 Employees", "🕒 Daily Timesheet", "📐 Estimator",
                    "📊 Estimate vs Actual", "🧑‍🔧 Employee-wise"])
    with tabs[0]:
        _employees_tab(user, site_id)
    with tabs[1]:
        _timesheet_tab(user, site_id)
    with tabs[2]:
        _estimator_tab(user, site_id)
    with tabs[3]:
        _dashboard_tab(user, site_id)
    with tabs[4]:
        _employee_wise_tab(user, site_id)

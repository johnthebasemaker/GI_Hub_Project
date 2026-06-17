"""
ai/cv/smart_scan.py — Pure helpers for the Returnable Items Smart Scan flow
============================================================================
Stateless logic the Streamlit page calls into. Keeping these pure (no
Streamlit imports) means they're cheap to unit-test in bug_check.

Public helpers
--------------
    bucket_detections(detections)
        Apply Phase 6D's confidence policy and return a (mode, items)
        tuple. mode ∈ {"auto", "candidates", "manual"}.

    lookup_employee_by_qr(qr_payload, *, conn=None) -> dict | None
        Decode-then-lookup wrapper. Returns the matching employees row
        (active only) or None.
"""

from __future__ import annotations


# Thresholds per the Phase 6D spec.
AUTO_CONF_THRESHOLD       = 0.75     # ≥ → auto-fill the manual picker
CANDIDATES_CONF_THRESHOLD = 0.30     # in [0.30, 0.75) → show top-3
MAX_CANDIDATES            = 3


def bucket_detections(detections: list[dict]) -> tuple[str, list[dict]]:
    """Classify a YOLO detection list into one of three UI modes.

    Returns
    -------
    ("auto", [top])
        Top detection ≥ AUTO_CONF_THRESHOLD. UI auto-fills the picker.
    ("candidates", [top, second, third])
        Top detection in [CANDIDATES_CONF_THRESHOLD, AUTO_CONF_THRESHOLD).
        UI presents up to MAX_CANDIDATES rows for the user to pick.
    ("manual", [])
        Empty list, or top detection < CANDIDATES_CONF_THRESHOLD. UI
        falls back to the existing manual entry form.

    Detections are assumed to already be sorted desc by confidence
    (matches what detect_tool() returns).
    """
    if not detections:
        return "manual", []
    top_conf = float(detections[0].get("confidence", 0.0) or 0.0)
    if top_conf >= AUTO_CONF_THRESHOLD:
        return "auto", [detections[0]]
    if top_conf >= CANDIDATES_CONF_THRESHOLD:
        return "candidates", list(detections[:MAX_CANDIDATES])
    return "manual", []


def lookup_employee_by_qr(qr_payload: str, *, conn=None) -> dict | None:
    """Look up the employee record for a decoded QR payload.

    Returns
    -------
    dict
        Row from `employees` (ID_Number, Name, Phone_Number, Department,
        status) if the payload matches an ACTIVE employee.
    None
        Payload empty, no matching ID_Number, or the employee is not in
        `active` status (inactive / suspended employees can't borrow).
    """
    payload = (qr_payload or "").strip()
    if not payload:
        return None

    # Lazy import keeps this module Streamlit-free.
    import sys
    from pathlib import Path
    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from database import get_employee_by_id_number

    emp = get_employee_by_id_number(payload, conn=conn)
    if not emp:
        return None
    if (emp.get("status") or "active") != "active":
        return None
    return emp

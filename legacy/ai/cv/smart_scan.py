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

# Phase 8C — Tier-3 fallback threshold (LocateAnything-3B sidecar).
# Per Phase 8C spec Q1(a): Tier 3 fires ONLY when YOLO returns "manual"
# mode (top conf < CANDIDATES_CONF_THRESHOLD). This constant exists to
# document the boundary the spec talks about ("YOLO confidence < 0.45");
# in the current implementation, anything that lands in `bucket_detections`
# → "manual" is exactly the set we want to forward to LocateAnything.
# Future versions could widen this if SKs find Tier 2's candidates
# unhelpful in the 0.30–0.45 sub-band.
TIER3_TRIGGER_THRESHOLD   = 0.45

# Tier-3 noise floor — LocateAnything detections below this score are
# discarded as "not even worth showing". Mirrors the spirit of
# CANDIDATES_CONF_THRESHOLD for the YOLO path.
TIER3_NOISE_FLOOR         = 0.20


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


# ---------------------------------------------------------------------------
# Phase 8C — Tier-3 LocateAnything fallback helpers (pure logic)
# ---------------------------------------------------------------------------
def should_invoke_tier3(detections: list[dict]) -> bool:
    """Return True when the LocateAnything sidecar should be invoked.

    Per Phase 8C spec Q1(a): Tier 3 fires ONLY when YOLO's top detection
    is in the "manual" mode bucket — i.e. either the detection list is
    empty, or the top confidence is below CANDIDATES_CONF_THRESHOLD.

    Pure check — does NOT call the sidecar, does NOT touch the network.
    The caller is responsible for the gate read + HTTP call.
    """
    if not detections:
        return True
    top_conf = float(detections[0].get("confidence", 0.0) or 0.0)
    return top_conf < CANDIDATES_CONF_THRESHOLD


def tier3_to_candidates(locate_dets: list[dict]) -> list[dict]:
    """Convert LocateAnything's `[{label, box, score}, ...]` shape into
    the same shape `bucket_detections` returns — so the SK Tier-3 panel
    can render the candidates using the same code paths the YOLO panel
    uses without special-casing.

    Output shape (per item):
        {
            "class_name":   str,
            "confidence":   float (0.0..1.0),
            "bbox":         [x1, y1, x2, y2],
            "source":       "tier3_locate_anything",  # provenance tag
        }

    Behaviour:
      - Filters out items with score < TIER3_NOISE_FLOOR.
      - Sorts remaining by score desc.
      - Caps at MAX_CANDIDATES (same ceiling as Tier-2 candidates panel).
      - Items missing `label` or with non-numeric `score` are silently dropped.
    """
    if not locate_dets:
        return []

    cleaned: list[dict] = []
    for d in locate_dets:
        if not isinstance(d, dict):
            continue
        label = (d.get("label") or "").strip()
        if not label:
            continue
        try:
            score = float(d.get("score", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if score < TIER3_NOISE_FLOOR:
            continue
        box = d.get("box") or []
        cleaned.append({
            "class_name": label,
            "confidence": score,
            "bbox":       list(box),
            "source":     "tier3_locate_anything",
        })

    cleaned.sort(key=lambda x: x["confidence"], reverse=True)
    return cleaned[:MAX_CANDIDATES]

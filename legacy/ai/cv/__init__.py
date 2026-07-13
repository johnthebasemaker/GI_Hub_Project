"""ai.cv — Computer-vision helpers (Phase 6).

Phase 6B: QR encode/decode (qr.py).
Phase 6C: YOLOv8 training CLI (train.py) + inference helper (inference.py).
"""

from ai.cv.qr import encode_id_to_png, decode_png_to_id  # noqa: F401
from ai.cv.smart_scan import (  # noqa: F401
    bucket_detections,
    lookup_employee_by_qr,
    AUTO_CONF_THRESHOLD,
    CANDIDATES_CONF_THRESHOLD,
)
# Inference helpers are imported lazily by callers to avoid pulling torch
# into Streamlit/bug_check at module-import time. Do NOT add them here.

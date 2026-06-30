"""
error_handling.py — friendly global error boundary for the Streamlit app.

End users should never see a raw traceback + source code (it's confusing and
leaks internals). Instead:
  • the user sees ONE friendly line with a short reference ID, and
  • the FULL traceback is written to logs/app_errors.log, keyed by that ID, so
    a developer can find and fix it by grepping the ref.

Pair this with `.streamlit/config.toml → [client] showErrorDetails = "none"`
so any error that escapes the boundary also stays quiet on the frontend.

Set the env var GI_DEBUG=1 to additionally show the full traceback inline
while developing.
"""
from __future__ import annotations

import logging
import os
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent / "logs"
_LOG_FILE = _LOG_DIR / "app_errors.log"
_logger: logging.Logger | None = None


def get_error_logger() -> logging.Logger:
    """Lazily build a singleton logger that writes to a rotating file."""
    global _logger
    if _logger is not None:
        return _logger
    try:
        _LOG_DIR.mkdir(exist_ok=True)
    except Exception:
        pass
    lg = logging.getLogger("gi_hub")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    if not lg.handlers:
        try:
            h: logging.Handler = RotatingFileHandler(
                _LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        except Exception:
            # Fall back to stderr if the file can't be opened (read-only FS, etc.)
            h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)s  %(message)s"))
        lg.addHandler(h)
    _logger = lg
    return lg


def is_streamlit_control_flow(exc: BaseException) -> bool:
    """True for st.rerun() / st.stop() control-flow signals, which MUST be
    re-raised (swallowing them would break navigation/auth gates)."""
    return type(exc).__name__ in ("RerunException", "StopException", "RerunData")


def new_error_id() -> str:
    """Short, user-shareable reference that ties the on-screen message to the
    full traceback in the log."""
    return uuid.uuid4().hex[:8].upper()


def debug_enabled() -> bool:
    return os.environ.get("GI_DEBUG", "") == "1"

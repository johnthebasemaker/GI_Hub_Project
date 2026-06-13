"""
whatsapp_worker.py — WhatsApp Queue Processor
==============================================
Sending backend auto-selected at runtime:

  1. Twilio WhatsApp API  (cloud / production)
     Set credentials in Streamlit Secrets (recommended) or env vars:
       [twilio]
       account_sid  = "ACxxxxxxxxxxxxxxxx"
       auth_token   = "your_auth_token"
       from_number  = "whatsapp:+14155238886"   ← Twilio sandbox default

  2. PyWhatKit  (local desktop fallback, Windows/macOS only)
     Requires a browser with WhatsApp Web already logged in.
     Will NOT work on Streamlit Cloud — use Twilio instead.

Running modes:
  • Embedded thread  — called from main.py via run_worker_loop()
  • Standalone       — python whatsapp_worker.py  (local/desktop)
"""

import os
import time
import datetime
import sqlite3

from database import get_connection, queue_whatsapp_alert

# ---------------------------------------------------------------------------
# Lazy pywhatkit loader — module-level import pulls in heavy GUI libs and
# can hang local startup (esp. inside Streamlit's threaded import) for tens
# of seconds. Defer the import to first send so the worker thread starts fast.
# ---------------------------------------------------------------------------
_pywhatkit = None
_PYWHATKIT_TRIED = False


def _load_pywhatkit():
    """Import pywhatkit lazily. Returns the module or None if unavailable."""
    global _pywhatkit, _PYWHATKIT_TRIED
    if _PYWHATKIT_TRIED:
        return _pywhatkit
    _PYWHATKIT_TRIED = True
    try:
        import pywhatkit as _pwk
        _pywhatkit = _pwk
    except Exception:
        _pywhatkit = None
    return _pywhatkit


def _PYWHATKIT_AVAILABLE() -> bool:
    return _load_pywhatkit() is not None


# ---------------------------------------------------------------------------
# Twilio config — reads Streamlit Secrets first, then env vars
# ---------------------------------------------------------------------------
def _twilio_config() -> tuple[str, str, str]:
    """
    Returns (account_sid, auth_token, from_number).
    from_number is the Twilio WhatsApp-enabled number in E.164 format,
    prefixed with 'whatsapp:' e.g. 'whatsapp:+14155238886'.
    """
    _default_from = "whatsapp:+14155238886"
    sid = token = ""
    from_num = _default_from

    # Streamlit Secrets (preferred on cloud)
    try:
        import streamlit as st
        cfg = st.secrets.get("twilio", {})
        sid      = str(cfg.get("account_sid",  "") or "")
        token    = str(cfg.get("auth_token",   "") or "")
        from_num = str(cfg.get("from_number", _default_from) or _default_from)
    except Exception:
        pass

    # Fallback: plain environment variables (local / Docker)
    if not sid:
        sid      = os.environ.get("TWILIO_ACCOUNT_SID", "")
        token    = os.environ.get("TWILIO_AUTH_TOKEN",  "")
        from_num = os.environ.get("TWILIO_FROM_NUMBER", _default_from)

    return sid, token, from_num


# ---------------------------------------------------------------------------
# Unified send — Twilio → PyWhatKit fallback
# ---------------------------------------------------------------------------
def _send_whatsapp(phone: str, text: str) -> None:
    """
    Send one WhatsApp message.  Raises on failure so the caller can mark
    the queue row as 'failed'. Tries Twilio first, then pywhatkit (local).

    The error messages we raise are deliberately specific so they're useful
    in the Admin → WhatsApp Console "error_message" column.
    """
    sid, token, from_num = _twilio_config()

    if sid and token:
        # Twilio path — works on any server, no browser needed
        try:
            from twilio.rest import Client
        except ImportError as e:
            raise RuntimeError(
                f"Twilio configured but `twilio` package missing: {e}. "
                "Run `pip install twilio`."
            ) from e
        to = phone if phone.startswith("whatsapp:") else f"whatsapp:{phone}"
        try:
            Client(sid, token).messages.create(from_=from_num, body=text, to=to)
        except Exception as e:
            # Twilio surfaces the most useful info inside the exception body
            raise RuntimeError(f"Twilio API error: {e}") from e
        return

    pwk = _load_pywhatkit()
    if pwk is None:
        raise RuntimeError(
            "No WhatsApp sender configured. "
            "Locally: keep pywhatkit installed and WhatsApp Web logged in. "
            "On Streamlit Cloud: add Twilio credentials to App Secrets."
        )

    # pywhatkit drives a browser via pyautogui. On macOS that needs
    # Accessibility + Input-Monitoring permissions granted to the Python
    # binary that's running Streamlit; otherwise the keyboard automation
    # silently no-ops and the message is never typed. We can't grant the
    # permission for the user, but we can give them a clear next step.
    import sys, threading
    if threading.current_thread() is not threading.main_thread():
        # pyautogui on macOS requires the main thread for some Cocoa calls.
        # When Streamlit runs the worker via @st.cache_resource, we're in a
        # daemon thread — the browser will open but the keyboard send will
        # often fail silently. Force a clear error rather than a silent drop.
        if sys.platform == "darwin":
            raise RuntimeError(
                "pywhatkit can't run from a Streamlit background thread on "
                "macOS (Cocoa requires main thread). For local desktop usage, "
                "run `python whatsapp_worker.py` in a separate terminal "
                "instead of relying on the embedded thread."
            )

    try:
        pwk.sendwhatmsg_instantly(
            phone_no=phone,
            message=text,
            wait_time=15,
            tab_close=True,
            close_time=3,
        )
    except Exception as e:
        raise RuntimeError(
            f"pywhatkit failed: {type(e).__name__}: {e}. "
            "Check that (1) WhatsApp Web is signed in, "
            "(2) Chrome/Safari is the default browser, "
            "(3) on macOS, the Python binary has Accessibility + "
            "Input Monitoring permissions in System Settings → Privacy."
        ) from e


# ---------------------------------------------------------------------------
# Queue processor — one message per call
# ---------------------------------------------------------------------------
def process_queue() -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] Checking WhatsApp queue…")
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            "SELECT id, phone_number, message FROM whatsapp_queue "
            "WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1"
        )
        row = c.fetchone()
        if not row:
            return

        msg_id, phone, text = row
        print(f"  → Sending #{msg_id} to {phone}")

        # State-lock first: prevents a crash re-queuing the same message
        c.execute(
            "UPDATE whatsapp_queue SET status = 'processing', "
            "attempts = COALESCE(attempts,0) + 1 WHERE id = ?",
            (msg_id,),
        )
        conn.commit()

        try:
            _send_whatsapp(phone, text)
            c.execute(
                "UPDATE whatsapp_queue SET status = 'sent', "
                "sent_at = CURRENT_TIMESTAMP, error_message = NULL WHERE id = ?",
                (msg_id,),
            )
            conn.commit()
            print(f"  ✅ Sent #{msg_id}. Cooling down 10 s…")
            time.sleep(10)
        except Exception as send_err:
            err = f"{type(send_err).__name__}: {send_err}"
            print(f"  ❌ Send failed #{msg_id}: {err}")
            c.execute(
                "UPDATE whatsapp_queue SET status = 'failed', "
                "error_message = ? WHERE id = ?",
                (err[:500], msg_id),
            )
            conn.commit()

    except Exception as e:
        print(f"❌ Worker error: {e}")
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# Overdue returnables — unchanged logic, just tidied
# ---------------------------------------------------------------------------
def check_overdue_returnables() -> None:
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] Checking overdue returnable items…")
    pending_alerts: list[tuple[str, str]] = []
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            SELECT ri.id, ri.material_name, ri.qty, ri.uom, ri.borrower_name,
                   ri.borrower_phone, ri.expected_return_time, ri.Site_ID
            FROM returnable_items ri
            WHERE ri.status = 'borrowed'
              AND ri.expected_return_time < datetime('now', 'localtime')
              AND ri.whatsapp_alert_sent = 0
        """)
        for row in c.fetchall():
            item_id, material, qty, uom, borrower, b_phone, due_time, site_id = row
            sk_msg = (
                f"⚠️ *OVERDUE RETURN ALERT*\n"
                f"📦 {qty} {uom or ''} of '{material}'\n"
                f"👤 Borrower: {borrower}\n"
                f"🕒 Due: {due_time}\n"
                f"Please follow up and mark returned in the portal."
            )
            c.execute(
                "SELECT Phone_Number FROM users "
                "WHERE role='store_keeper' AND Site_ID=?",
                (site_id,),
            )
            for (sk_phone,) in c.fetchall():
                if sk_phone:
                    pending_alerts.append((sk_phone, sk_msg))
            if b_phone:
                pending_alerts.append((
                    b_phone,
                    f"⚠️ *RETURN REMINDER — {material}*\n"
                    f"Dear {borrower}, {qty} {uom or ''} of '{material}' "
                    f"was due back at {due_time}. Please return immediately.",
                ))
            c.execute(
                "UPDATE returnable_items SET whatsapp_alert_sent=1 WHERE id=?",
                (item_id,),
            )
            print(f"  ⚠️ Overdue alert queued: #{item_id} '{material}'")
        conn.commit()
    except Exception as e:
        print(f"❌ Overdue check error: {e}")
    finally:
        if conn:
            conn.close()

    for phone, msg in pending_alerts:
        queue_whatsapp_alert(phone, msg)


# ---------------------------------------------------------------------------
# Worker loop — called by the embedded thread OR standalone script
# ---------------------------------------------------------------------------
def run_worker_loop() -> None:
    """Infinite poll loop. Safe to run as a daemon thread."""
    print("🟢 WhatsApp worker loop started")
    while True:
        try:
            process_queue()
        except Exception as e:
            print(f"❌ process_queue crashed: {e}")
        try:
            check_overdue_returnables()
        except Exception as e:
            print(f"❌ check_overdue crashed: {e}")
        time.sleep(60)


# ---------------------------------------------------------------------------
# Standalone entry point (local desktop use)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 50)
    print("🟢 WhatsApp Background Worker — Standalone Mode")
    sid, _, _ = _twilio_config()
    if sid:
        print("   Sender: Twilio API")
    elif _PYWHATKIT_AVAILABLE():
        print("   Sender: PyWhatKit (ensure WhatsApp Web is open)")
    else:
        print("   ⚠️  No sender configured — messages will be marked failed")
    print("   Press CTRL+C to stop.")
    print("=" * 50)
    run_worker_loop()

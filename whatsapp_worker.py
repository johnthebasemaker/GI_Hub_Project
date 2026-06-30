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
# Provider selection (Workstream C). Default ("" / "auto") preserves the
# existing Twilio→macOS→pywhatkit chain so the Mac demo keeps working unchanged.
# Set WHATSAPP_PROVIDER=meta on the server (docker-compose worker) to use the
# Meta WhatsApp Business Cloud API. pywhatkit is never imported in meta/twilio mode.
# ---------------------------------------------------------------------------
WHATSAPP_PROVIDER = os.environ.get("WHATSAPP_PROVIDER", "").strip().lower()
if WHATSAPP_PROVIDER not in ("", "auto", "meta", "twilio", "pywhatkit"):
    print(f"⚠️  Unknown WHATSAPP_PROVIDER={WHATSAPP_PROVIDER!r}; using default chain")
    WHATSAPP_PROVIDER = ""


def _meta_config() -> tuple[str, str, str]:
    """Returns (phone_number_id, access_token, api_version) for the Meta
    WhatsApp Cloud API, from Streamlit secrets [meta] or META_* env vars."""
    pnid = token = ""
    ver = "v21.0"
    try:
        import streamlit as st
        cfg = st.secrets.get("meta", {})
        pnid  = str(cfg.get("phone_number_id", "") or "")
        token = str(cfg.get("access_token",    "") or "")
        ver   = str(cfg.get("api_version", ver) or ver)
    except Exception:
        pass
    if not pnid:
        pnid = os.environ.get("META_PHONE_NUMBER_ID", "")
    if not token:
        token = os.environ.get("META_ACCESS_TOKEN", "")
    ver = os.environ.get("META_API_VERSION", ver)
    return pnid, token, ver


def _send_via_meta(phone: str, text: str) -> None:
    """Send one WhatsApp message via the Meta Cloud API (Graph). Raises on
    failure so the queue row is marked 'failed' with the API error.

    NOTE: a plain text body only delivers inside the 24-hour customer-service
    window. Business-initiated notifications outside that window require an
    approved message TEMPLATE — that's the next WhatsApp build step.
    """
    import json
    import urllib.error
    import urllib.request

    pnid, token, ver = _meta_config()
    if not pnid or not token:
        raise RuntimeError(
            "WHATSAPP_PROVIDER=meta but META_PHONE_NUMBER_ID / META_ACCESS_TOKEN "
            "are not configured (set them in the env / Docker secret)."
        )
    to = phone.replace("whatsapp:", "").strip().lstrip("+")
    url = f"https://graph.facebook.com/{ver}/{pnid}/messages"
    payload = json.dumps({
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Meta API HTTP {e.code}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"Meta API error: {type(e).__name__}: {e}") from e


# ---------------------------------------------------------------------------
# Unified send — provider router → Twilio → macOS → PyWhatKit
# ---------------------------------------------------------------------------
def _send_via_chrome_macos(phone: str, text: str) -> None:
    """
    Send a WhatsApp message on macOS via the system browser.

    Permission strategy:
      - We deliberately AVOID `tell application "Google Chrome"` because that
        triggers macOS Automation permission ("Python wants to control Chrome")
        which prompts on every send if the binary path varies.
      - Instead, we use ONLY `System Events` for input, which needs the
        Accessibility permission once — granted, then quiet forever.
      - `open -a <browser> URL` and `open -a <browser>` to focus only need
        no permission at all (they're plain LaunchServices calls).

    Browser override: env GI_WHATSAPP_BROWSER (default 'Google Chrome').
    """
    import subprocess
    import time
    import urllib.parse

    browser = os.environ.get("GI_WHATSAPP_BROWSER", "Google Chrome")
    if browser.lower() in {"chrome", "google chrome"}:
        browser = "Google Chrome"
    elif browser.lower() == "safari":
        browser = "Safari"

    clean_phone = phone.lstrip("+").replace(" ", "").replace("-", "")
    url = (
        f"https://web.whatsapp.com/send?phone={clean_phone}"
        f"&text={urllib.parse.quote(text)}"
    )

    # 1. Open the URL in the chosen browser (LaunchServices — no perm needed).
    try:
        subprocess.run(["open", "-a", browser, url], check=True, timeout=10)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"Couldn't open WhatsApp Web in {browser}. "
            f"Is it installed? Try: open -a '{browser}' --args --version. "
            f"Override with GI_WHATSAPP_BROWSER env var. (exit {e.returncode})"
        ) from e
    except FileNotFoundError as e:
        raise RuntimeError(f"`open` command missing — is this macOS? {e}") from e

    # 2. Wait for WhatsApp Web to load + parse the ?text= param into the input.
    wait_s = int(os.environ.get("GI_WHATSAPP_WAIT_S", "15"))
    time.sleep(wait_s)

    # 3. Make sure the target browser is the active app (LaunchServices, no perm).
    subprocess.run(["open", "-a", browser], timeout=5)
    time.sleep(0.5)

    # 4. Press Enter via System Events — Accessibility permission only.
    #    No "tell application <browser>" here, so no Automation prompt.
    enter_osa = 'tell application "System Events" to keystroke return'
    try:
        subprocess.run(
            ["osascript", "-e", enter_osa],
            check=True, timeout=10,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            "AppleScript keystroke failed. Grant the Python binary "
            "Accessibility permission ONCE in:\n"
            "  System Settings → Privacy & Security → Accessibility → add "
            f"{sys_executable()}\n"
            f"(exit {e.returncode})"
        ) from e

    # 5. Let WhatsApp Web actually send, then close the tab via Cmd+W
    #    (System Events keystroke — no Automation permission).
    time.sleep(3)
    close_osa = (
        'tell application "System Events" to keystroke "w" using {command down}'
    )
    try:
        subprocess.run(["osascript", "-e", close_osa], timeout=5)
    except Exception:
        pass  # best-effort


def sys_executable() -> str:
    """Return the path of the Python interpreter the worker is running under.
    Used in error messages so the user knows which binary to whitelist."""
    import sys
    return sys.executable


def _send_whatsapp(phone: str, text: str) -> None:
    """
    Send one WhatsApp message. Raises on failure so the caller can mark
    the queue row as 'failed' with a useful error_message.

    Priority order:
      1. Twilio  — if credentials are configured (any platform)
      2. macOS native — `open -a 'Google Chrome'` + AppleScript Enter key.
         Bypasses pywhatkit. Works from any process / thread.
      3. pywhatkit — legacy fallback on non-macOS desktop (Windows/Linux)
         that has a logged-in WhatsApp Web in the default browser.

    WHATSAPP_PROVIDER overrides this: `meta` → Meta Cloud API only, `twilio` →
    Twilio only, `pywhatkit` → pywhatkit only. Unset/`auto` = the chain below.
    """
    # ── Explicit provider routing (Workstream C) ──────────────────────────
    # `meta` → Meta Cloud API only (the server path). Everything else
    # (twilio / pywhatkit / auto / unset) uses the existing chain below, which
    # already prefers Twilio when creds exist and falls back appropriately.
    if WHATSAPP_PROVIDER == "meta":
        return _send_via_meta(phone, text)

    sid, token, from_num = _twilio_config()

    # ── 1. Twilio (cloud / production) ────────────────────────────────────
    if sid and token:
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
            raise RuntimeError(f"Twilio API error: {e}") from e
        return

    # ── 2. macOS-native Chrome/Safari + AppleScript ───────────────────────
    import sys
    if sys.platform == "darwin":
        _send_via_chrome_macos(phone, text)
        return

    # ── 3. pywhatkit fallback (Windows / Linux desktop) ───────────────────
    pwk = _load_pywhatkit()
    if pwk is None:
        raise RuntimeError(
            "No WhatsApp sender configured. "
            "Cloud: add Twilio credentials. "
            "Windows/Linux desktop: install pywhatkit + log in to WhatsApp Web."
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
            "Confirm WhatsApp Web is signed in and default browser is set."
        ) from e


# ---------------------------------------------------------------------------
# Queue processor — one message per call
# ---------------------------------------------------------------------------
# A transient send failure (sender offline, API hiccup) should not strand a
# message in 'failed' forever. We auto-retry up to MAX_SEND_ATTEMPTS by flipping
# the row back to 'pending'; the 60-second poll loop then re-picks it, giving
# natural spacing without a busy loop. Only after the cap is it terminal-'failed'
# (the admin "Retry all failed" button resets attempts to give a fresh budget).
MAX_SEND_ATTEMPTS = 3


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
            # `attempts` was already incremented during the state-lock above.
            row_at = c.execute(
                "SELECT COALESCE(attempts,0) FROM whatsapp_queue WHERE id = ?",
                (msg_id,),
            ).fetchone()
            attempts = int(row_at[0]) if row_at else MAX_SEND_ATTEMPTS
            if attempts < MAX_SEND_ATTEMPTS:
                # Under the cap → requeue for the next poll (auto-retry).
                next_status = "pending"
                print(f"  ↻ Send failed #{msg_id} (attempt {attempts}/"
                      f"{MAX_SEND_ATTEMPTS}) — requeued: {err}")
            else:
                next_status = "failed"
                print(f"  ❌ Send failed #{msg_id} (attempt {attempts}/"
                      f"{MAX_SEND_ATTEMPTS}) — giving up: {err}")
            c.execute(
                "UPDATE whatsapp_queue SET status = ?, "
                "error_message = ? WHERE id = ?",
                (next_status, err[:500], msg_id),
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
def _maybe_run_delivery_reminders() -> None:
    """Run sweep_delivery_reminders() at most once per local day. The
    UNIQUE constraint inside the helper would block duplicate per-target
    fires, but firing the sweep query itself 1,440 times a day is wasteful.
    We use app_settings.delivery_reminders_last_run as a cheap day-marker."""
    try:
        import datetime as _dt
        import database as _db
        today = _dt.date.today().isoformat()
        conn = _db.get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings "
                "WHERE key='delivery_reminders_last_run'").fetchone()
            if row and row[0] == today:
                return
            n = _db.sweep_delivery_reminders(conn=conn)
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES "
                "  ('delivery_reminders_last_run', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (today,),
            )
            conn.commit()
            if n:
                print(f"📨 Delivery reminders: fired {n} fresh notification(s) for {today}")
        finally:
            conn.close()
    except Exception as e:
        print(f"❌ delivery_reminders crashed: {e}")


def _maybe_run_form_drafts_prune() -> None:
    """Phase 7E — prune expired form_drafts rows at most once per local day.

    Marker key: app_settings.form_drafts_last_prune. Cheaper than running
    the DELETE every 60-sec poll tick; idempotent across worker restarts."""
    try:
        import datetime as _dt
        import database as _db
        today = _dt.date.today().isoformat()
        conn = _db.get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings "
                "WHERE key='form_drafts_last_prune'").fetchone()
            if row and row[0] == today:
                return
            n = _db.prune_expired_form_drafts(conn=conn)
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES "
                "  ('form_drafts_last_prune', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (today,),
            )
            conn.commit()
            if n:
                print(f"🧹 Form drafts prune: removed {n} expired draft(s) on {today}")
        finally:
            conn.close()
    except Exception as e:
        print(f"❌ form_drafts prune crashed: {e}")


def _maybe_run_returnable_reminders() -> None:
    """Run sweep_returnable_reminders() at most once per local HOUR.

    Phase 6E. The sweep is hour-window based so each event must fire on
    the correct hour bucket; calling it more frequently than 1/hour is
    wasteful and could miss boundaries. Marker key:
    `app_settings.returnable_reminders_last_run_hour` stores the current
    "YYYY-MM-DDTHH" stamp. Worker restart mid-hour is safe — same marker
    means no re-fire.
    """
    try:
        import datetime as _dt
        import database as _db
        marker = _dt.datetime.now().strftime("%Y-%m-%dT%H")
        conn = _db.get_connection()
        try:
            row = conn.execute(
                "SELECT value FROM app_settings "
                "WHERE key='returnable_reminders_last_run_hour'").fetchone()
            if row and row[0] == marker:
                return
            n = _db.sweep_returnable_reminders(conn=conn)
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES "
                "  ('returnable_reminders_last_run_hour', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (marker,),
            )
            conn.commit()
            if n:
                print(f"🛠️ Returnable reminders: fired {n} fresh event(s) for {marker}")
        finally:
            conn.close()
    except Exception as e:
        print(f"❌ returnable_reminders crashed: {e}")


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
        # Phase 5 — T-2 / T-1 / T-0 reminders, idempotent within a day
        _maybe_run_delivery_reminders()
        # Phase 6E — Returnable loan reminders, idempotent within an hour
        _maybe_run_returnable_reminders()
        # Phase 7E — Form drafts prune, idempotent within a day
        _maybe_run_form_drafts_prune()
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

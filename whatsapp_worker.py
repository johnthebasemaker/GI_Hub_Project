"""
whatsapp_worker.py — Background Automator
=============================================================
Runs on the background desktop to process the WhatsApp Queue
and overdue returnable-item alerts.
DO NOT run this via Streamlit. Run it via standard Python terminal.
"""

import sqlite3
import time
import datetime
import pywhatkit
import os
from database import get_connection, queue_whatsapp_alert

def process_queue():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Checking WhatsApp Queue...")
    
    try:
        conn = get_connection()
        c = conn.cursor()
        
        # Grab the oldest pending message
        c.execute("SELECT id, phone_number, message FROM whatsapp_queue WHERE status = 'pending' ORDER BY created_at ASC LIMIT 1")
        row = c.fetchone()
        
        if row:
            msg_id, phone, text = row
            print(f"🚀 Found Message #{msg_id} for {phone}. Initiating Web Automation...")

            # State-lock: mark as processing immediately so a crash or retry can't re-queue it
            c.execute("UPDATE whatsapp_queue SET status = 'processing' WHERE id = ?", (msg_id,))
            conn.commit()

            try:
                # Send message instantly (waits 15 seconds for page load, closes tab after 3 seconds)
                pywhatkit.sendwhatmsg_instantly(
                    phone_no=phone,
                    message=text,
                    wait_time=15,
                    tab_close=True,
                    close_time=3
                )

                c.execute("UPDATE whatsapp_queue SET status = 'sent', sent_at = CURRENT_TIMESTAMP WHERE id = ?", (msg_id,))
                conn.commit()
                print(f"✅ Successfully dispatched Message #{msg_id}. Cooling down for 10 seconds...")
                time.sleep(10)  # Anti-ban cooldown between messages

            except Exception as send_err:
                print(f"❌ Send failed for Message #{msg_id}: {send_err}")
                c.execute("UPDATE whatsapp_queue SET status = 'failed' WHERE id = ?", (msg_id,))
                conn.commit()

    except Exception as e:
        print(f"❌ Worker Error: {e}")
    finally:
        conn.close()

def check_overdue_returnables():
    """
    Scans returnable_items for items that are overdue and haven't had an alert
    dispatched yet. Queues a WhatsApp message to the site's Store Keeper(s) and
    to the borrower (if a phone number was recorded), then sets whatsapp_alert_sent=1
    to prevent repeat alerts.
    """
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Checking overdue returnable items...")
    conn = None
    # Collect alerts in memory — populated inside try, queued after conn is closed.
    pending_alerts = []
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
        overdue_rows = c.fetchall()

        for row in overdue_rows:
            (item_id, material, qty, uom, borrower, borrower_phone,
             due_time, site_id) = row

            sk_alert = (
                f"⚠️ *OVERDUE RETURN ALERT*\n"
                f"📦 Item: {qty} {uom or ''} of '{material}'\n"
                f"👤 Borrower: {borrower}\n"
                f"🕒 Was due back at: {due_time}\n\n"
                f"Please follow up immediately and mark the item returned in the Store Keeper Portal."
            )

            c.execute(
                "SELECT Phone_Number FROM users WHERE role = 'store_keeper' AND Site_ID = ?",
                (site_id,)
            )
            for (sk_phone,) in c.fetchall():
                if sk_phone:
                    pending_alerts.append((sk_phone, sk_alert))

            if borrower_phone:
                borrower_msg = (
                    f"⚠️ *RETURN REMINDER — {material}*\n"
                    f"Dear {borrower}, you have an overdue item: "
                    f"{qty} {uom or ''} of '{material}' that was due back at {due_time}. "
                    f"Please return it to the site store immediately. Thank you."
                )
                pending_alerts.append((borrower_phone, borrower_msg))

            c.execute(
                "UPDATE returnable_items SET whatsapp_alert_sent = 1 WHERE id = ?",
                (item_id,)
            )
            print(f"  ⚠️ Queued overdue alert for item #{item_id} ('{material}', borrower: {borrower})")

        conn.commit()

    except Exception as e:
        print(f"❌ Overdue check error: {e}")
    finally:
        # Always release the connection — prevents a dangling RESERVED lock
        # that would block mark_item_returned and process_queue.
        if conn:
            conn.close()

    # Queue AFTER conn is closed — no competing write connection is open.
    for phone, msg in pending_alerts:
        queue_whatsapp_alert(phone, msg)


if __name__ == "__main__":
    print("==================================================")
    print("🟢 WhatsApp Background Worker Started")
    print("Ensure WhatsApp Web is logged into your default browser.")
    print("Press CTRL+C to terminate.")
    print("==================================================")

    # Infinite loop checking every 60 seconds
    while True:
        process_queue()
        try:
            check_overdue_returnables()
        except Exception as ov_err:
            print(f"❌ Overdue check crashed unexpectedly: {ov_err}")
        time.sleep(60)
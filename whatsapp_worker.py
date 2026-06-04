"""
whatsapp_worker.py — Background Automator
=============================================================
Runs on the background desktop to process the WhatsApp Queue.
DO NOT run this via Streamlit. Run it via standard Python terminal.
"""

import sqlite3
import time
import datetime
import pywhatkit
import os
from database import get_connection

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

if __name__ == "__main__":
    print("==================================================")
    print("🟢 WhatsApp Background Worker Started")
    print("Ensure WhatsApp Web is logged into your default browser.")
    print("Press CTRL+C to terminate.")
    print("==================================================")
    
    # Infinite loop checking every 60 seconds
    while True:
        process_queue()
        time.sleep(60)
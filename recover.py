import sqlite3
from auth import hash_password

def recover_admin():
    print("Attempting to recover admin account...")
    conn = sqlite3.connect("gi_database.db")
    c = conn.cursor()
    
    # Generate a fresh, valid bcrypt hash for 'admin2026'
    new_hash = hash_password("admin2026")
    
    # Force update the database
    c.execute("UPDATE users SET password_hash = ? WHERE username = 'admin'", (new_hash,))
    
    if c.rowcount > 0:
        print("✅ Success! Admin password has been reset to: admin2026")
    else:
        print("❌ Error: Could not find 'admin' user in the database.")
        
    conn.commit()
    conn.close()

if __name__ == "__main__":
    recover_admin()
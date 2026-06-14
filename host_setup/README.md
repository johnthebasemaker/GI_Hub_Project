# `host_setup/` — Path A deployment for GI Hub

Self-host on a Mac + Cloudflare Tunnel + Cloudflare Access (email allow-list).
Domain: **`giinventory.com`** (already on your Cloudflare account).
Target URL: **`https://gi.giinventory.com`**.
Allowed sign-in: anyone with an `@generalindustries.net` email.

This folder ships everything you need:

```
host_setup/
├── launchd/                              # 4 launchd .plist templates
│   ├── com.gi.streamlit.plist.tmpl       # the web app
│   ├── com.gi.whatsapp-worker.plist.tmpl # WhatsApp queue drainer
│   ├── com.gi.cloudflared.plist.tmpl     # the tunnel
│   └── com.gi.backup.plist.tmpl          # nightly backup at 02:00
├── scripts/
│   ├── install.sh        # one-shot install + load all four services
│   ├── uninstall.sh      # unload + remove (keeps data)
│   ├── restart_app.sh    # after `git pull`, restart Streamlit only
│   └── backup_db.sh      # SQLite online-backup → iCloud Drive
├── cloudflared_config.yml.example
└── README.md             # this file
```

---

## One-time setup — 45 minutes from zero to live

### 1. Install `cloudflared` (5 min)

You had a tarball-naming hiccup earlier — clean re-do:

```bash
sudo rm -f /usr/local/bin/cloudflared
cd ~/Downloads
curl -L -o cloudflared.tgz \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz
ls -lh cloudflared.tgz                                    # should be ~18 MB
tar -xzf cloudflared.tgz
sudo mv cloudflared /usr/local/bin/cloudflared
sudo chmod +x /usr/local/bin/cloudflared
rm cloudflared.tgz
cloudflared --version                                     # version 2024.x.x
```

### 2. Create the tunnel and route DNS (5 min)

```bash
cloudflared tunnel login                                  # browser opens, pick giinventory.com → Authorize
cloudflared tunnel create gi-hub
# Output prints a UUID like 8f2a3c4b-d5e6-... and the credentials file path.
# COPY the UUID — you'll paste it into the config in step 3.

cloudflared tunnel route dns gi-hub gi.giinventory.com
# CNAME for `gi` appears automatically in Cloudflare DNS dashboard.
```

### 3. Write the tunnel config (2 min)

```bash
mkdir -p ~/.cloudflared
cp "<this-folder>/cloudflared_config.yml.example" ~/.cloudflared/config.yml
# Open the file, replace <TUNNEL_UUID> + <USERNAME>:
nano ~/.cloudflared/config.yml
```

### 4. Confirm Python venv + DB exist (2 min)

```bash
cd "/Users/johnsonandrew/Downloads/CNCEC PROJECT"
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run main.py                           # Ctrl-C once it loads — just confirming
```

If this is the first run, `gi_database.db` gets created automatically by `init_db()`.

### 5. Install the launchd services (1 min)

```bash
./host_setup/scripts/install.sh
```

This:
- Renders the 4 plist templates into `~/Library/LaunchAgents/`
- Substitutes your project path, home, and username
- Loads all four — they start immediately and auto-restart on crash
- Prints a status table at the end

### 6. Verify locally (1 min)

```bash
./host_setup/scripts/install.sh --status   # all 4 should be green ✓ with PIDs
open http://localhost:8501                 # app loads
```

### 7. Set up Cloudflare Access for `@generalindustries.net` only (10 min)

This is the layer that ensures **only company emails can reach the app.** All on the Cloudflare dashboard, no code changes:

1. Cloudflare dashboard → **Zero Trust** (left sidebar, near the bottom).
2. First visit: choose a Team name → `giinventory` → **Free** plan (no card).
3. **Access → Applications → Add an application** → **Self-hosted**.
4. Configure:
   - **Application name:** `GI Hub Warehouse`
   - **Session duration:** `24 hours`
   - **Application domain:** `gi.giinventory.com`
   - Click **Next**.
5. **Add a policy:**
   - **Policy name:** `GI Staff only`
   - **Action:** `Allow`
   - **Configure rules → Include:**
     - Selector: **Emails ending in**
     - Value: `@generalindustries.net`
6. Click **Next** → leave defaults → **Add application**.
7. Optional but recommended — **Settings → Authentication → Login methods**: keep "One-time PIN" enabled (Cloudflare emails a 6-digit code to the user). You can also enable Google Workspace OAuth later if your company uses Google Workspace.

**Test it:**
- Open `https://gi.giinventory.com` from any device.
- You see Cloudflare's login page first. Enter a `@generalindustries.net` email → get a PIN by email → enter it → land on the GI Hub login page.
- Try with a personal Gmail: blocked at "You don't have permission".

### 8. Open WhatsApp Web for the worker (2 min)

The worker needs a logged-in WhatsApp Web session on the host Mac:

1. Open **Google Chrome** → `https://web.whatsapp.com`.
2. WhatsApp on phone → **Settings → Linked Devices → Link a Device** → scan QR.
3. Right-click the WhatsApp Web tab → **Pin Tab** (so it never closes).
4. Keep that Chrome instance running. The worker will pop new tabs into this Chrome to send messages.

### 9. Fire a manual backup to confirm permissions (1 min)

```bash
./host_setup/scripts/backup_db.sh
ls -lh ~/Library/Mobile\ Documents/com~apple~CloudDocs/GI_Hub_Backups/db/
```

If iCloud Drive isn't enabled, this fails — turn it on at **System Settings → Apple Account → iCloud → iCloud Drive**.

### 10. Hardening before sharing the URL (5 min)

- **Enable FileVault** (mandatory): System Settings → Privacy & Security → FileVault → On.
- **Change every default password** (sign in as admin, go to User Management).
- **Send the security brief from `handoff.md`** to management before circulating the URL.

You're live. URL: `https://gi.giinventory.com`. Visible only to `@generalindustries.net` emails.

---

## Daily operations

### Check status

```bash
./host_setup/scripts/install.sh --status
# Each service shows ✓ green if running, ⏸ yellow if loaded but exited, ✗ red if not loaded.
```

### Tail logs

```bash
./host_setup/scripts/install.sh --logs
# Streams all 4 services' .log + .err in one console. Ctrl-C to stop.
```

Logs live under `~/Library/Logs/`:
- `gi-streamlit.log` / `gi-streamlit.err`
- `gi-whatsapp-worker.log` / `gi-whatsapp-worker.err`
- `gi-cloudflared.log` / `gi-cloudflared.err`
- `gi-backup.log` / `gi-backup.err`

### Manual one-shot backup

```bash
./host_setup/scripts/backup_db.sh
```

The nightly run at **02:00** is automatic via `com.gi.backup`. Each run produces a timestamped snapshot in `~/Library/Mobile Documents/com~apple~CloudDocs/GI_Hub_Backups/db/`. Snapshots older than 14 days are auto-pruned.

### Restore from a backup

```bash
# Stop the app first
launchctl unload ~/Library/LaunchAgents/com.gi.streamlit.plist

# Restore (replace timestamp)
cp "~/Library/Mobile Documents/com~apple~CloudDocs/GI_Hub_Backups/db/gi_database_20260615_020000.db" \
   "/Users/johnsonandrew/Downloads/CNCEC PROJECT/gi_database.db"

# Restart
launchctl load -w ~/Library/LaunchAgents/com.gi.streamlit.plist
```

---

## When you change code or features

This is the workflow you'll use most often. **No downtime needed.**

```bash
cd "/Users/johnsonandrew/Downloads/CNCEC PROJECT"

# 1. Pull new code
git pull

# 2. If requirements.txt changed
.venv/bin/pip install -r requirements.txt

# 3. Restart Streamlit (and worker if its code changed)
./host_setup/scripts/restart_app.sh                # Streamlit only
# OR
./host_setup/scripts/restart_app.sh --worker       # Streamlit + worker
# OR
./host_setup/scripts/restart_app.sh --all          # everything

# 4. Verify
./host_setup/scripts/install.sh --status
```

`init_db()` runs on every Streamlit start, so any new schema columns/tables in `git pull` are applied automatically before users can sign in.

**Average restart downtime: ~3 seconds.** Users on an open page may see a "connection lost" toast that auto-reconnects.

---

## Quick troubleshooting

| Symptom | Where to look |
|---|---|
| `https://gi.giinventory.com` shows Cloudflare error 502 | Streamlit not running. `install.sh --status` → fix Streamlit row. |
| Cloudflare error 530 | Tunnel isn't connected. Check `gi-cloudflared.err` — usually a typo in `~/.cloudflared/config.yml`. |
| Cloudflare Access page never lets you in | Email isn't on the policy. Cloudflare Zero Trust → Access → Applications → policy → ensure your address (or domain pattern) is listed. |
| App loads but WhatsApp alerts don't deliver | `gi-whatsapp-worker.err` — usually WhatsApp Web tab closed or signed out. Re-pair on `web.whatsapp.com`. |
| Backup directory empty | iCloud Drive disabled. `gi-backup.err` will say "no such file or directory" for the iCloud path. |
| AI features dead | Check Ollama is running: `curl http://localhost:11434/api/tags`. Restart: `ollama serve`. |
| Mac went to sleep | The Streamlit launchd uses `caffeinate -dis` to prevent sleep — but lid-close still sleeps a laptop. Use clamshell mode (external monitor + power) or System Settings → Battery → Prevent automatic sleeping (Mac mini doesn't have this issue). |
| Mac shows lock screen after a while | `caffeinate` keeps the system awake but doesn't prevent screen lock. That's fine for the worker IF the worker uses Twilio. For pywhatkit + WhatsApp Web you also need to disable the screensaver: System Settings → Screen Saver → Start after = Never. |

---

## Uninstall (data preserved)

```bash
./host_setup/scripts/uninstall.sh
```

This unloads + removes the four `.plist` files. **It does NOT delete:**
- `gi_database.db`
- `uploads/`
- iCloud backups
- Cloudflare tunnel (still routes DNS — to fully delete: `cloudflared tunnel delete gi-hub` and remove the DNS record in the Cloudflare dashboard)
- Cloudflare Access policy (still blocks/allows — remove via Zero Trust dashboard)

# Google Drive sync: one-time setup (about 15 minutes)

This lets `tools/gdrive_sync.py` read your **CNCEC PROJECT Backup** folder by
itself. The permission is **read-only**: the script can list and download files,
and can never change, delete or share anything in your Drive. You do this once.
Afterwards the script renews its own access.

You need:
- the Google account that owns the folder (`johnsonandrew.j10@gmail.com`);
- this Mac, with the project at `~/GI_Hub_Project`.

---

## Part 1: create a small Google Cloud project (in the browser)

1. Open <https://console.cloud.google.com/> and sign in with the account that
   owns the folder. Accept the terms if asked.
2. At the top left, click the project picker (it may say *Select a project*),
   then **New project**.
   - Project name: `GI-Hub Drive Sync`
   - Location: leave as *No organization*
   - Click **Create** and wait about 10 seconds. Make sure the picker now shows
     `GI-Hub Drive Sync`.
3. **Turn on the Drive API.** In the search bar at the top type `Google Drive API`,
   open it, and click **Enable**.
4. **Set up the consent screen.** Open the menu (☰), then *APIs & Services* →
   **OAuth consent screen**. It may be called **Google Auth Platform**; click
   **Get started**.
   - App name: `GI-Hub Drive Sync`
   - User support email: your Gmail
   - Audience: **External**
   - Contact email: your Gmail
   - Tick the agreement, then click **Create**.
5. **Add yourself as a test user.** Go to **Audience** → *Test users* → **Add
   users** → type your Gmail → **Save**.
6. **Publish the app,** so the permission does not expire after 7 days.
   - On the same **Audience** page, click **Publish app** → **Confirm**. The
     status becomes *In production*.
   - Google will **not** review it. That is fine for an app only you use.
   - You will see a *"Google hasn't verified this app"* screen in Part 2; that
     is expected.
7. **Create the key file.** Go to **Clients** (or *Credentials* → **Create
   credentials** → **OAuth client ID**).
   - Application type: **Desktop app**
   - Name: `gi-hub-mac`
   - Click **Create**, then **Download JSON**.
8. Move the downloaded file (named like `client_secret_….json`) to this exact place:

```bash
mv ~/Downloads/client_secret_*.json ~/GI_Hub_Project/deploy/gdrive_client.json
```

---

## Part 2: give the script read-only access (on this Mac)

```bash
cd ~/GI_Hub_Project
.venv/bin/python tools/gdrive_sync.py --auth
```

1. A browser tab opens on Google's sign-in page. Choose your Gmail.
2. **"Google hasn't verified this app"**: click **Advanced**, then **Go to
   GI-Hub Drive Sync (unsafe)**. It is your own app.
3. The page says the app wants to **"See and download all your Google Drive
   files."** That is the read-only permission. Click **Continue** (or **Allow**).
4. The tab says *"GI-Hub has read-only access. You can close this tab."* The
   terminal says `✅ token saved to deploy/gdrive_token.json`.

**Check it works** (it only lists, nothing is downloaded):

```bash
.venv/bin/python tools/gdrive_sync.py --list
```

You should see the six workbooks, which one would be picked, and the files that
are ignored (lock files `~$…`, PDFs, DN workbooks).

---

## Safety notes

- `deploy/gdrive_client.json` and `deploy/gdrive_token.json` are **git-ignored**.
  They never go to GitHub. Do not email them.
- **To revoke access at any time:** go to <https://myaccount.google.com/permissions>,
  open *GI-Hub Drive Sync*, then *Remove access*. Then delete
  `deploy/gdrive_token.json`.
- The script asks Drive only for the **one folder ID**
  (`1rhTdX3UuBvwheXZwzBwmCViDkO9EMZR9`). It never reads any other folder.

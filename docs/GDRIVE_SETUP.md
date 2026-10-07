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
   While the app is in **Testing**, Google ends the sign-in **7 days** after you
   give it, and the scheduled pulls stop (the top bar's Drive chip turns red:
   *sign-in ended*). Publishing fixes that for good.

   **6a. Fill in the Branding page first.** *Publish app* stays greyed out
   ("To publish your app, you must complete your configuration on the Branding
   page") until it is complete. Open ☰ → **Google Auth Platform → Branding**
   and fill in:

   | Section | Field | What to put |
   |---|---|---|
   | App information | App name | `GI-Hub Drive Sync` |
   | App information | User support email | your Gmail (pick it from the list) |
   | App logo | — | **leave empty** — a logo makes Google require a review |
   | App domain | Application home page | `https://gi.giinventory.com` |
   | App domain | Privacy policy link | `https://gi.giinventory.com` |
   | App domain | Terms of service link | leave empty |
   | Authorised domains | **+ Add domain** | `giinventory.com` |
   | Developer contact information | Email addresses | your Gmail — **at the very bottom, the field most often missed** |

   Click **Save**. If it still refuses, a red or yellow line at the top of the
   Branding page names the missing field.

   **6b. Publish.** Go back to **Audience** → **Publish app** → **Confirm**.
   The status becomes *In production*.
   - Google will **not** review it. Read-only Drive access is a "restricted"
     scope, so Google may say verification is needed; confirm anyway. Without
     a review the app works for its own accounts (up to 100 users).
   - You will see a *"Google hasn't verified this app"* screen in Part 2; that
     is expected.
   - **Already signed in while it was in Testing?** Run Part 2 once more after
     publishing, so the saved sign-in is the long-lived kind.
7. **Create the key file.** Go to **Clients** (or *Credentials* → **Create
   credentials** → **OAuth client ID**).
   - Application type: **Desktop app**
   - Name: `gi-hub-mac`
   - Click **Create**, then **Download JSON**.
8. Move the downloaded file to this exact place. Google names it either
   `client_secret.json` or `client_secret_<long number>.json`; use the line that
   matches yours:

```bash
mv ~/Downloads/client_secret.json ~/GI_Hub_Project/deploy/gdrive_client.json
```

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

You should see the six workbooks, which one would be picked, the files that are
not used (lock files `~$…`, PDFs), and the subfolders read since Phase 22: **DN
for CNCEC**, **MTC** and **Pending Material Follow-up** (Waste Disposal is
ignored).

---

## Part 3: from then on (Phase 22a)

- **It pulls by itself** at the times in Admin Console → **Drive sync**
  (07:30 and 19:30 to start with). Change them there; no restart is needed.
- **Admin and HOD** can pull any time with the **Pull** button in the top bar.
- Every page shows a cloud and **"07:30 today"** in the top bar: when the
  workbook data last arrived. Amber means it is older than 26 hours, or ERP
  changes are waiting for your Commit; red means the last pull failed or the
  Google sign-in ended (run Part 2 again).
- **The ERP ledger commits by itself only when a pull just ADDS rows** (new
  days of paper). If a pull would edit or remove existing rows, it waits for
  you: Admin Console → Drive sync → **Commit ERP ledger**.
- A terminal run is the same run, recorded the same way:

```bash
.venv/bin/python tools/gdrive_sync.py
```

---

## Safety notes

- `deploy/gdrive_client.json` and `deploy/gdrive_token.json` are **git-ignored**.
  They never go to GitHub. Do not email them.
- **To revoke access at any time:** go to <https://myaccount.google.com/permissions>,
  open *GI-Hub Drive Sync*, then *Remove access*. Then delete
  `deploy/gdrive_token.json`.
- The script asks Drive only for the **one folder ID**
  (`1rhTdX3UuBvwheXZwzBwmCViDkO9EMZR9`) and the subfolders inside it. It never
  reads any other folder.
- DN photos, MTCs and request files are kept as **read-only copies** in the
  git-ignored `.cache/drive/` folder on this Mac, so staff without Drive access
  can open them in GI Hub.

# GI Floor PWA — offline scan-and-stage

A separate process that runs **alongside** the Streamlit app to give store keepers an offline-capable phone UI for staging consumption entries. Both processes write to the same `gi_database.db` (SQLite WAL mode makes concurrent writes safe).

## Quick start

```bash
# In one terminal — the existing Streamlit app, unchanged:
streamlit run main.py

# In another terminal — the new PWA service:
python pwa/run.py
```

The PWA is then live at `http://<your-mac-ip>:8001/`. On a phone connected to the same Wi-Fi, open that URL, sign in once (using the same username/password as the Streamlit app), and choose **"Add to Home Screen"**. The phone now has an installable app that:

1. Caches the inventory list on first online sign-in.
2. Lets the user keep scanning + staging entries when Wi-Fi drops.
3. Auto-syncs the local outbox to `pending_issues` (status=`draft`) the moment connectivity returns.

Those rows show up in the HOD's EOD review exactly like web-staged entries — no new workflow.

## Endpoints

| Method | Path                                | Purpose                              | Auth      |
|-------:|-------------------------------------|--------------------------------------|-----------|
| POST   | `/api/login`                        | Username/password → bearer token     | none      |
| GET    | `/api/whoami`                       | Token introspection                  | bearer    |
| GET    | `/api/inventory`                    | Minimal inventory list (cached)      | bearer    |
| POST   | `/api/pending_issues/batch`         | Bulk-stage offline outbox            | bearer    |
| GET    | `/healthz`                          | Liveness probe                       | none      |
| GET    | `/`                                 | PWA shell (`index.html`)             | none      |
| GET    | `/sw.js`, `/app.webmanifest`        | Service worker + install manifest    | none      |
| GET    | `/api/docs`                         | FastAPI auto-generated Swagger UI    | none      |

## Tests

```bash
pytest tests/test_pwa_api.py
```

Tests use FastAPI's `TestClient` against an in-memory SQLite — no network, no real bcrypt cost (passwords are pre-hashed in the fixture).

## Why a separate process

Streamlit and `uvicorn` both own event loops; mixing them in one Python interpreter is fragile. The clean split:

- Streamlit serves the desktop / admin / HOD UI.
- This FastAPI service serves the floor phones.
- They share the SQLite file. WAL mode + `busy_timeout=5000` (already in `database.get_connection`) is enough for the write volume of a typical warehouse shift.

If you later outgrow SQLite, the migration target (Postgres) is unchanged — both processes just point at the new DSN.

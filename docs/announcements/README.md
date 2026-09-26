# Announcements — "What's new", authored as code (Phase 14d)

One YAML file per feature, named `<YYYY-MM-DD>-<key>.yaml`, added **in the PR
that ships the feature** (ruling Q14-13). Reviewing the diff is approving the
sentence users will read.

```yaml
key: sme-grouped-queue            # unique, lower-case, 3-64 chars
title: Surface Shield jobs — one area per job
body: |
  Plain words, two or three sentences. What changed and what to do.
routes: [/execution]              # REQUIRED — where the feature lives
roles: [supervisor, hod]          # optional — narrows the audience, never widens it
sites: [CNCEC]                    # optional — omit for every site
tutorial: sup_grouped_queue_v1    # optional — Training Hub module key
rerender: true                    # optional — that tutorial must be re-recorded
manual: "4.9a.2"                  # optional — must be a real USER_MANUAL.md heading (rule 13)
```

**Who sees it** is worked out from the navigation matrix (rule 14): every role
that can open one of `routes`. You cannot announce a page to someone who cannot
open it. After changing `frontend/src/config/nav.tsx`, refresh the snapshot:

```
.venv/bin/python tools/announcements.py nav
```

Check the files, then load them as drafts:

```
.venv/bin/python tools/announcements.py lint
.venv/bin/python tools/announcements.py sync
```

Nothing is published by a deploy. An admin publishes, schedules or retracts in
**Admin Console → Announcements**. Delivery is in-app only: the bell and the
What's-new panel.

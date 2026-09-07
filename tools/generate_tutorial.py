#!/usr/bin/env python3
"""
tools/generate_tutorial.py — PHASE 12 TUTORIAL ORCHESTRATOR.

Turns one tracked YAML script into one finished tutorial MP4:

    YAML ─▶ [egress guard] ─▶ HeyGen payload ─▶ avatar audio ─┐   ← pass A
                                        (measured durations)   │
                                                               ▼
              shot list + per-beat HOLDS ─▶ Playwright ─▶ screencast.webm  ← pass B
                                              + beats.json    │
                                                              ▼
                                                    ffmpeg composite ─▶ .mp4

⚠️ PASS A COMES FIRST, AND THAT IS THE DESIGN, NOT THE ORDER IT WAS WRITTEN IN.
A Playwright-driven UI is far faster than a person describing it: the first run
of this pipeline recorded first and narrated second, and overran ALL SIX beats
— 40.2 s of speech over a 19.6 s recording, the worst by 5.15 s. So the audio
is rendered and MEASURED first, and each UI step is then held for the length of
its own line. With a real key that ordering is unchanged: HeyGen is called
BEFORE the browser opens, because the avatar's timing is what the screencast
has to be cut to.

⚠️ THE ONE RULE THIS FILE ENFORCES (P12-1). The HeyGen request is built from
the YAML's `narration:` block and nothing else, and `assert_text_only()`
refuses to send any string that is not a `say:` line in that file. No frame, no
screenshot, no database row, no API response. The screencast — which is the
half that CAN carry real stock and real employee names — never leaves the
machine: the avatar comes back as a clip and ffmpeg composites LOCALLY.

⚠️ NO HEYGEN KEY EXISTS YET, so the default run MOCKS the call: it prints the
exact JSON that would be posted and synthesises a stand-in avatar locally
(`say` for the voice, Pillow for the card). Everything downstream of the call
— alpha compositing, audio placement, beat-aligned captions, the encode — is
the real pipeline, unchanged, so the mock proves the part that would otherwise
be assumed.

⚠️ AND ONE MEASURED FACT ABOUT THIS MACHINE: Homebrew's ffmpeg 8.1.2 is built
WITHOUT libfreetype, so `drawtext` does not exist here ("Unknown filter
'drawtext'"). Every caption is therefore rendered to an RGBA PNG with Pillow
and composited with `overlay`. That is not a workaround — it is better: real
fonts, brand colours, and no filtergraph escaping for text a human wrote.

Usage
-----
    .venv/bin/python tools/generate_tutorial.py                 # one tutorial
    .venv/bin/python tools/generate_tutorial.py --all           # the catalogue
    .venv/bin/python tools/generate_tutorial.py --all --dry-run # the plan only
    .venv/bin/python tools/generate_tutorial.py --skip-record   # re-composite

⚠️ Do not run this while `cd tests/e2e && npm test` is running. Both own
`gihub_e2e_pw`, :8010 and :5183, and the loser fails looking like a flaky spec.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import textwrap
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
E2E = ROOT / "tests" / "e2e"
RECORDER = ROOT / "tests" / "video_gen"
SCRIPT_DIR = ROOT / "tools" / "tutorials"
DEFAULT_SCRIPT = SCRIPT_DIR / "store_keeper_hub_assistant.yaml"
NAV_DUMP = ROOT / "frontend" / "scripts" / "nav_access_dump.mjs"
AUTH_PY = ROOT / "backend" / "api" / "auth.py"
DEFAULT_OUT = ROOT / "docs" / "tutorials" / "out"
MAKE_DATASET = ROOT / "tools" / "make_tutorial_db.py"
TUTORIAL_DB = ROOT / "tutorial_fixture.db"

# ⚠️ ITS OWN DATABASE AND ITS OWN TWO PORTS, not the gate's `gihub_e2e_pw` on
# :8010/:5183. `tests/e2e/harness/env.ts` reads all four from the environment,
# so this needs no edit to the suite — and it turns "do not record while the E2E
# gate is running" from a documented hazard into an impossible one. Answers the
# plan's open question Q10.
DATASET_ENV = {
    "tutorial": {
        "GI_DB_FILE": str(TUTORIAL_DB),
        "E2E_DB": "gihub_tutorial_pw",
        "E2E_API_PORT": "8011",
        "E2E_WEB_PORT": "5184",
    },
    # The gate's own stack, loaded from the REAL gi_database.db. Recording
    # against it is a diagnostic, never a deliverable — see `_dataset_env`.
    "e2e": {},
}

# ⚠️ Bumped when the COMPOSITE changes in a way that makes an existing render
# stale. The batch runner re-renders anything whose manifest carries an older
# one — which is how a pipeline fix reaches sixty videos without a person
# remembering which of them it touched.
PIPELINE_VERSION = 2

FPS = 30
CANVAS = (1920, 1080)
AVATAR_PX = 360
GI_NAVY = (0, 31, 64, 255)
GI_GOLD = (201, 162, 39, 255)
INK = (233, 238, 247, 255)
DIM = (132, 146, 168, 255)

FONT_REG = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

# ⚠️ WHERE THE AVATAR SITS IS A PER-TUTORIAL CHOICE, NOT A HOUSE STYLE, and
# this is the defect the first composite had: the stand-in was pinned bottom
# right, which is exactly where the Hub Assistant panel opens — so the tutorial
# about the assistant spent thirty seconds with the assistant behind a talking
# head. The rule is simply that the avatar never covers the control being
# demonstrated, and only the script's author knows which control that is.
CORNERS = {
    "bottom-left": ("56", "H-h-56"),
    "bottom-right": ("W-w-56", "H-h-56"),
    "top-left": ("56", "56"),
    "top-right": ("W-w-56", "56"),
}

# HeyGen's v2 generate endpoint. Never called without --live AND a key.
HEYGEN_URL = "https://api.heygen.com/v2/video/generate"


# ══════════════════════════════════════════════════════════════════════════
# 0. small shell helpers
# ══════════════════════════════════════════════════════════════════════════
def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    printable = " ".join(str(c) for c in cmd)
    print(f"  $ {printable[:200]}{'…' if len(printable) > 200 else ''}")
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def ffmpeg(args: list[str]) -> None:
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args])


def probe_seconds(path: pathlib.Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    return float(out)


def hms(seconds: float) -> str:
    return f"{int(seconds // 60):d}:{seconds % 60:05.2f}"


# ══════════════════════════════════════════════════════════════════════════
# 0b. provenance
# ══════════════════════════════════════════════════════════════════════════
def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_state() -> dict:
    """The commit a render was made from, and whether the tree was clean."""
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                    check=True, capture_output=True,
                                    text=True).stdout.strip())
        return {"sha": sha, "dirty": dirty}
    except Exception:  # noqa: BLE001 — a render outside a checkout still works
        return {"sha": None, "dirty": None}


# ══════════════════════════════════════════════════════════════════════════
# 0c. the rule-14 route lint
# ══════════════════════════════════════════════════════════════════════════
def role_levels() -> dict[str, int]:
    """
    Read the seniority ladder from `auth.ROLE_META` — the ONE place it is
    defined. Restating it here would make a third copy of a number that already
    disagrees with people's intuitions (rule 14: `minLevel` is a ladder and the
    roles are not one).
    """
    src = AUTH_PY.read_text(encoding="utf-8")
    block = src.split("ROLE_META = {", 1)[1].split("}\n", 1)[0]
    return {m[1]: int(m[2]) for m in
            re.finditer(r'"(\w+)":\s*\{"label":[^,]+,\s*"level":\s*(\d+)\}', block)}


def nav_access() -> dict:
    """Ask the frontend manifest which roles may open which routes."""
    out = subprocess.run(
        ["node", str(NAV_DUMP), json.dumps(role_levels())],
        cwd=ROOT / "frontend", check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def route_lint(doc: dict, access: dict) -> list[str]:
    """
    Refuse a script that declares a route its role cannot open.

    ⚠️ AN UNRESOLVED ROUTE IS REPORTED, NEVER PASSED. `/records/*` and
    `/master/*` are built by `.map()` in the manifest and the dumper cannot
    resolve them; saying "fine" about a route nobody checked is rule 16's
    mistake in a new place, so they come back as UNKNOWN and the ground-truth
    check at record time is what settles them.
    """
    problems: list[str] = []
    if not access.get("sane"):
        return ["nav_access_dump found almost nothing — the manifest has moved, "
                "and this lint would have passed for the wrong reason"]
    role = doc["hub_role"]
    known = access["routes"]
    publics = access["publics"]
    for route in doc.get("routes") or []:
        if any(route.startswith(pfx) for pfx in publics):
            continue
        if route not in known:
            problems.append(
                f"UNKNOWN  {route} — the nav manifest builds this group with "
                f".map() and the dumper cannot resolve it "
                f"({len(access['unresolved'])} such group(s)). Reported, never "
                f"passed; the record-time check settles it")
        elif role not in known[route]:
            problems.append(f"REFUSED  {route} — {role} may not open it "
                            f"(allowed: {', '.join(known[route]) or 'nobody'})")
    return problems


def redaction_lint(doc: dict) -> list[str]:
    """
    Refuse a replacement that contains the thing it replaces.

    ⚠️ FOUND BY THE FIRST SCRIPT THAT NEEDED ONE. The redaction hook rewrites
    text nodes on every DOM mutation, and writing a text node IS a mutation, so
    it re-enters on its own output. That is fine while the mapping reaches a
    fixed point — `worker` → `demo.user` does, because `demo.user` contains no
    `worker`. `hod` → `demo.hod` does not: the observer produced `demo.demo.hod`,
    then `demo.demo.demo.hod`, and kept going until the renderer stalled. The
    recording failed four minutes later reporting only that a heading was not
    visible, which points at the page, not at the mask.
    """
    bad = []
    for src, dst in (doc.get("redaction") or {}).get("replace", {}).items():
        if str(src) in str(dst):
            bad.append(f"REFUSED  redaction {src!r} → {dst!r}: the replacement "
                       f"contains the thing it replaces, so the in-page rewrite "
                       f"never terminates")
    return bad


def manual_fence_lint(doc: dict) -> list[str]:
    """
    Every chapter a script says it was written from must be one the role is
    ALLOWED to read.

    ⚠️ IT ASKS RULE 9's OWN FUNCTION. `manual_qa.allowed_sections()` is the
    security boundary — the fence that runs BEFORE BM25 scores anything — and
    it imports cleanly with no database. Re-stating the allowlist here would
    create a second, weaker copy of the one thing P11-4 says must not acquire
    one, and the copy is always what rots.

    The failure it prevents is specific and quiet: a tutorial narrated from a
    chapter its role cannot open teaches that role a screen it will never see,
    and the only symptom is a confused person. §13 is shared by the Store
    Keeper and the HOD; §6 is the HOD's alone; §14 and §15 are deliberately
    isolated from each other.
    """
    sections = doc.get("manual_sections") or []
    if not sections:
        return ["no `manual_sections:` — say which chapters the narration came "
                "from so the fence can be checked"]
    try:
        # ⚠️ The repo root, not the CWD. Run as `python tools/generate_tutorial.py`
        # sys.path[0] is `tools/`, so `backend` is not importable and the lint
        # would report "could not import the fence" forever — which is honest
        # and useless.
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        os.environ.setdefault("GI_DOTENV", "0")
        from backend.api.ai.manual_qa import allowed_sections
    except Exception as e:  # noqa: BLE001
        return [f"could not import the fence ({e}) — reported, not assumed"]
    allowed = allowed_sections(doc["hub_role"])
    bad = [n for n in sections if int(n) not in allowed]
    if bad:
        return [f"FENCED   §{n} is not in {doc['hub_role']}'s allowlist "
                f"({', '.join('§' + str(x) for x in sorted(allowed))})"
                for n in bad]
    return []


def check_visited(doc: dict, beats: dict) -> list[str]:
    """
    The ORACLE. `canAccessPath` fails closed by redirecting, so a role that
    walks into a forbidden page lands somewhere the script never declared.
    """
    declared = set(doc.get("routes") or [])
    visited = [v for v in beats.get("visited", []) if v not in ("", "about:blank")]
    stray = [v for v in dict.fromkeys(visited) if v not in declared]
    if not stray:
        return []
    return [f"the browser landed on {v!r}, which the script does not declare — "
            f"either add it to `routes:` or the app refused a page "
            f"{doc['hub_role']} may not open" for v in stray]


# ══════════════════════════════════════════════════════════════════════════
# 1. the script
# ══════════════════════════════════════════════════════════════════════════
REQUIRED = ("tutorial_id", "title", "role", "hub_role", "language",
            "steps", "narration")


def load_script(path: pathlib.Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if not doc.get(k)]
    if missing:
        sys.exit(f"FATAL: {path.name} is missing: {', '.join(missing)}")
    for i, line in enumerate(doc["narration"]):
        if not line.get("beat") or not line.get("say"):
            sys.exit(f"FATAL: narration[{i}] needs both `beat:` and `say:`")

    # ⚠️ EVERY NARRATION LINE MUST NAME A BEAT SOME STEP ACTUALLY STAMPS, and
    # every beat must have a line. Either half alone is silent: a line whose
    # beat is never stamped is simply dropped from the timeline, and a beat with
    # no line is a hold with nothing to say in it. Both produce a video that
    # looks finished.
    step_beats = [st.get("beat") for st in doc["steps"] if st.get("beat")]
    said = [l["beat"] for l in doc["narration"]]
    orphan_lines = [b for b in said if b not in step_beats]
    orphan_beats = [b for b in step_beats if b not in said]
    if orphan_lines:
        sys.exit(f"FATAL: narration names beat(s) no step stamps: "
                 f"{', '.join(orphan_lines)}")
    if orphan_beats:
        sys.exit(f"FATAL: step(s) stamp beat(s) nothing narrates: "
                 f"{', '.join(orphan_beats)}")
    if said != step_beats:
        sys.exit(f"FATAL: narration order {said} does not match the step order "
                 f"{step_beats} — the compositor places lines at MEASURED beat "
                 f"times, so a reordered script would narrate the wrong screen")
    return doc


# A beat's narration is followed by this much silence before the UI moves on.
# Not zero: a step that cuts on the last syllable reads as clipped.
BREATH_S = 0.35


def shot_list(doc: dict, holds: dict[str, int], think_ms: int) -> dict:
    """The JSON the spec reads. Python owns the YAML; TypeScript never parses it."""
    red = doc.get("redaction") or {}
    return {
        "tutorial_id": doc["tutorial_id"],
        "role": doc["role"],
        "language": doc["language"],
        "steps": doc["steps"],
        "assistant_question": doc.get("assistant_question"),
        "assistant_answer": (doc.get("assistant_answer") or "").strip() or None,
        "assistant_think_ms": think_ms,
        # ⚠️ THE WHOLE POINT OF PASS A. Each beat is held for as long as its
        # narration actually takes, measured from the rendered audio. See the
        # module docstring: the first run of this pipeline overran every one of
        # six beats by 2.3-5.2 s because the holds were guesses.
        "holds": holds,
        "routes": list(doc.get("routes") or ["/"]),
        "mask": list(red.get("mask") or []),
        "replace": dict(red.get("replace") or {}),
    }


# ══════════════════════════════════════════════════════════════════════════
# 2. record — Playwright against the isolated E2E stack (rule 15)
# ══════════════════════════════════════════════════════════════════════════
def _dataset_env(dataset: str) -> dict[str, str]:
    """
    Build (or refresh) the dataset the recording runs against, and return the
    environment overrides that point the E2E stack at it.

    ⚠️ `--dataset e2e` IS NOT A SUPPORTED WAY TO MAKE A VIDEO. It records
    against the gate's clone of the real `gi_database.db` — real employee
    names, real material descriptions, real SAP codes, real quantities — and
    ruling P12-0 says a tutorial is recorded against synthetic data or it is not
    published. It stays reachable because it is genuinely useful for debugging
    the recorder against the shapes the gate uses, and because removing it would
    make somebody re-invent it worse. It is loud, and it stamps the manifest.
    """
    if dataset == "e2e":
        print("\n  " + "!" * 72)
        print("  ⚠️  --dataset e2e: recording against the REAL gi_database.db "
              "clone.")
        print("      The output carries live employee names and live stock. "
              "It is a")
        print("      DIAGNOSTIC ONLY and must not be published (ruling P12-0).")
        print("  " + "!" * 72)
        return {}

    print(f"      building the synthetic dataset → {TUTORIAL_DB.name}")
    run([sys.executable, str(MAKE_DATASET), "--out", str(TUTORIAL_DB)])
    return dict(DATASET_ENV["tutorial"])


def dataset_version() -> int:
    """Read `DATASET_VERSION` without importing the legacy package."""
    for line in MAKE_DATASET.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATASET_VERSION"):
            return int(line.split("=")[1].strip())
    return 0


def ensure_node_modules() -> None:
    """
    Point the recorder at the E2E suite's `node_modules` instead of installing
    a second copy. Same Playwright build as the gate, no network, ~300 MB saved
    — and it cannot drift into recording against a different browser than the
    one the suite tests with.
    """
    link = RECORDER / "node_modules"
    target = E2E / "node_modules"
    if not target.exists():
        sys.exit(f"FATAL: {target} is missing — run `npm ci` in tests/e2e first")
    if link.is_symlink() or link.exists():
        return
    link.symlink_to(os.path.relpath(target, RECORDER))
    print(f"[record] linked {link.relative_to(ROOT)} → {target.relative_to(ROOT)}")


def record(shot: dict, work: pathlib.Path, reuse_stack: bool,
           dataset_env: dict[str, str],
           keep_stack: bool = False) -> tuple[pathlib.Path, dict]:
    ensure_node_modules()
    work.mkdir(parents=True, exist_ok=True)
    shot_path = work / "shotlist.json"
    shot_path.write_text(json.dumps(shot, indent=2), encoding="utf-8")

    env = {**os.environ, **dataset_env,
           "GI_TUTORIAL_SHOTLIST": str(shot_path),
           "GI_TUTORIAL_OUT": str(work)}
    # ⚠️ TWO INDEPENDENT FLAGS. See tests/video_gen/stack.ts: one flag was not
    # enough, and the bug only appeared once there were two scripts to batch.
    if reuse_stack:
        env["GI_VIDEO_REUSE_STACK"] = "1"
    if keep_stack:
        env["GI_VIDEO_KEEP_STACK"] = "1"

    t0 = time.time()
    # ⚠️ NOT `check=True`. A failed recording is one tutorial to fix, not a
    # reason to abandon the other fifty-nine and leave the shared stack up —
    # which is exactly what the first four-script batch did.
    rc = subprocess.run(
        ["npx", "playwright", "test", "-c", "../video_gen/playwright.config.ts"],
        cwd=E2E, env=env, check=False).returncode
    if rc:
        raise RecordingFailed(f"playwright exited {rc} — see the error above")
    print(f"      playwright finished in {time.time() - t0:.1f}s")

    beats = json.loads((work / "beats.json").read_text(encoding="utf-8"))
    return pathlib.Path(beats["video"]), beats


# ══════════════════════════════════════════════════════════════════════════
# 3. the HeyGen boundary
# ══════════════════════════════════════════════════════════════════════════
class EgressRefused(RuntimeError):
    pass


class RecordingFailed(RuntimeError):
    """One tutorial's browser run failed. The batch carries on to the rest."""


# Shapes that mean "this came out of the database, not out of the script".
_DATA_SHAPES = (
    (re.compile(r"\bdata:[a-z]+/"), "a data: URI (an embedded asset)"),
    (re.compile(r"[A-Za-z0-9+/]{120,}={0,2}"), "a base64 blob"),
    (re.compile(r"(?:^|\s)/(?:Users|var|tmp|home)/\S+"), "an absolute filesystem path"),
    (re.compile(r"\.(?:png|jpe?g|webm|mp4|mov|db|xlsx|csv|pdf)\b", re.I), "a file reference"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "an email address"),
    (re.compile(r"\b\d{6,}\b"), "a long digit run (an ID or SAP code)"),
)


def assert_text_only(payload: dict, allowed: set[str]) -> None:
    """
    The egress guard. Two independent checks, because either alone fails open:

      1. STRUCTURAL — every leaf is a scalar. A payload cannot carry bytes, a
         file handle or a nested asset descriptor, because there is nowhere in
         the tree for one to sit.
      2. PROVENANCE — every free-text leaf longer than a token is EXACTLY a
         `say:` line from the tracked YAML. Not "looks safe", not "passed a
         scrubber": character-for-character a string a person reviewed in a
         diff. A scrubber is a filter that gets better over time; this is a
         whitelist that is complete on day one.

    The `_DATA_SHAPES` sweep runs anyway, over the whitelisted strings. It
    exists to catch a script whose AUTHOR pasted a real name or a SAP code into
    the narration — provenance says the string was reviewed, not that the
    review was any good.
    """
    def walk(node, path="payload"):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, (str, int, float, bool)) or node is None:
            if isinstance(node, str) and " " in node.strip():
                if node not in allowed:
                    raise EgressRefused(
                        f"{path} carries free text that is not a reviewed "
                        f"narration line:\n    {node[:120]!r}")
        else:
            raise EgressRefused(f"{path} is a {type(node).__name__}, not a scalar")

    walk(payload)

    for text in allowed:
        for rx, what in _DATA_SHAPES:
            if rx.search(text):
                raise EgressRefused(
                    f"narration line looks like {what} — refusing to send:\n"
                    f"    {text[:120]!r}")


def heygen_payload(doc: dict) -> tuple[dict, set[str]]:
    """
    Build the request AND the whitelist of strings it is allowed to contain, in
    the same function.

    ⚠️ These two were separate on the first run and the guard immediately
    refused its own payload — `payload.title` is composed from four tracked
    YAML scalars and the whitelist only knew about narration lines. That is the
    guard working, but a whitelist assembled somewhere else is a whitelist that
    drifts: the next field added here would have been "fixed" by widening the
    other function, and the check would have quietly become decorative.
    Returning both from one place makes every emitted string name its source.
    """
    av = doc.get("avatar") or {}
    title = f"GI Hub · {doc['title']} · {doc['hub_role']} · {doc['language']}"
    allowed = {title}
    for line in doc["narration"]:
        allowed.add(line["say"].strip())
        allowed.add(" ".join(line["say"].split()))
    payload = {
        "caption": False,
        "dimension": {"width": AVATAR_PX * 3, "height": AVATAR_PX * 3},
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": av.get("avatar_id", "PLACEHOLDER_AVATAR_ID"),
                    "avatar_style": "normal",
                },
                "voice": {
                    "type": "text",
                    "voice_id": av.get("voice_id", "PLACEHOLDER_VOICE_ID"),
                    "input_text": line["say"].strip(),
                },
                "background": {"type": "color", "value": "#00000000"},
            }
            for line in doc["narration"]
        ],
    }
    payload["title"] = title
    return payload, allowed


def heygen_live(payload: dict, key: str) -> dict:
    """
    ⚠️ UNVERIFIED. No HeyGen key has ever been used against this repository, so
    this function has never executed. It is written from the published v2
    contract and must be treated as a first draft until one real 200 is seen.
    Deliberately NOT reachable without both --live and HEYGEN_API_KEY.
    """
    import requests
    r = requests.post(HEYGEN_URL, json=payload, timeout=60,
                      headers={"X-Api-Key": key, "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()


# ══════════════════════════════════════════════════════════════════════════
# 4. the mock avatar — local voice + local card
# ══════════════════════════════════════════════════════════════════════════
def say_to_wav(text: str, dest: pathlib.Path, voice: str | None) -> pathlib.Path | None:
    """macOS `say` stands in for HeyGen's TTS. Returns None when unavailable."""
    if not shutil.which("say"):
        return None
    aiff = dest.with_suffix(".aiff")
    cmd = ["say", "-r", "168", "-o", str(aiff)]
    if voice:
        cmd += ["-v", voice]
    try:
        run(cmd + [text])
    except subprocess.CalledProcessError:
        return None
    ffmpeg(["-i", str(aiff), "-ar", "48000", "-ac", "1", str(dest)])
    aiff.unlink(missing_ok=True)
    return dest


def synthesize(doc: dict, work: pathlib.Path, voice: str | None) -> list[dict]:
    """
    PASS A — render every narration line and MEASURE it, before a browser is
    opened.

    ⚠️ THIS ORDERING IS THE MOST IMPORTANT THING IN THE FILE, and it was not
    obvious until the pipeline was run the other way round. The first version
    recorded first and narrated second, and the timing report said this:

        beat            starts     window     narration   verdict
        title             0.86s     3.15s      8.30s   OVERRUNS by  5.15s
        open_assistant    4.02s     4.17s      6.46s   OVERRUNS by  2.29s
        question          8.18s     1.23s      5.41s   OVERRUNS by  4.17s
        thinking          9.41s     1.83s      5.98s   OVERRUNS by  4.15s
        answer           11.24s     5.74s      8.71s   OVERRUNS by  2.97s
        close            16.98s     2.66s      5.34s   OVERRUNS by  2.68s

    Six beats, six overruns, 40.2 s of speech over a 19.6 s recording. A UI
    driven by Playwright is *fast* — far faster than a person explaining it —
    so a narration written for humans will always outrun it. The fix is not a
    longer guessed pause: it is to hold each step for the MEASURED length of
    its own line, which means the audio has to exist first.

    Same discipline as `docs/exec_video/project_v3/build.py`, which cuts every
    scene to its measured WAV rather than to a guess — arrived at here
    independently, by getting it wrong.
    """
    clips: list[dict] = []
    vo = work / "vo"
    vo.mkdir(parents=True, exist_ok=True)
    for i, line in enumerate(doc["narration"]):
        text = " ".join(line["say"].split())
        wav = say_to_wav(text, vo / f"{i:02d}_{line['beat']}.wav", voice)
        # No `say` on this host: estimate at ~2.6 words/s so the holds are
        # still roughly right and the pipeline still produces a silent video.
        dur = probe_seconds(wav) if wav else len(text.split()) / 2.6
        clips.append({"beat": line["beat"], "dur": dur, "wav": wav, "text": text})
    return clips


def holds_from(clips: list[dict]) -> dict[str, int]:
    return {c["beat"]: int((c["dur"] + BREATH_S) * 1000) for c in clips}


def place_narration(clips: list[dict], times: dict[str, float], total_s: float,
                    work: pathlib.Path) -> tuple[pathlib.Path, list[dict]]:
    """
    PASS B's other half — lay the rendered lines onto the MEASURED beat times.

    The report is printed either way. A green one is not decoration: it is the
    evidence that the holds computed in pass A survived contact with a real
    browser, and a UI change that makes a step slower shows up here as a gap
    rather than as narration talking over the next screen.
    """
    placed = []
    for c in clips:
        if c["beat"] not in times:
            print(f"  ⚠️  narration beat {c['beat']!r} was never stamped by the "
                  f"recording — skipped")
            continue
        placed.append({**c, "start": times[c["beat"]]})

    print("\n  beat            starts     window     narration   verdict")
    print("  " + "─" * 62)
    worst = 0.0
    for i, c in enumerate(placed):
        nxt = placed[i + 1]["start"] if i + 1 < len(placed) else total_s
        window = nxt - c["start"]
        over = c["dur"] - window
        worst = max(worst, over)
        verdict = "ok" if over <= 0.05 else f"OVERRUNS by {over:5.2f}s"
        print(f"  {c['beat']:<14} {c['start']:7.2f}s {window:8.2f}s "
              f"{c['dur']:9.2f}s   {verdict}")
    print(f"  {'':<14} {'':>7}  {'':>8}  worst overrun: {worst:+.2f}s\n")

    out = work / "narration.wav"
    have = [c for c in placed if c["wav"]]
    if not have:
        ffmpeg(["-f", "lavfi", "-t", f"{total_s:.3f}",
                "-i", "anullsrc=r=48000:cl=mono", str(out)])
        print("  ⚠️  no `say` on this host — the narration track is silence")
        return out, placed

    args: list[str] = ["-f", "lavfi", "-t", f"{total_s:.3f}",
                       "-i", "anullsrc=r=48000:cl=mono"]
    for c in have:
        args += ["-i", str(c["wav"])]
    parts = [f"[{n}:a]adelay={int(c['start'] * 1000)}:all=1[a{n}]"
             for n, c in enumerate(have, start=1)]
    mix = "".join(f"[a{n}]" for n in range(1, len(have) + 1))
    graph = ";".join(parts) + f";[0:a]{mix}amix=inputs={len(have) + 1}:normalize=0[out]"
    ffmpeg(args + ["-filter_complex", graph, "-map", "[out]",
                   "-ar", "48000", "-ac", "1", str(out)])
    return out, placed


def avatar_card(doc: dict, dest: pathlib.Path) -> pathlib.Path:
    """A round, transparent-background stand-in for HeyGen's talking head."""
    from PIL import Image, ImageDraw, ImageFont
    size = AVATAR_PX * 3
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size - 1, size - 1), fill=GI_NAVY, outline=GI_GOLD, width=14)
    f_big = _font(FONT_BOLD, 96)
    f_sm = _font(FONT_REG, 46)
    _centre(d, size // 2, size // 2 - 110, "MOCK", f_big, GI_GOLD)
    _centre(d, size // 2, size // 2 - 10, "AVATAR", f_big, GI_GOLD)
    av = (doc.get("avatar") or {}).get("avatar_id", "—")
    _centre(d, size // 2, size // 2 + 96, "HeyGen would render", f_sm, DIM)
    _centre(d, size // 2, size // 2 + 152, av, f_sm, INK)
    img.save(dest)
    return dest


def avatar_mask(dest: pathlib.Path) -> pathlib.Path:
    """A greyscale circle: white inside, black outside. `alphamerge` fodder."""
    from PIL import Image, ImageDraw
    size = AVATAR_PX * 3
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).ellipse((0, 0, size - 1, size - 1), fill=255)
    m.save(dest)
    return dest


def probe_pix_fmt(path: pathlib.Path) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt", "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()


def build_avatar_clip(doc: dict, narration: pathlib.Path, total_s: float,
                      work: pathlib.Path) -> tuple[pathlib.Path, bool]:
    """
    Encode the stand-in the way a real transparent-background HeyGen clip would
    arrive, so the composite exercises the ACTUAL alpha path.

    ⚠️ AND THEN PROVE IT, BECAUSE THE ENCODE LIES. Homebrew's ffmpeg 8.1.2
    accepts `-pix_fmt yuva420p` with `libvpx-vp9`, exits 0, prints no warning
    — and writes a `yuv420p` stream. The alpha is silently discarded, and the
    only symptom is a black square around the avatar in the finished MP4. Same
    shape as rule 16: an exit code is not a result, so the pix_fmt is READ BACK
    and a mask path is used when the claim does not hold. VP8 drops it too;
    both were checked.
    """
    card = avatar_card(doc, work / "avatar_card.png")
    alpha = work / "avatar.webm"
    try:
        ffmpeg(["-loop", "1", "-framerate", str(FPS), "-i", str(card),
                "-i", str(narration), "-t", f"{total_s:.3f}",
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "1M",
                "-auto-alt-ref", "0", "-deadline", "realtime", "-cpu-used", "5",
                "-c:a", "libopus", "-b:a", "96k", str(alpha)])
        got = probe_pix_fmt(alpha)
        if got.startswith("yuva"):
            return alpha, True
        print(f"  ⚠️  VP9 encode reported success but wrote {got}, not yuva420p — "
              f"this build has no alpha. Using the mask path instead.")
    except subprocess.CalledProcessError:
        print("  ⚠️  VP9 alpha encode failed — using the mask path instead")
    alpha.unlink(missing_ok=True)

    opaque = work / "avatar.mp4"
    ffmpeg(["-loop", "1", "-framerate", str(FPS), "-i", str(card),
            "-i", str(narration), "-t", f"{total_s:.3f}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "22",
            "-c:a", "aac", "-b:a", "128k", str(opaque)])
    return opaque, False


# ══════════════════════════════════════════════════════════════════════════
# 5. overlays — Pillow, because this ffmpeg has no drawtext
# ══════════════════════════════════════════════════════════════════════════
def _font(path: str, size: int):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _centre(draw, cx: int, y: int, text: str, font, fill) -> None:
    w = draw.textlength(text, font=font)
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def _panel(draw, box, radius=18, fill=(0, 31, 64, 214), outline=GI_GOLD, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def watermark_png(doc: dict, live: bool, dest: pathlib.Path) -> pathlib.Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    title, sub = doc["title"], doc.get("subtitle", doc["hub_role"])
    f_t, f_s, f_b = _font(FONT_BOLD, 40), _font(FONT_REG, 25), _font(FONT_BOLD, 22)
    w = int(max(d.textlength(title, font=f_t), d.textlength(sub, font=f_s))) + 120
    _panel(d, (48, 44, 48 + w, 156))
    d.rectangle((48, 44, 56, 156), fill=GI_GOLD)
    d.text((84, 62), title, font=f_t, fill=INK)
    d.text((86, 112), sub, font=f_s, fill=DIM)
    if not live:
        # Top right, deliberately: the bottom of the frame belongs to the
        # captions and the avatar, and a warning that overlaps either is a
        # warning people learn to read past.
        band = "PROTOTYPE · MOCK AVATAR · NO HEYGEN CALL WAS MADE"
        bw = int(d.textlength(band, font=f_b)) + 44
        _panel(d, (CANVAS[0] - 48 - bw, 44, CANVAS[0] - 48, 96),
               radius=10, fill=(90, 20, 20, 214), outline=(255, 92, 92, 255))
        d.text((CANVAS[0] - 26 - bw, 56), band, font=f_b, fill=(255, 214, 214, 255))
    img.save(dest)
    return dest


def caption_png(text: str, x0: int, wrap: int, dest: pathlib.Path) -> pathlib.Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = _font(FONT_REG, 34)
    # ⚠️ NOT `[:3]`. Truncating here silently dropped the last four words of a
    # narration line the avatar still said out loud — a caption that disagrees
    # with the voice is worse than no caption. The panel grows instead, and a
    # line that needs more than four is a line to shorten in the YAML.
    lines = textwrap.wrap(text, width=wrap)
    if len(lines) > 4:
        print(f"  ⚠️  caption wraps to {len(lines)} lines — shorten it: {text[:60]}…")
    lh, pad = 46, 26
    w = int(max(d.textlength(ln, font=f) for ln in lines)) + pad * 2
    h = lh * len(lines) + pad * 2 - 8
    y0 = CANVAS[1] - 72 - h
    _panel(d, (x0, y0, x0 + w, y0 + h))
    for i, ln in enumerate(lines):
        d.text((x0 + pad, y0 + pad - 6 + i * lh), ln, font=f, fill=INK)
    img.save(dest)
    return dest


# ══════════════════════════════════════════════════════════════════════════
# 5b. freeze-padding and captions
# ══════════════════════════════════════════════════════════════════════════
def plan_padding(clips: list[dict], beats: list[dict],
                 total_s: float) -> tuple[list[dict], list[float], float]:
    """
    Work out how much of each beat's final frame has to be HELD for its
    narration to finish, and where every beat then starts.

    ⚠️ WHY THIS EXISTS WHEN PASS A ALREADY PREVENTS OVERRUNS. Pass A cuts the
    UI to the narration at RECORD time, so a fresh render needs no padding at
    all and this is a no-op. It earns its place in the three cases where the
    audio and the screencast were not made together:

      · a LANGUAGE CUT — §6.1 of the plan. One screencast, four languages, and
        Tamil does not take as long as English. Every beat already ends on a
        static hold, so extending it duplicates identical frames and is
        invisible. This is what makes "record once, cut N times" possible, and
        it is only possible because `beats.json` gives the segment boundaries.
      · `--skip-record` after a script edit that made a line longer.
      · any residual drift between the measured audio and the real browser.

    Returns (segments, new beat start times, new total). A segment is
    `{start, end, pad}` in the ORIGINAL timeline; the lead-in before the first
    beat is segment zero and is never padded.
    """
    at = [b["t_ms"] / 1000.0 for b in beats]
    bounds = [0.0] + at + [total_s]
    segs: list[dict] = []
    for i in range(len(bounds) - 1):
        start, end = bounds[i], bounds[i + 1]
        pad = 0.0
        if i > 0:                       # segment i covers beat i-1
            need = clips[i - 1]["dur"] + BREATH_S if i - 1 < len(clips) else 0.0
            pad = max(0.0, need - (end - start))
        segs.append({"start": start, "end": end, "pad": round(pad, 3)})

    new_at: list[float] = []
    t = 0.0
    for i, seg in enumerate(segs):
        if i:
            new_at.append(round(t, 3))
        t += (seg["end"] - seg["start"]) + seg["pad"]
    return segs, new_at, round(t, 3)


def _vtt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"


def write_vtt(placed: list[dict], total_s: float, dest: pathlib.Path) -> pathlib.Path:
    """
    WebVTT from the MEASURED beat times.

    ⚠️ `training_assets.captions_uri` has been a column since slice 10b and has
    never been filled, because the videos did not exist. It costs nothing here:
    the cue times are the beat times the compositor already uses, so the
    captions cannot disagree with the burned-in ones or with the voice.
    """
    lines = ["WEBVTT", ""]
    for i, c in enumerate(placed):
        end = placed[i + 1]["start"] if i + 1 < len(placed) else total_s
        lines += [f"{i + 1}",
                  f"{_vtt_time(c['start'])} --> {_vtt_time(max(c['start'] + 0.6, end - 0.15))}",
                  c["text"], ""]
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


# ══════════════════════════════════════════════════════════════════════════
# 6. composite
# ══════════════════════════════════════════════════════════════════════════
def composite(screencast: pathlib.Path, avatar: pathlib.Path, alpha: bool,
              mask: pathlib.Path, watermark: pathlib.Path,
              captions: list[tuple[pathlib.Path, float, float]],
              corner: str, segments: list[dict], total_s: float,
              dest: pathlib.Path) -> None:
    """
    One graph, two entry points for the avatar:

      · `alpha=True`  — the clip carries its own alpha (a real HeyGen
        transparent render, or a build of ffmpeg that can encode yuva420p).
      · `alpha=False` — the clip is opaque and a greyscale mask supplies the
        alpha through `alphamerge`. This is ALSO the path a green-screen
        delivery takes (`colorkey` → `alphamerge`), so it is not a downgrade —
        it is the branch most real footage will use.
    """
    args = ["-i", str(screencast), "-i", str(avatar), "-i", str(watermark),
            "-i", str(mask)]
    for png, _, _ in captions:
        args += ["-i", str(png)]

    # The screencast, optionally freeze-padded per beat, then scaled once. The
    # padding is done INSIDE this graph rather than as an intermediate file:
    # a separate pass would mean two generation losses for a hold that is, by
    # construction, the same frame repeated.
    pads = sum(sg["pad"] for sg in segments)
    if pads < 0.05:
        chain = [f"[0:v]scale={CANVAS[0]}:{CANVAS[1]}:flags=lanczos,"
                 f"fps={FPS},format=rgba[bg]"]
    else:
        n = len(segments)
        chain = [f"[0:v]split={n}" + "".join(f"[q{i}]" for i in range(n))]
        for i, sg in enumerate(segments):
            tail = (f",tpad=stop_mode=clone:stop_duration={sg['pad']:.3f}"
                    if sg["pad"] > 0.001 else "")
            chain.append(f"[q{i}]trim=start={sg['start']:.3f}:end={sg['end']:.3f},"
                         f"setpts=PTS-STARTPTS{tail}[t{i}]")
        chain.append("".join(f"[t{i}]" for i in range(n))
                     + f"concat=n={n}:v=1:a=0,"
                       f"scale={CANVAS[0]}:{CANVAS[1]}:flags=lanczos,"
                       f"fps={FPS},format=rgba[bg]")
    if alpha:
        chain.append(f"[1:v]scale={AVATAR_PX}:{AVATAR_PX},format=rgba[av]")
    else:
        chain += [
            f"[1:v]scale={AVATAR_PX}:{AVATAR_PX},format=rgba[avc]",
            f"[3:v]scale={AVATAR_PX}:{AVATAR_PX},format=gray[avm]",
            "[avc][avm]alphamerge[av]",
        ]
    chain += [
        f"[bg][av]overlay={CORNERS[corner][0]}:{CORNERS[corner][1]}:format=auto[v0]",
        "[v0][2:v]overlay=0:0[v1]",
    ]
    prev = "v1"
    for n, (_, t0, t1) in enumerate(captions):
        nxt = f"c{n}"
        # ⚠️ commas inside a filter option must be escaped — the filtergraph
        # parser reads a bare comma as "next filter in the chain".
        chain.append(f"[{prev}][{n + 4}:v]overlay=0:0:"
                     f"enable=between(t\\,{t0:.3f}\\,{t1:.3f})[{nxt}]")
        prev = nxt
    chain.append(f"[{prev}]format=yuv420p[v]")

    ffmpeg(args + [
        "-filter_complex", ";".join(chain),
        "-map", "[v]", "-map", "1:a",
        "-t", f"{total_s:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-profile:v", "high", "-level", "4.1",
        "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart",
        str(dest),
    ])


# ══════════════════════════════════════════════════════════════════════════
# 6b. the manifest (P12-5)
# ══════════════════════════════════════════════════════════════════════════
def write_manifest(doc: dict, script_path: pathlib.Path, payload: dict,
                   dataset: str, beats: dict, placed: list[dict],
                   video: pathlib.Path, captions: pathlib.Path,
                   total_s: float, alpha: bool, live: bool,
                   dest: pathlib.Path) -> pathlib.Path:
    """
    ⚠️ `script_sha256` IS THE FIELD THIS FILE EXISTS FOR. Ruling Q4 says a
    module's `training_modules.version` is bumped when the NARRATION or the
    demonstrated process changes and NOT for a cosmetic re-render — so
    something has to be able to answer "did the script change?" a year later
    without re-deriving it from a script that has since changed again. That is
    this number, and the batch runner uses the same one to decide what to
    re-render.

    Everything else is the same instinct as `ai_traces` (P11-1): a render that
    somebody has to explain later should not need archaeology. A file rather
    than a table for now — P11-3's lesson is that observability must never be
    able to break the thing it observes, and a batch job has no home in the
    database yet.
    """
    manifest = {
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "tutorial_id": doc["tutorial_id"],
        "title": doc["title"],
        "role": doc["role"],
        "hub_role": doc["hub_role"],
        "language": doc["language"],
        "training_module_key": doc.get("training_module_key"),
        # ── the Q4 key ──────────────────────────────────────────────────────
        "script_path": str(script_path.relative_to(ROOT)),
        "script_sha256": sha256_file(script_path),
        "narration_sha256": sha256_text(
            "\n".join(" ".join(l["say"].split()) for l in doc["narration"])),
        # ── what crossed the boundary, and what did not ────────────────────
        "heygen": {
            "called": live,
            "endpoint": HEYGEN_URL,
            "payload_sha256": sha256_text(
                json.dumps(payload, sort_keys=True, ensure_ascii=False)),
            "lines_sent": len(doc["narration"]),
            "reason": "sent" if live else "no HEYGEN_API_KEY and no --live",
        },
        "dataset": {
            "name": dataset,
            "version": dataset_version(),
            "synthetic": dataset == "tutorial",
        },
        "git": git_state(),
        "routes": {
            "declared": list(doc.get("routes") or []),
            "visited": beats.get("visited", []),
        },
        "video": {
            "path": str(video.relative_to(ROOT)),
            "duration_s": round(total_s, 3),
            "width": CANVAS[0], "height": CANVAS[1], "fps": FPS,
            "bytes": video.stat().st_size,
            "sha256": sha256_file(video),
            "avatar_alpha": alpha,
        },
        "captions": {"path": str(captions.relative_to(ROOT)), "format": "WebVTT"},
        "beats": [{"id": c["beat"], "start_s": round(c["start"], 3),
                   "narration_s": round(c["dur"], 3), "text": c["text"]}
                  for c in placed],
    }
    dest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return dest


def read_manifest(out: pathlib.Path, tutorial_id: str) -> dict | None:
    path = out / f"{tutorial_id}.manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a corrupt manifest means "re-render"
        return None


# ══════════════════════════════════════════════════════════════════════════
# 7. main
# ══════════════════════════════════════════════════════════════════════════
def render(a, script_path: pathlib.Path, access: dict) -> int:
    """Render ONE tutorial. Returns a process exit code."""
    doc = load_script(script_path)
    work = a.out / doc["tutorial_id"]
    work.mkdir(parents=True, exist_ok=True)
    print(f"\n═══ {doc['title']} · {doc['hub_role']} · {doc['language']} ═══")
    print(f"    script  {script_path.relative_to(ROOT)}")
    print(f"    work    {work.relative_to(ROOT)}")
    print(f"    dataset {a.dataset} (v{dataset_version()})")

    # ── 1. rule 14, before a browser exists ──────────────────────────────
    print("\n[1/6] rule-14 route lint · rule-9 manual fence")
    problems = (route_lint(doc, access) + manual_fence_lint(doc)
                + redaction_lint(doc))
    for line in problems:
        print(f"      {line}")
    if any(x.startswith(("REFUSED", "FENCED", "nav_access")) for x in problems):
        print("\n🛑 REFUSED (rule 14) — a tutorial must never show a page its "
              "role cannot open.")
        return 3
    if not problems:
        print(f"      ✅ {len(doc.get('routes') or [])} declared route(s) open to "
              f"{doc['hub_role']}; narration cites "
              f"{', '.join('§' + str(n) for n in doc.get('manual_sections') or [])}"
              f" — all inside its manual allowlist")

    # ── 2. the egress boundary ───────────────────────────────────────────
    print("\n[2/6] HeyGen payload — the only thing that would leave this machine")
    payload, allowed = heygen_payload(doc)
    try:
        assert_text_only(payload, allowed)
    except EgressRefused as e:
        print(f"\n🛑 EGRESS REFUSED (P12-1)\n   {e}")
        return 4
    print(f"      ✅ egress guard: every free-text field traces to a reviewed "
          f"line in {script_path.name} ({len(allowed)} whitelisted strings)")
    if not a.quiet:
        print(f"      ── the exact JSON that would be POSTed to {HEYGEN_URL} ──")
        for ln in json.dumps(payload, indent=2, ensure_ascii=False).splitlines():
            print("      " + ln)

    key = os.environ.get("HEYGEN_API_KEY", "").strip()
    if a.live and not key:
        sys.exit("FATAL: --live needs HEYGEN_API_KEY in the environment")
    live = bool(a.live and key)
    if live:
        print("\n      ⚠️  --live: calling HeyGen for real (UNVERIFIED PATH)")
        print(json.dumps(heygen_live(payload, key), indent=2))
        sys.exit("STOP: the poll-and-download half is not implemented — see "
                 "PROPOSED_PHASE12_PLAN.md §4.4")
    print("\n      ⛔ NOT SENT — no HEYGEN_API_KEY and no --live. "
          "Synthesising the avatar locally instead.")

    # ── 3. PASS A: narration, measured ───────────────────────────────────
    print("\n[3/6] pass A — rendering the narration so the UI can be cut to it")
    clips = synthesize(doc, work, a.voice)
    holds = holds_from(clips)
    spoken = sum(c["dur"] for c in clips)
    print(f"      {len(clips)} lines, {spoken:.1f}s of speech "
          f"({'measured' if clips[0]['wav'] else 'ESTIMATED — no `say` here'})")
    think_ms = max(int(doc.get("assistant_think_ms", 1400)),
                   holds.get("thinking", 0))
    shot = shot_list(doc, holds, think_ms)

    # ── 4. PASS B: the screencast ────────────────────────────────────────
    if a.skip_record:
        beats = json.loads((work / "beats.json").read_text(encoding="utf-8"))
        screencast = pathlib.Path(beats["video"])
        print(f"\n[4/6] --skip-record: reusing {screencast.name}")
    else:
        db = DATASET_ENV.get(a.dataset, {})
        print(f"\n[4/6] pass B — recording against "
              f"{db.get('E2E_DB', 'gihub_e2e_pw')} on "
              f":{db.get('E2E_API_PORT', '8010')}/:{db.get('E2E_WEB_PORT', '5183')}")
        try:
            screencast, beats = record(shot, work, a.reuse_stack,
                                       _dataset_env(a.dataset), a.keep_stack)
        except RecordingFailed as e:
            print(f"\n🛑 RECORDING FAILED — {e}")
            return 7

    raw_total = probe_seconds(screencast)
    last_beat = max(b["t_ms"] for b in beats["beats"]) / 1000.0
    print(f"      {screencast.name}: {hms(raw_total)}, "
          f"{len(beats['beats'])} beats, last at {last_beat:.2f}s")
    if last_beat > raw_total + 0.5:
        # Asserted rather than trusted: a beat past the end of the video means
        # t0 and the recording start disagree, and every narration line is then
        # misplaced by the same amount.
        print(f"FATAL: last beat {last_beat:.2f}s is past the video's "
              f"{raw_total:.2f}s — beat t0 and the recording start disagree")
        return 5

    # ── 4b. the rule-14 ORACLE ───────────────────────────────────────────
    strays = check_visited(doc, beats)
    if strays:
        print("\n🛑 REFUSED (rule 14, ground truth)")
        for line in strays:
            print(f"   {line}")
        return 3
    print(f"      ✅ visited {len(dict.fromkeys(beats.get('visited', [])))} "
          f"path(s), all declared")

    # ── 5. freeze-padding, narration, avatar, overlays ───────────────────
    print("\n[5/6] placing the narration and building the stand-in avatar")
    segments, new_at, total_s = plan_padding(clips, beats["beats"], raw_total)
    pad_total = sum(sg["pad"] for sg in segments)
    if pad_total >= 0.05:
        print(f"      freeze-padding {pad_total:.2f}s across "
              f"{sum(1 for sg in segments if sg['pad'] > 0.001)} beat(s) — "
              f"{hms(raw_total)} → {hms(total_s)}")
    else:
        print("      no freeze-padding needed (pass A already cut the UI to "
              "the narration)")
    times = {b["id"]: t for b, t in zip(beats["beats"], new_at)}
    narration, placed = place_narration(clips, times, total_s, work)
    avatar, alpha = build_avatar_clip(doc, narration, total_s, work)
    print(f"      avatar: {avatar.name} "
          f"({'VP9 with alpha' if alpha else 'opaque H.264 + alphamerge mask'})")

    ov = work / "overlays"
    ov.mkdir(exist_ok=True)
    mask = avatar_mask(ov / "avatar_mask.png")
    wm = watermark_png(doc, live, ov / "watermark.png")
    corner = (doc.get("avatar") or {}).get("corner", "bottom-left")
    if corner not in CORNERS:
        print(f"FATAL: avatar.corner must be one of {', '.join(CORNERS)}")
        return 6
    cap_x = 56 + AVATAR_PX + 40 if corner == "bottom-left" else 64
    cap_wrap = 52 if corner == "bottom-left" else 64
    captions: list[tuple[pathlib.Path, float, float]] = []
    for i, c in enumerate(placed):
        end_t = placed[i + 1]["start"] if i + 1 < len(placed) else total_s
        captions.append((caption_png(c["text"], cap_x, cap_wrap, ov / f"cap{i:02d}.png"),
                         c["start"], max(c["start"] + 0.6, end_t - 0.15)))
    print(f"      {len(captions)} beat-aligned captions + 1 watermark, avatar "
          f"{corner} (Pillow — this ffmpeg has no drawtext)")

    # ── 6. composite, captions file, manifest ────────────────────────────
    print("\n[6/6] compositing")
    final = a.out / f"{doc['tutorial_id']}.mp4"
    t0 = time.time()
    composite(screencast, avatar, alpha, mask, wm, captions, corner,
              segments, total_s, final)
    vtt = write_vtt(placed, total_s, a.out / f"{doc['tutorial_id']}.vtt")
    manifest = write_manifest(doc, script_path, payload, a.dataset, beats,
                              placed, final, vtt, total_s, alpha, live,
                              a.out / f"{doc['tutorial_id']}.manifest.json")

    print(f"\n✅ {final.relative_to(ROOT)}")
    print(f"   {hms(probe_seconds(final))} · {final.stat().st_size / 1e6:.1f} MB · "
          f"{CANVAS[0]}x{CANVAS[1]} · encoded in {time.time() - t0:.1f}s")
    print(f"   {vtt.relative_to(ROOT)} · {manifest.relative_to(ROOT)}")
    print(f"   script_sha256 {sha256_file(script_path)[:16]}…  (ruling Q4: the "
          f"version bumps when THIS changes)")
    print("\n   To publish it into the training hub Phase 10 already built:")
    print(f"     POST /training/assets  {{\"module_key\": "
          f"\"{doc.get('training_module_key', '?')}\", \"language\": "
          f"\"{doc['language']}\", \"storage_uri\": \"<object-store URL>\", "
          f"\"captions_uri\": \"<…>.vtt\", \"duration_s\": {int(total_s)}}}")
    print("   (admin-only; the player says \"not published yet\" until a row "
          "exists)\n")
    return 0


# ══════════════════════════════════════════════════════════════════════════
# 8. the batch runner
# ══════════════════════════════════════════════════════════════════════════
def why_render(doc: dict, script_path: pathlib.Path, out: pathlib.Path,
               force: bool, stale: bool) -> str | None:
    """
    Make-style: say WHY a tutorial needs rendering, or None to skip it.

    ⚠️ A UI CHANGE IS NOT A REASON, AND THAT IS RULING Q4. Re-rendering because
    a commit landed would, under a naive publish step, bump every module's
    version and un-certify the whole workforce for a CSS tweak. So the default
    triggers are the script's own hash, the dataset version and the pipeline
    version — all three of which mean the OUTPUT would genuinely differ.
    A tutorial recorded from an older commit is reported as `stale` and
    re-rendered only when asked, because "the footage is a bit old" and "the
    video is wrong" are different problems with different costs.
    """
    m = read_manifest(out, doc["tutorial_id"])
    if force:
        return "forced"
    if m is None:
        return "no manifest"
    if not (out / f"{doc['tutorial_id']}.mp4").exists():
        return "video missing"
    if m.get("script_sha256") != sha256_file(script_path):
        return "script changed"
    if m.get("pipeline_version") != PIPELINE_VERSION:
        return f"pipeline {m.get('pipeline_version')} → {PIPELINE_VERSION}"
    if (m.get("dataset") or {}).get("version") != dataset_version():
        return "dataset changed"
    if stale and (m.get("git") or {}).get("sha") != git_state()["sha"]:
        return "stale (recorded from an older commit)"
    return None


def batch(a, access: dict) -> int:
    scripts = sorted(SCRIPT_DIR.glob("*.yaml"))
    if not scripts:
        print(f"no tutorial scripts in {SCRIPT_DIR.relative_to(ROOT)}")
        return 0
    print(f"\n═══ BATCH — {len(scripts)} script(s) in "
          f"{SCRIPT_DIR.relative_to(ROOT)} ═══\n")

    plan: list[tuple[pathlib.Path, dict, str]] = []
    blocked = 0
    for sp in scripts:
        try:
            doc = load_script(sp)
        except SystemExit as e:
            print(f"  ❌ {sp.name}: {e}")
            blocked += 1
            continue
        problems = (route_lint(doc, access) + manual_fence_lint(doc)
                    + redaction_lint(doc))
        refused = [x for x in problems if x.startswith(("REFUSED", "FENCED"))]
        reason = why_render(doc, sp, a.out, a.force, a.stale)
        mark = "RENDER" if reason else "skip  "
        if refused:
            mark, blocked = "REFUSE", blocked + 1
        print(f"  {mark}  {sp.name:<44} {reason or 'up to date'}")
        for x in problems:
            print(f"          ⚠️  {x}")
        if reason and not refused:
            plan.append((sp, doc, reason))

    print(f"\n  {len(plan)} to render · {len(scripts) - len(plan) - blocked} "
          f"up to date · {blocked} blocked")
    if a.dry_run:
        # ⚠️ A DRY RUN TOUCHES NOTHING: no dataset build, no browser, no HeyGen,
        # no ffmpeg. It is the thing somebody runs before a long batch, and one
        # that quietly rebuilt a fixture would not be that thing.
        print("  --dry-run: nothing was recorded, built, sent or encoded.\n")
        return 1 if blocked else 0

    # ⚠️ THE STACK IS RAISED ONCE AND DROPPED ONCE. `cutover_migrate.py --wipe`
    # plus uvicorn plus Vite is ~28 s; paying it per video would be most of a
    # sixty-clip batch. The first render raises it and every render KEEPS it;
    # the `finally` drops it exactly once, including when a render throws — a
    # batch that leaks a database and two ports is a batch that breaks the next
    # person's E2E run, and they would have no idea why.
    failed = 0
    kept = False
    try:
        for sp, _doc, _reason in plan:
            a.keep_stack = True
            rc = render(a, sp, access)
            kept = not a.skip_record
            a.reuse_stack = True
            if rc:
                failed += 1
                print(f"  ❌ {sp.name} failed with exit {rc}")
    finally:
        if kept:
            teardown_stack(a)
    print(f"\n═══ BATCH DONE — {len(plan) - failed} rendered, {failed} failed, "
          f"{blocked} blocked ═══\n")
    return 1 if (failed or blocked) else 0


def teardown_stack(a) -> None:
    """
    Drop the stack the batch kept up — through Playwright's OWN teardown, not a
    copy of it.

    ⚠️ The trick is the two flags pulling in opposite directions: REUSE=1 makes
    `stack.ts` skip global-setup, KEEP unset makes it run global-teardown, and
    a grep that matches no test means nothing is recorded in between. Writing a
    Python kill-the-pids-and-drop-the-database function instead would be a
    second teardown to keep in step with the first, and the one that rots is
    always the copy.
    """
    print("\n[batch] dropping the shared stack …")
    env = {**os.environ, **DATASET_ENV.get(a.dataset, {}),
           "GI_VIDEO_REUSE_STACK": "1"}
    env.pop("GI_VIDEO_KEEP_STACK", None)
    # Output is captured: the grep matches nothing, so Playwright prints
    # "Error: No tests found" and exits 1. That is the mechanism working, and
    # printing it in the middle of a batch log makes a clean run look broken.
    subprocess.run(
        ["npx", "playwright", "test", "-c", "../video_gen/playwright.config.ts",
         "--grep", "__no_test_matches_this__"],
        cwd=E2E, env=env, check=False, capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", type=pathlib.Path, default=DEFAULT_SCRIPT)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--all", action="store_true",
                    help=f"batch: every *.yaml in {SCRIPT_DIR.name}/")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --all: print the plan and touch nothing")
    ap.add_argument("--force", action="store_true",
                    help="with --all: re-render everything")
    ap.add_argument("--stale", action="store_true",
                    help="with --all: also re-render anything recorded from an "
                         "older commit")
    ap.add_argument("--skip-record", action="store_true",
                    help="re-composite from the last screencast (no browser)")
    ap.add_argument("--reuse-stack", action="store_true",
                    help="attach to an already-running stack")
    ap.add_argument("--keep-stack", action="store_true",
                    help="leave the stack up after this render")
    ap.add_argument("--dataset", choices=("tutorial", "e2e"), default="tutorial",
                    help="tutorial = the synthetic dataset (P12-0, the only one "
                         "a published video may use); e2e = the gate's clone of "
                         "the REAL database, diagnostic only")
    ap.add_argument("--voice", default=None, help="macOS `say` voice for the mock VO")
    ap.add_argument("--quiet", action="store_true",
                    help="do not print the full HeyGen payload")
    ap.add_argument("--live", action="store_true",
                    help="actually call HeyGen (needs HEYGEN_API_KEY; UNVERIFIED)")
    a = ap.parse_args()
    # A relative --script is resolved before anything tries to make it relative
    # to the repo root again.
    a.script = a.script.resolve()
    a.out = a.out.resolve()

    a.out.mkdir(parents=True, exist_ok=True)
    access = nav_access()
    if a.all:
        return batch(a, access)
    if a.dry_run:
        doc = load_script(a.script)
        problems = (route_lint(doc, access) + manual_fence_lint(doc)
                    + redaction_lint(doc))
        reason = why_render(doc, a.script, a.out, a.force, a.stale)
        print(f"  {'RENDER' if reason else 'skip  '}  {a.script.name}  "
              f"{reason or 'up to date'}")
        for x in problems:
            print(f"          ⚠️  {x}")
        print("  --dry-run: nothing was recorded, built, sent or encoded.\n")
        return 1 if any(x.startswith(("REFUSED", "FENCED")) for x in problems) else 0
    return render(a, a.script, access)


if __name__ == "__main__":
    sys.exit(main())

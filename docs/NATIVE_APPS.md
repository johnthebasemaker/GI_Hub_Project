# Native Apps — build & distribution guide

GI Hub ships as **one web codebase** (`frontend/`) wrapped three ways:

| Target | Wrapper | Output | Status |
|---|---|---|---|
| Android | Capacitor | `.apk` / `.aab` | scaffolded — build locally (below) |
| iOS | Capacitor | `.ipa` (Xcode) | scaffolded — build locally (below) |
| Windows / macOS | Tauri v2 | `.exe` / `.dmg` | foundation documented (below) — init requires the Rust toolchain, which is not installed on this Mac, so the Tauri steps are **not yet build-verified** |
| Any browser / phone | PWA | installable, offline | LIVE today (USER_MANUAL §1.2) |

Everything below runs from `frontend/` unless noted.

## 1. Android (.apk)

Prerequisites (one-time): [Android Studio](https://developer.android.com/studio)
with an SDK + JDK 17 (Studio bundles both).

```bash
npm install                    # brings @capacitor/{core,cli,android,ios}
npx cap add android            # one-time: generates the android/ project (gitignored)
npm run cap:sync               # vite build + copy dist/ into the native shell
cd android && ./gradlew assembleDebug         # debug APK, installable immediately
# → android/app/build/outputs/apk/debug/app-debug.apk
```

Signed release for distribution: `./gradlew assembleRelease` after configuring
a keystore in `android/app/build.gradle` (Android Studio → Build → Generate
Signed App Bundle walks you through it).

## 2. iOS

Prerequisites: Xcode + an Apple Developer account (free account = 7-day
sideload; paid = TestFlight/App Store).

```bash
npx cap add ios                # one-time: generates the ios/ project (gitignored)
npm run cap:ios                # build + sync + open in Xcode → Product ▸ Archive
```

## 3. Windows (.exe) / macOS (.dmg) — Tauri v2 foundation

Tauri compiles a tiny Rust shell around the same `dist/` build — outputs are
~10 MB installers. **Requires the Rust toolchain** (`rustup`), which is not
installed on the dev Mac yet — steps below are the documented foundation and
have not been build-verified here:

```bash
# one-time
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # Rust
npm install -D @tauri-apps/cli
npx tauri init      # answers: app name "GI Hub" · window title "GI Hub"
                    #          frontend dist ../dist · dev server http://localhost:5173
# every build
npm run build && npx tauri build
# → src-tauri/target/release/bundle/ (msi/exe on Windows, dmg/app on macOS)
```

`npx tauri init` generates `src-tauri/` (committed once created; its `target/`
build dir is gitignored). Windows installers must be built ON Windows, dmg ON
macOS — Tauri does not cross-compile.

## 4. OTA updates — how deployed changes reach users

- **PWA + browser users:** the service worker is built with
  `registerType: 'autoUpdate'` and `src/main.tsx` polls for a new deployment
  every 15 minutes **and** on every tab refocus — users get the new version
  automatically, no manual refresh required.
- **Capacitor apps (bundled mode, default):** the shell carries its own copy
  of `dist/` — rebuild + reinstall to update.
- **Capacitor apps (live-shell mode):** uncomment the `server.url` block in
  `frontend/capacitor.config.ts` to make the native app load the hosted
  portal (`https://gi.giinventory.com`) instead of the bundled copy — then
  the PWA auto-update path above applies to the native apps too, and no
  reinstall is ever needed. Recommended once the Hetzner deployment is live.

## 5. Hosting the installers

After the Hetzner deployment, drop the built artifacts in the repo-root
`downloads/` directory the API serves (or any static path behind the tunnel)
and link them from USER_MANUAL §1.2 — the manual already carries the
placeholder links (`/downloads/gi-hub.apk`, `/downloads/GI-Hub-Setup.exe`,
`/downloads/GI-Hub.dmg`).

## 6. Sanity gates

The wrappers never fork the web code: `npm run build && npx tsc --noEmit`
stays the frontend gate, and `tools/diagnose_sync.py` (docs/DEBUGGING.md)
verifies the deployed sync chain the apps rely on.

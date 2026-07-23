import type { CapacitorConfig } from '@capacitor/cli'

// Capacitor wrapper config — packages the built PWA (dist/) as native
// Android (.apk / .aab) and iOS apps. Full build commands + prerequisites:
// docs/NATIVE_APPS.md (repo) and USER_MANUAL §1.2.
//
// OTA strategy: by default the app ships the bundled dist/ and updates when
// you rebuild. For true over-the-air updates WITHOUT store rebuilds, point
// the shell at the hosted portal instead (uncomment `server`) — the app then
// always loads the live site and the PWA service worker keeps it fresh and
// offline-capable (strict 15-min update polling is wired in src/main.tsx).
const config: CapacitorConfig = {
  appId: 'com.giinventory.hub',
  appName: 'GI Hub',
  webDir: 'dist',
  // server: {
  //   url: 'https://gi.giinventory.com',
  //   cleartext: false,
  // },
  android: {
    allowMixedContent: false,
  },
}

export default config

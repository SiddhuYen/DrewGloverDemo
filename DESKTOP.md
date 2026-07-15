# Warm-Intro Pathfinder — desktop app

A downloadable Mac (`.dmg`) and Windows (`.zip`) build that wraps the FastAPI
pathfinder in a native window. It ships with a pre-built graph so it opens warm,
and **live search** works: type a person who isn't in the graph yet and it pulls
structured sources on demand.

## Using it

1. **Mac:** open `WarmIntroPathfinder.dmg`, drag the app out, open it.
   **Windows:** unzip `WarmIntroPathfinder-windows.zip`, run `WarmIntroPathfinder.exe`.
2. Type a name (e.g. *Sheel Mohnot*) → **Find path**.
3. **Live search:** click **⚙ → Save** with your own [Serper](https://serper.dev)
   API key to search for people not yet in the graph. The bundled graph works
   without a key.

State lives in a per-user data dir (auto-created):
`graph.db` (grows as you search), `cache.db`, `settings.json` (your key).

> The apps are **unsigned**. Mac: right-click → Open (once) to bypass Gatekeeper.
> Windows: "More info → Run anyway" past SmartScreen. Proper signing/notarization
> needs an Apple Developer + Windows code-signing cert.

## Building

- **Mac (local):** `bash build/build_mac.sh` → `dist/WarmIntroPathfinder.{app,dmg}`
- **Both, via CI:** the `build-desktop` GitHub Actions workflow builds macOS and
  Windows on their own runners (a Mac can't build a Windows binary). Trigger it
  from the Actions tab (*Run workflow*), or push a `v*` tag to also publish the
  installers to a GitHub Release.

The build (`build/pathfinder.spec`) bundles Python, the app, the spaCy model, and
`resources/graph.db`. It **excludes** Playwright/Chromium — the browser-render
fallback (some firm rosters) silently degrades; everything else works.

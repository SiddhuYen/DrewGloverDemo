# Warm-Intro Pathfinder — desktop app

A downloadable Mac (`.dmg`) and Windows (`.zip`) build that wraps the FastAPI
pathfinder in a native window. It ships with a pre-built graph so it opens warm,
and **live search** works: type a person who isn't in the graph yet and it pulls
structured sources on demand.

## Using it

1. **Mac:** open `WarmIntroPathfinder.dmg`, drag the app out, open it.
   **Windows:** unzip `WarmIntroPathfinder-windows.zip`, run `WarmIntroPathfinder.exe`.
2. Type a name (e.g. *Sheel Mohnot*) → **Find path**.
3. **Live search** (people not yet in the bundled graph) works out of the box
   if this build has a [Serper](https://serper.dev) key baked in — see
   *Baked-in API keys* below. If not, click **⚙ → Save** with your own key.
   The bundled graph works either way, with no key at all.

State lives in a per-user data dir (auto-created):
`graph.db` (grows as you search), `cache.db`, `settings.json` (a key you paste
in yourself, which always overrides whatever's baked in).

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

## Baked-in API keys

Two keys can be baked into the build at CI time from repo secrets, so the
person running the app never has to find, paste, or even know about either
one. Same mechanism for both: a GitHub Actions secret → a
`resources/*_key.txt` file written during the build (never committed — see
`.gitignore`) → bundled by PyInstaller → read once at startup by
`desktop/main.py`. Neither secret is required — a build with nothing set
still succeeds; each feature just stays in its normal no-key state.

**Serper** (live search for people not yet in the bundled graph):

1. Create a key at [serper.dev](https://serper.dev).
2. Add it as the `SERPER_API_KEY` repository secret (Settings → Secrets and
   variables → Actions).
3. That's it — the next build bakes it in.

Unlike Claude below, Serper still has its own Settings-UI paste box
(`settings.json`), and a key entered there always wins over the baked-in one
— so pasting a personal key still works exactly as before, it just isn't
required anymore.

**Claude** (relationship-strength classification for deep search's co-mention
edges, see `app/providers/llm_classify.py` — off by default, since
`CO_MENTION_ENABLED` and `DEEP_SEARCH` both default off, and a no-op even when
on unless a key is configured):

The key is a real Anthropic key, **spend-capped in the Anthropic Console**
(set a dollar limit on this specific key) rather than routed through a
proxy — one trusted user, so a bounded worst case beats running extra
infrastructure.

1. Create an API key in the Anthropic Console and set a spend limit on it.
2. Add it as the `CLAUDE_API_KEY` repository secret (Settings → Secrets and
   variables → Actions).
3. That's it — the next build bakes it in.

For local development, export either `SERPER_API_KEY` or `CLAUDE_API_KEY` in
your own shell — a real env var always wins over a baked-in file.

# Claude API access for a shipped desktop build

Drew's Mac/Windows build of Artemis never gets the real Anthropic API key.
Instead:

```
Drew's app  --(low-stakes virtual key)-->  your LiteLLM proxy  --(real key)-->  Anthropic
```

The real key lives only in the proxy's environment (`ANTHROPIC_API_KEY`,
set as a Fly secret / env var on whatever host runs the proxy — never in
anything you hand to Drew). The app ships with a *virtual key* the proxy
issued, scoped to a spend cap. If that leaks, you revoke it and nothing else
is affected.

## One-time setup

1. Run the proxy somewhere always-on (`litellm_config.yaml` + `fly.toml` in
   this directory — or any host, LiteLLM doesn't require Fly specifically).
2. Set `ANTHROPIC_API_KEY` (real key) and `LITELLM_MASTER_KEY` (a key only
   you use to administer the proxy) as secrets on that host.
3. Generate the one virtual key Drew's build will use — see the `curl`
   example at the top of `litellm_config.yaml`.

## What the app itself needs

The desktop build bakes the virtual key in at CI build time — Drew never
sees or enters anything. Set two **repository secrets** (Settings → Secrets
and variables → Actions) in this GitHub repo:

| Secret | Value |
|---|---|
| `CLAUDE_API_BASE` | Your proxy's URL (e.g. `https://artemis-claude-proxy.fly.dev`) |
| `LITELLM_VIRTUAL_KEY` | The virtual key from step 3 above |

**These are deliberately different variable names from `CLAUDE_API_KEY`.**
`CLAUDE_API_KEY` (in `app/config.py`) means "a real Anthropic key, used only
when `CLAUDE_API_BASE` is unset" — local dev talking to Anthropic directly.
`LITELLM_VIRTUAL_KEY` means "the proxy-issued key, used only when
`CLAUDE_API_BASE` **is** set." The app never falls back from one to the
other, so a real key sitting unused in the environment can never
accidentally get sent somewhere that isn't actually Anthropic. **Never put a
real Anthropic key in a repository secret or anywhere near the desktop
build** — only `LITELLM_VIRTUAL_KEY` belongs there.

`.github/workflows/build-desktop.yml` writes those into
`resources/litellm_key.json` during the build (never committed — see
`.gitignore`), PyInstaller bundles it into the `.app`/`.exe` like any other
resource, and `desktop/main.py` reads it once at startup, setting
`LITELLM_VIRTUAL_KEY` (never `CLAUDE_API_KEY`) in the running app's
environment. If the secret isn't set, the build still succeeds; the app
just runs with the classifier in its normal no-op state, same as any other
missing API key.

For local development: export `CLAUDE_API_KEY` (a real key) in your own
shell to talk to Anthropic directly, or export both `CLAUDE_API_BASE` and
`LITELLM_VIRTUAL_KEY` to test against the proxy exactly as the shipped app
would. A locally-exported env var always wins over the baked-in file, so
this never fights with CI.

## If this ever grows past one user

Generate one virtual key per user instead of one shared key — same
`/key/generate` call, different `key_alias`/`max_budget` per person. Each
gets independent spend tracking and revocation without anyone's access
depending on anyone else's key, and without minting additional real
Anthropic keys.

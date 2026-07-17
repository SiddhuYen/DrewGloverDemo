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

Two environment variables, read in `app/config.py`:

| Var | Value |
|---|---|
| `CLAUDE_API_BASE` | Your proxy's URL (e.g. `https://artemis-claude-proxy.fly.dev`) |
| `CLAUDE_API_KEY` | The virtual key from step 3 above — **not** the real Anthropic key |

Leave `CLAUDE_API_BASE` unset for local development (the SDK falls back to
talking to Anthropic directly with whatever key you have locally) — only the
shipped build needs it pointed at the proxy.

## If this ever grows past one user

Generate one virtual key per user instead of one shared key — same
`/key/generate` call, different `key_alias`/`max_budget` per person. Each
gets independent spend tracking and revocation without anyone's access
depending on anyone else's key, and without minting additional real
Anthropic keys.
